from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.common import VersionedModel, new_id, utcnow
from competitive_agent.storage import (
    LATEST_USER_VERSION,
    TABLES,
    Repository,
    canonical_args_hash,
    connect,
    get_user_version,
    migrate,
    register_schema,
)
from competitive_agent.tracing import REDACTED, TraceWriter


class CheckpointState(VersionedModel):
    node: str
    open_questions: list[str] = []
    budget_spent_usd: float = 0.0


class StubClaim(VersionedModel):
    claim_id: str
    company_id: str
    claim_type: str = "unknown"
    grounding_status: str = "unknown"
    text: str = ""


class StubOpportunity(VersionedModel):
    opportunity_id: str
    critic_verdict: str = "unknown"
    headline: str = ""


@pytest.fixture()
def repo(tmp_path):
    conn = connect(tmp_path / "db" / "agent.db")
    migrate(conn)
    repository = Repository(conn)
    yield repository
    conn.close()


def make_artifact(**overrides) -> RawArtifact:
    defaults = dict(
        artifact_id=new_id("art"),
        company_id="cmp-deel",
        source_type="website",
        source_name="deel.com",
        url="https://www.deel.com/pricing",
        final_url="https://www.deel.com/pricing",
        retrieved_at=utcnow(),
        raw_text="Deel launched a new EOR product.",
        normalized_text="Deel launched a new EOR product.",
        content_hash="hash-abc123",
        collection_method="http_fetch",
    )
    defaults.update(overrides)
    return RawArtifact(**defaults)


# -- migrations ---------------------------------------------------------------


def test_migrations_create_all_tables_and_set_user_version(tmp_path):
    conn = connect(tmp_path / "agent.db")
    version = migrate(conn)
    assert version == LATEST_USER_VERSION
    assert get_user_version(conn) == LATEST_USER_VERSION

    names = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert set(TABLES) <= names

    indexes = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert "idx_tool_calls_cache" in indexes
    assert "idx_artifacts_content_hash" in indexes

    # Idempotent re-run.
    assert migrate(conn) == LATEST_USER_VERSION
    conn.close()


