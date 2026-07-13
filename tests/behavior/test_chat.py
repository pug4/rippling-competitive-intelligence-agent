"""Grounded per-run analysis chatbot (assignment: 'Be conversational')."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


def test_build_context_digests_the_findings():
    from competitive_agent.chat import build_context

    pkg = {
        "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
        "dominant_message": {"label": "All-in-one platform", "theme": "consolidation"},
        "eval_summary": {"n_artifacts": 50, "n_proof_gaps": 5, "n_opportunities": 3},
        "proof_gaps": [
            {"short_label": "compliance", "attackability": "medium", "proof_strength": "weak"}
        ],
        "opportunities": [{"title": "Workflow demo", "message_angle": "show the flow"}],
        "linkedin_posts": [{"author": "Dana", "theme": "consolidation", "excerpt": "one platform"}],
    }
    ctx = build_context(pkg)
    assert "Deel" in ctx and "Rippling" in ctx
    assert "consolidation" in ctx and "compliance" in ctx
    assert "Workflow demo" in ctx and "Dana" in ctx


def test_chat_answers_from_run_and_suggests_followups(isolated_env: Path):
    from competitive_agent.chat import chat_about_run
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    res = asyncio.run(
        chat_about_run(state.run_id, "What's their most attackable gap?", execution_mode="fixture")
    )
    assert res["answer"]
    assert isinstance(res["suggested_followups"], list) and len(res["suggested_followups"]) >= 1
    assert res["confidence"] in ("high", "medium", "low")


def test_chat_unknown_run_raises(isolated_env: Path):
    from competitive_agent.chat import chat_about_run

    with pytest.raises(KeyError):
        asyncio.run(chat_about_run("RUN-nope", "hi", execution_mode="fixture"))


def test_chat_api_endpoint(isolated_env: Path):
    from fastapi.testclient import TestClient

    from competitive_agent.api import app
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    client = TestClient(app)
    # missing question -> 400
    assert client.post(f"/api/runs/{state.run_id}/chat", json={"question": ""}).status_code == 400
    # unknown run -> 404
    assert client.post("/api/runs/RUN-x/chat", json={"question": "hi"}).status_code == 404
    # real question (fixture) -> grounded answer + followups
    r = client.post(
        f"/api/runs/{state.run_id}/chat",
        json={"question": "What changed recently?", "execution_mode": "fixture"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] and "suggested_followups" in body


def test_rewindow_endpoint_recounts_deterministically(isolated_env: Path):
    from fastapi.testclient import TestClient

    from competitive_agent.api import app
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    client = TestClient(app)
    # unknown run -> 404; invalid windows -> 422
    assert (
        client.post(
            "/api/runs/RUN-x/rewindow", json={"lookback_days": 365, "current_days": 90}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/runs/{state.run_id}/rewindow", json={"lookback_days": 90, "current_days": 365}
        ).status_code
        == 422
    )
    r = client.post(
        f"/api/runs/{state.run_id}/rewindow", json={"lookback_days": 180, "current_days": 30}
    )
    assert r.status_code == 200
    body = r.json()
    assert {"time_windows", "temporal_baseline", "change_events", "reconciliation_notes"} <= set(
        body
    )
    # Anchored at the run's ORIGINAL current-window end, never now().
    orig_end = next(w for w in state.time_windows if w.purpose == "current").end_at.isoformat()
    new_cur = next(w for w in body["time_windows"] if w["purpose"] == "current")
    assert new_cur["end_at"][:19] == orig_end[:19]
    # Same params as the run's own windows must reproduce the persisted baseline shape.
    assert isinstance(body["temporal_baseline"], dict)


def test_new_run_request_validates_window_params(isolated_env: Path):
    from fastapi.testclient import TestClient

    from competitive_agent.api import app

    client = TestClient(app)
    bad = client.post(
        "/api/runs",
        json={
            "company": "x.com",
            "execution_mode": "fixture",
            "lookback_days": 90,
            "current_days": 180,
        },
    )
    assert bad.status_code == 400
    assert "current_days" in bad.json()["detail"]


def test_live_progress_snapshot_and_runs_list_persistence(isolated_env: Path):
    """A run is visible from the DB alone — refresh/restart-proof (user req).

    /api/runs/{id}/live returns checkpointed progress (phase, counts, source
    mix, latest sources) from pure DB/trace reads; /api/runs lists a run with
    no data.json yet as in_progress with a derived live_status.
    """
    from fastapi.testclient import TestClient

    from competitive_agent.api import app
    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis
    from competitive_agent.storage.repository import Repository

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    client = TestClient(app)

    live = client.get(f"/api/runs/{state.run_id}/live")
    assert live.status_code == 200
    snap = live.json()
    assert snap["status"] == "complete" and snap["report_ready"] is True
    assert snap["phase"]  # humanized, never a raw node id with underscores
    assert "_" not in snap["phase"]
    assert snap["counts"]["artifacts"] > 0
    assert snap["counts"]["classified"] > 0
    assert isinstance(snap["source_mix"], list) and snap["source_mix"]
    assert isinstance(snap["latest_artifacts"], list) and snap["latest_artifacts"]
    assert all("url" in a and "source_type" in a for a in snap["latest_artifacts"])
    assert isinstance(snap["recent_activity"], list)

    assert client.get("/api/runs/RUN-nope/live").status_code == 404

    # Simulate an interrupted run: flip the DB row back to running with a
    # stale heartbeat and hide the report — the list must still show it.
    repo = Repository.open(get_settings().db_path)
    repo.conn.execute(
        "UPDATE runs SET status='running', current_node='execute_action', "
        "updated_at='2020-01-01T00:00:00+00:00' WHERE run_id=?",
        (state.run_id,),
    )
    repo.conn.commit()
    data = Path(get_settings().outputs_dir) / "runs" / state.run_id / "data.json"
    hidden = data.rename(data.with_suffix(".hidden"))
    try:
        rows = client.get("/api/runs").json()
        mine = next(r for r in rows if r["run_id"] == state.run_id)
        assert mine["in_progress"] is True
        assert mine["live_status"] == "interrupted"  # stale heartbeat, no thread
        assert mine["phase"] == "Collecting sources"
        snap2 = client.get(f"/api/runs/{state.run_id}/live").json()
        assert snap2["status"] == "interrupted" and snap2["report_ready"] is False
    finally:
        hidden.rename(data)


def test_resume_endpoint_guards_and_dismiss(isolated_env: Path):
    """Resume 404s on unknown runs, 409s when a report exists; dismiss

    marks an orphaned run failed so it leaves the queue (data retained)."""
    from fastapi.testclient import TestClient

    from competitive_agent.api import app
    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis
    from competitive_agent.storage.repository import Repository

    state = run_analysis("deel.com", mode="snapshot", execution_mode="fixture")
    client = TestClient(app)

    assert client.post("/api/runs/RUN-nope/resume").status_code == 404
    # Completed run with a report on disk: nothing to resume.
    assert client.post(f"/api/runs/{state.run_id}/resume").status_code == 409

    # Orphan the run (no report, stale running row) and resume it for real:
    # the job thread drives it from the checkpoint back to a finished report.
    repo = Repository.open(get_settings().db_path)
    repo.conn.execute(
        "UPDATE runs SET status='running', current_node='assess_coverage' WHERE run_id=?",
        (state.run_id,),
    )
    repo.conn.commit()
    data = Path(get_settings().outputs_dir) / "runs" / state.run_id / "data.json"
    data.unlink()
    res = client.post(f"/api/runs/{state.run_id}/resume")
    assert res.status_code == 200
    job = res.json()
    assert job["run_id"] == state.run_id and job["resumed"] is True
    import time

    for _ in range(200):  # fixture runs finish in seconds
        jobs = {j["job_id"]: j for j in client.get("/api/jobs").json()}
        if jobs[job["job_id"]]["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert jobs[job["job_id"]]["status"] == "done", jobs[job["job_id"]].get("error")
    assert data.exists()  # the resumed run re-rendered its report

    # Dismiss: only for runs nobody is driving; status flip only, data kept.
    state2 = run_analysis("deel.com", mode="snapshot", execution_mode="fixture")
    repo.conn.execute(
        "UPDATE runs SET status='running', updated_at='2020-01-01T00:00:00+00:00' WHERE run_id=?",
        (state2.run_id,),
    )
    repo.conn.commit()
    assert client.post(f"/api/runs/{state2.run_id}/dismiss").json()["status"] == "failed"
    assert repo.get_run(state2.run_id)["status"] == "failed"
    n_artifacts = repo.conn.execute(
        "SELECT COUNT(*) FROM artifacts WHERE run_id=?", (state2.run_id,)
    ).fetchone()[0]
    assert n_artifacts > 0  # dismiss never deletes collected evidence


def test_paid_search_targeting_guards_and_cache(isolated_env: Path):
    """Paid-search drafts are grounded and guarded (user req: ad targeting).

    validate_before_spend and conquesting legal review are FORCED regardless
    of model output; a quote absent from the supplied evidence demotes its
    cluster to inferred/low; results cache to paid_search.json.
    """
    import asyncio

    from fastapi.testclient import TestClient

    from competitive_agent.api import app
    from competitive_agent.paid_search import generate_paid_search_targets
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    res = asyncio.run(generate_paid_search_targets(state.run_id, execution_mode="fixture"))
    assert res["disclaimer"].startswith("Search volume")
    by_label = {c["cluster_label"]: c for c in res["clusters"]}

    grounded = by_label["consolidating HR tools"]
    assert grounded["validate_before_spend"] is True  # fixture said false — forced
    assert grounded["quote_verified"] is True  # 'consolidating_hr_tools' is in the CEP block
    assert grounded["priority_tier"] == "high"  # verified quote keeps its tier

    conquest = by_label["competitor brand comparison"]
    assert conquest["legal_review_required"] is True  # forced for conquesting
    assert conquest["quote_verified"] is False  # fabricated quote
    assert conquest["evidence_basis"] == "inferred"  # demoted
    assert conquest["priority_tier"] == "low"  # capped
    assert "could not be verified" in conquest["risk_note"]

    # Cached on disk; the API serves it without regenerating.
    cache = Path(get_settings_outputs()) / "runs" / state.run_id / "paid_search.json"
    assert cache.exists()
    client = TestClient(app)
    got = client.get(f"/api/runs/{state.run_id}/paid-search").json()
    assert got["generated"] is True and len(got["clusters"]) == 2
    assert client.get("/api/runs/RUN-nope/paid-search").json() == {"generated": False}
    post = client.post(
        f"/api/runs/{state.run_id}/paid-search", json={"execution_mode": "fixture"}
    )
    assert post.status_code == 200 and post.json()["generated"] is True
    assert (
        client.post("/api/runs/RUN-nope/paid-search", json={"execution_mode": "fixture"})
        .status_code
        == 404
    )


def get_settings_outputs():
    from competitive_agent.config import get_settings

    return get_settings().outputs_dir