def test_connection_pragmas(tmp_path):
    conn = connect(tmp_path / "agent.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


# -- payload round-trip -------------------------------------------------------


def test_artifact_save_load_round_trip_validates_through_registry(repo):
    run_id = repo.create_run(company="deel", mode="single_company")
    artifact = make_artifact()
    repo.save_artifact(run_id, artifact)

    loaded = repo.get_artifact(artifact.artifact_id)
    assert isinstance(loaded, RawArtifact)
    assert loaded == artifact

    by_hash = repo.find_artifact_by_hash("hash-abc123")
    assert isinstance(by_hash, RawArtifact)
    assert by_hash.artifact_id == artifact.artifact_id

    listed = repo.list_artifacts(run_id=run_id)
    assert [a.artifact_id for a in listed] == [artifact.artifact_id]
    assert repo.list_artifacts(company_id="cmp-deel")[0].artifact_id == artifact.artifact_id

    with pytest.raises(ValueError):
        repo.list_artifacts()


def test_payload_tables_round_trip(repo):
    run_id = repo.create_run(company="deel", mode="single_company")
    register_schema(StubClaim)
    register_schema(StubOpportunity)

    claim = StubClaim(
        claim_id=new_id("clm"),
        company_id="cmp-deel",
        claim_type="pricing",
        grounding_status="grounded",
        text="Deel EOR starts at $599.",
    )
    repo.save_claim(run_id, claim)
    claims = repo.list_claims(run_id)
    assert claims == [claim]
    assert repo.list_claims(run_id, grounding_status="ungrounded") == []

    opp = StubOpportunity(
        opportunity_id=new_id("opp"), critic_verdict="accepted", headline="Attack pricing opacity"
    )
    repo.save_opportunity(run_id, opp)
    assert repo.list_opportunities(run_id) == [opp]
    assert repo.list_opportunities(run_id, critic_verdict="rejected") == []

    cls_id = repo.save_classification(
        run_id, "message", claim, prompt_version="p1", model_id="claude-x"
    )
    row = repo.conn.execute("SELECT * FROM classifications WHERE id = ?", (cls_id,)).fetchone()
    assert row["family"] == "message"
    assert row["prompt_version"] == "p1"
    assert row["model_id"] == "claude-x"
    assert repo.load_payload(row) == claim

    fb_id = repo.save_feedback(
        run_id,
        target_type="opportunity",
        target_id=opp.opportunity_id,
        feedback_type="rejected",
        reason="not grounded",
        retry_mode="soft",
    )
    row = repo.conn.execute("SELECT * FROM feedback_events WHERE id = ?", (fb_id,)).fetchone()
    assert row["feedback_type"] == "rejected"


def test_non_basemodel_writes_raise_type_error(repo):
    run_id = repo.create_run(company="deel", mode="single_company")
    with pytest.raises(TypeError):
        repo.save_payload("claims", {"id": "x", "run_id": run_id}, {"not": "a model"})
    with pytest.raises(TypeError):
        repo.save_artifact(run_id, {"artifact_id": "a1"})
    with pytest.raises(TypeError):
        repo.update_run_state(run_id, state={"node": "plan"})


# -- tool-call cache ----------------------------------------------------------


def test_tool_call_cache_hits_on_same_args_hash(repo):
    run_id = repo.create_run(company="deel", mode="single_company")
    args = {"query": "deel pricing", "limit": 10}
    repo.record_tool_call(
        run_id, "act-1", "web_search", "live", args, "success", latency_ms=120, cost_usd=0.01
    )

    # Key order must not matter (canonical JSON hashing).
    hash_reordered = canonical_args_hash({"limit": 10, "query": "deel pricing"})
    hit = repo.find_cached_tool_call("web_search", hash_reordered)
    assert hit is not None
    assert hit["status"] == "success"
    assert json.loads(hit["args_json"]) == args

    assert repo.find_cached_tool_call("web_search", canonical_args_hash({"query": "other"})) is None
    assert repo.find_cached_tool_call("other_tool", hash_reordered) is None


def test_tool_call_cache_respects_max_age_and_statuses(repo):
    run_id = repo.create_run(company="deel", mode="single_company")
    args = {"url": "https://www.deel.com"}
    args_hash = canonical_args_hash(args)
    tc_id = repo.record_tool_call(run_id, "act-1", "fetch_page", "live", args, "success")

    # Fresh enough: hit.
    assert repo.find_cached_tool_call("fetch_page", args_hash, max_age_seconds=3600) is not None

    # Backdate the row two hours: stale for a one-hour window, fine without one.
    old = (utcnow() - timedelta(hours=2)).isoformat()
    repo.conn.execute("UPDATE tool_calls SET created_at = ? WHERE id = ?", (old, tc_id))
    repo.conn.commit()
    assert repo.find_cached_tool_call("fetch_page", args_hash, max_age_seconds=3600) is None
    assert repo.find_cached_tool_call("fetch_page", args_hash) is not None

    # Failed calls are never cache hits under default statuses.
    args2 = {"url": "https://www.deel.com/blog"}
    repo.record_tool_call(run_id, "act-2", "fetch_page", "live", args2, "failed_retryable")
    assert repo.find_cached_tool_call("fetch_page", canonical_args_hash(args2)) is None


# -- run state checkpointing ---------------------------------------------------


def test_run_state_checkpoint_update_and_reload(repo):
    run_id = repo.create_run(company="deel", mode="single_company", execution_mode="fixture")
    state = CheckpointState(node="collect", open_questions=["pricing?"], budget_spent_usd=0.42)
    repo.update_run_state(run_id, status="running", current_node="collect", state=state)

    row = repo.get_run(run_id)
    assert row["status"] == "running"
    assert row["current_node"] == "collect"
    assert row["execution_mode"] == "fixture"
    assert row["state_schema_version"] == CheckpointState.SCHEMA_VERSION
    assert datetime.fromisoformat(row["updated_at"]) >= datetime.fromisoformat(row["created_at"])

    reloaded = repo.load_run_state(row, CheckpointState)
    assert reloaded == state

    # Second checkpoint overwrites.
    state2 = CheckpointState(node="classify", budget_spent_usd=1.10)
    repo.update_run_state(run_id, current_node="classify", state=state2)
    assert repo.load_run_state(repo.get_run(run_id), CheckpointState) == state2

    assert repo.list_runs(company="deel")[0]["run_id"] == run_id

    with pytest.raises(LookupError):
        repo.update_run_state("run-missing", status="running")


# -- excerpt verification --------------------------------------------------------


def test_artifact_contains_uses_shared_normalization(repo):
    run_id = repo.create_run(company="deel", mode="single_company")
    artifact = make_artifact(
        normalized_text='Deel launched a new EOR product - "global payroll" included.'
    )
    repo.save_artifact(run_id, artifact)

    assert repo.artifact_contains(artifact.artifact_id, "launched   a NEW eor")
    # Smart quotes/dashes normalize to ASCII via the shared path.
    assert repo.artifact_contains(artifact.artifact_id, "product — “global payroll”")
    assert not repo.artifact_contains(artifact.artifact_id, "acquired a competitor")
    assert not repo.artifact_contains(artifact.artifact_id, "")
    assert not repo.artifact_contains("art-missing", "anything")


# -- trace writer ----------------------------------------------------------------


def test_trace_writer_appends_and_redacts(tmp_path):
    run_dir = tmp_path / "run-abc123"
    writer = TraceWriter(run_dir)
    writer.append(
        "tool_started",
        {
            "tool": "web_search",
            "api_key": "sk-SUPER-SECRET",
            "nested": {"authorization": "Bearer xyz", "query": "deel pricing"},
            "items": [{"session_token": "tok-1", "url": "https://deel.com"}],
        },
    )
    writer.append("definitely_not_a_known_event", {"ok": True})  # warns, never crashes

    lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["run_id"] == "run-abc123"
    assert first["event_type"] == "tool_started"
    assert "ts" in first
    assert first["payload"]["api_key"] == REDACTED
    assert first["payload"]["nested"]["authorization"] == REDACTED
    assert first["payload"]["items"][0]["session_token"] == REDACTED
    assert first["payload"]["nested"]["query"] == "deel pricing"
    raw = lines[0]
    assert "sk-SUPER-SECRET" not in raw
    assert "Bearer xyz" not in raw
    assert "tok-1" not in raw
