"""LOOP contract tests (CONTRACTS.md): Level-B starvation rebalance, coverage
labeling after collection, source_allowlist enforcement, per-competitor
Similarweb peer proposals, and interactive fallback decisions.

Everything runs in fixture mode with ZERO API keys — no network, no live
providers. The comparative fixture run is module-scoped so several assertions
share one (seconds-long) run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from competitive_agent.graph import GraphContext
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.company import Company
from competitive_agent.schemas.source import ResearchAction, ToolResult
from competitive_agent.state import DirectorState


def _company(domain: str = "x.com", name: str = "X") -> Company:
    return Company(
        company_id="CO-x",
        canonical_name=name,
        primary_domain=domain,
        resolved_at=datetime.now(UTC),
        resolution_confidence="high",
    )


class _Cfg:
    """Minimal AppConfig stand-in for direct planner/node calls."""

    sources = {
        "website": True,
        "wayback": True,
        "exa_web": True,
        "exa_linkedin": True,
        "similarweb": True,
        "google_ads": True,
        "meta_ads": True,
        "linkedin_ads": True,
        "reviews": True,
        "jobs": True,
        "events": True,
        "ooh": True,
    }
    budgets = {"max_retries_per_source": 2}
    windows = {}
    collection = {"deep_crawl": False}
    exa_agent = {}
    historical = {}
    taxonomy = {}
    focal_company = {}


def _crafted_state(**overrides) -> DirectorState:
    state = DirectorState(run_id="RUN-loop", company_input="x.com", **overrides)
    state.company = _company()
    return state


# ---------------------------------------------------------------------------
# Module-scoped default comparative fixture run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_run(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("loop-rebalance")
    mp.setenv("ANTHROPIC_API_KEY", "")
    mp.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    mp.setattr(settings, "db_path", tmp / "agent.db")
    mp.setattr(settings, "outputs_dir", tmp / "outputs")
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    yield state
    mp.undo()
    config_mod.reset_config_cache()


def _executed_types(state: DirectorState) -> set[str]:
    return {k.split(":", 1)[0] for k in state.executed_action_keys}


def test_default_comparative_run_executes_reviews_and_ads(fixture_run):
    """Rebalance measure (CONTRACTS.md LOOP): search_reviews and
    search_google_ads each execute >= 1 time in a default comparative fixture
    run — without blowing the iteration/tool budgets to get there."""
    executed = _executed_types(fixture_run)
    assert "search_reviews" in executed
    assert "search_google_ads" in executed
    # Rebalanced by score, not by extending budgets.
    assert fixture_run.iteration <= fixture_run.max_iterations
    assert fixture_run.stop_reason not in ("max_iterations_reached", "tool_call_cap_reached")


def test_reviews_and_ads_dims_labeled_after_collection(fixture_run):
    """Coverage-model fix: once reviews/ads artifacts are collected, their
    dimensions must not read not_attempted (customer_proof via the evidenced
    map; paid_media low for discovery pointers, medium for real records)."""
    executed = _executed_types(fixture_run)
    assert "search_reviews" in executed and "search_google_ads" in executed
    assert fixture_run.coverage.get("customer_proof") in ("medium", "high")
    assert fixture_run.coverage.get("paid_media") in ("low", "medium", "high")


def test_peer_similarweb_enrichment_runs_after_digital_competitors(fixture_run):
    """The fixture similarweb artifact reports digital_competitors, so the run
    must also execute peer enrichments (params {domain, peer: true}) for the
    top-affinity domains, excluding the researched company's own domain."""
    peer_keys = [k for k in fixture_run.executed_action_keys if k.startswith("enrich_similarweb:")]
    # 1 for deel.com itself + peers (fixture lists deel.com, remote.com,
    # gusto.com; own domain excluded -> 2 peers).
    assert len(peer_keys) >= 3


# ---------------------------------------------------------------------------
# Starvation floor (score_and_select)
# ---------------------------------------------------------------------------


def _wayback_action() -> ResearchAction:
    return ResearchAction(
        action_id="ACT-wb",
        action_type="search_wayback",
        company_id="CO-x",
        source_name="wayback",
        parameters={"url": "https://www.x.com/"},
        strategic_importance=0.85,
        expected_reliability=0.6,
        estimated_cost_usd=0.01,
        estimated_latency_seconds=15.0,
    )


def _reviews_action() -> ResearchAction:
    return ResearchAction(
        action_id="ACT-rv",
        action_type="search_reviews",
        company_id="CO-x",
        source_name="reviews",
        parameters={"company": "X", "num_results": 4},
        strategic_importance=0.6,
        expected_reliability=0.5,
        estimated_cost_usd=0.01,
        estimated_latency_seconds=5.0,
    )


def test_starvation_floor_ramps_with_iteration():
    from competitive_agent.planner import score_and_select

    state = _crafted_state()
    state.coverage = {"customer_proof": "not_attempted"}

    # Early cycle: Level-A ordering is preserved (wayback wins).
    state.iteration = 0
    best, _ = score_and_select(state, [_wayback_action(), _reviews_action()])
    assert best is not None and best.action_type == "search_wayback"

    # Mid-run: a never-attempted Level-B source with a thin dimension is
    # floored above the repeat proposal — it can no longer starve.
    state.iteration = 10
    best, trace = score_and_select(state, [_wayback_action(), _reviews_action()])
    assert best is not None and best.action_type == "search_reviews"
    by_type = {r["action_type"]: r["utility"] for r in trace}
    assert by_type["search_reviews"] > by_type["search_wayback"]


def test_starvation_floor_only_for_never_attempted_thin_dims():
    from competitive_agent.planner import action_key, score_and_select

    state = _crafted_state()
    state.iteration = 10

    # Already attempted -> no floor.
    state.coverage = {"customer_proof": "not_attempted"}
    state.executed_action_keys = [action_key("search_reviews", {"company": "X"})]
    best, _ = score_and_select(state, [_wayback_action(), _reviews_action()])
    assert best is not None and best.action_type == "search_wayback"

    # Dimension already high -> no floor.
    state.executed_action_keys = []
    state.coverage = {"customer_proof": "high"}
    best, _ = score_and_select(state, [_wayback_action(), _reviews_action()])
    assert best is not None and best.action_type == "search_wayback"


def test_optional_sources_proposed_even_when_dim_incidentally_covered():
    """A /customers page raising customer_proof to medium must not stop the
    never-attempted dedicated reviews source from being proposed (live-trace
    starvation: search_reviews proposed 2x then never again)."""
    from competitive_agent.planner import propose_actions

    state = _crafted_state()
    state.coverage = {"customer_proof": "medium", "paid_media": "not_attempted"}
    ctx = GraphContext(repository=None, trace=None, config=_Cfg(), settings=None)
    types = {p.action_type for p in propose_actions(state, ctx)}
    assert "search_reviews" in types
    assert "search_google_ads" in types


# ---------------------------------------------------------------------------
# source_allowlist enforcement
# ---------------------------------------------------------------------------


def test_source_allowlist_filters_proposals_to_listed_sources():
    from competitive_agent.planner import propose_actions

    ctx = GraphContext(repository=None, trace=None, config=_Cfg(), settings=None)

    unrestricted = propose_actions(_crafted_state(), ctx)
    assert {p.source_name for p in unrestricted} - {"reviews"}  # many sources by default

    state = _crafted_state(source_allowlist=["reviews"])
    restricted = propose_actions(state, ctx)
    assert restricted, "allowlisted source must still be proposed"
    assert {p.source_name for p in restricted} == {"reviews"}

    # An empty allowlist blocks every external source.
    state_none = _crafted_state(source_allowlist=[])
    assert propose_actions(state_none, ctx) == []


def test_source_allowlist_keeps_internal_reuse_action():
    from competitive_agent.planner import propose_actions

    state = _crafted_state(
        source_allowlist=["reviews"],
        reuse_evidence_only=True,
        parent_run_id="RUN-parent",
    )
    ctx = GraphContext(repository=None, trace=None, config=_Cfg(), settings=None)
    proposals = propose_actions(state, ctx)
    assert [p.action_type for p in proposals] == ["reuse_evidence"]


# ---------------------------------------------------------------------------
# Per-competitor Similarweb peers (unit: propose_actions with a crafted state)
# ---------------------------------------------------------------------------


class _PeerRepo:
    def __init__(self, artifacts):
        self._artifacts = artifacts

    def list_artifacts(self, run_id=None):
        return self._artifacts


def _similarweb_artifact(competitors, peer=False) -> RawArtifact:
    return RawArtifact(
        artifact_id="ART-sw",
        company_id="CO-x",
        source_type="similarweb",
        source_name="similarweb",
        url="https://www.similarweb.com/website/x.com/",
        final_url="https://www.similarweb.com/website/x.com/",
        retrieved_at=datetime.now(UTC),
        raw_text="similarweb estimates",
        normalized_text="similarweb estimates",
        content_hash="sw",
        collection_method="exa_similarweb",
        metadata={
            "peer": peer,
            "metrics": {"digital_competitors": {"value": competitors, "estimated": True}},
        },
    )


def test_peer_proposals_from_digital_competitors_top_affinity():
    from competitive_agent.planner import propose_actions

    repo = _PeerRepo(
        [
            _similarweb_artifact(
                [
                    {"domain": "x.com", "affinity": 0.99},  # own domain: excluded
                    {"domain": "peer-a.com", "affinity": 0.9},
                    {"domain": "peer-b.com", "affinity": 0.8},
                    {"domain": "peer-c.com", "affinity": 0.7},
                    {"domain": "peer-d.com", "affinity": 0.6},  # beyond top 3
                ]
            )
        ]
    )
    ctx = GraphContext(repository=repo, trace=None, config=_Cfg(), settings=None)
    state = _crafted_state()
    peers = [
        p
        for p in propose_actions(state, ctx)
        if p.action_type == "enrich_similarweb" and p.parameters.get("peer") is True
    ]
    assert [p.parameters["domain"] for p in peers] == ["peer-a.com", "peer-b.com", "peer-c.com"]
    assert all(p.source_name == "similarweb" for p in peers)
    # Cached for later planning cycles (no repeated repository scans).
    assert ctx.scratch["similarweb_peers:CO-x"] == ["peer-a.com", "peer-b.com", "peer-c.com"]


def test_peer_proposals_ignore_peer_artifacts_and_missing_metrics():
    from competitive_agent.planner import propose_actions

    repo = _PeerRepo([_similarweb_artifact([{"domain": "peer-a.com", "affinity": 0.9}], peer=True)])
    ctx = GraphContext(repository=repo, trace=None, config=_Cfg(), settings=None)
    peers = [
        p
        for p in propose_actions(_crafted_state(), ctx)
        if p.action_type == "enrich_similarweb" and p.parameters.get("peer")
    ]
    assert peers == []  # peers-of-peers are never proposed


# ---------------------------------------------------------------------------
# Exa-Agent run cap (config exa_agent.max_exa_agent_runs — dead-knob fix)
# ---------------------------------------------------------------------------


def _exa_agent_backed(proposals):
    return [p for p in proposals if p.action_type in ("enrich_similarweb", "research_linkedin")]


def test_exa_agent_cap_blocks_new_proposals_at_cap():
    """At the cap (default 6 EXECUTED exa-agent-backed runs), no NEW
    enrich_similarweb / research_linkedin action — peer enrichments included —
    may be proposed; every other proposal type is untouched."""
    from competitive_agent.planner import action_key, propose_actions

    repo = _PeerRepo([_similarweb_artifact([{"domain": "peer-a.com", "affinity": 0.9}])])
    ctx = GraphContext(repository=repo, trace=None, config=_Cfg(), settings=None)
    state = _crafted_state()
    state.executed_action_keys = [
        action_key("enrich_similarweb", {"domain": f"done-{i}.com", "peer": True}) for i in range(5)
    ] + [action_key("research_linkedin", {"company": "X"})]
    proposals = propose_actions(state, ctx)
    assert _exa_agent_backed(proposals) == []
    assert proposals  # only exa-agent-backed actions are capped


def test_exa_agent_cap_below_cap_still_proposes_peers():
    from competitive_agent.planner import action_key, propose_actions

    repo = _PeerRepo([_similarweb_artifact([{"domain": "peer-a.com", "affinity": 0.9}])])
    ctx = GraphContext(repository=repo, trace=None, config=_Cfg(), settings=None)
    state = _crafted_state()
    state.executed_action_keys = [
        action_key("enrich_similarweb", {"domain": f"done-{i}.com", "peer": True}) for i in range(5)
    ]  # 5 of 6 — one bounded run left
    proposals = propose_actions(state, ctx)
    peers = [
        p
        for p in proposals
        if p.action_type == "enrich_similarweb" and p.parameters.get("peer") is True
    ]
    assert [p.parameters["domain"] for p in peers] == ["peer-a.com"]


def test_exa_agent_cap_reads_config_knob():
    from competitive_agent.planner import action_key, propose_actions

    class _CfgCap(_Cfg):
        exa_agent = {"max_exa_agent_runs": 1}

    repo = _PeerRepo([_similarweb_artifact([{"domain": "peer-a.com", "affinity": 0.9}])])
    state = _crafted_state()
    state.executed_action_keys = [action_key("enrich_similarweb", {"domain": "x.com"})]
    capped = propose_actions(
        state, GraphContext(repository=repo, trace=None, config=_CfgCap(), settings=None)
    )
    assert _exa_agent_backed(capped) == []
    # Same single executed run under the default cap (6): proposals still flow.
    open_proposals = propose_actions(
        state, GraphContext(repository=repo, trace=None, config=_Cfg(), settings=None)
    )
    assert _exa_agent_backed(open_proposals)


# ---------------------------------------------------------------------------
# Interactive fallback decisions (execute_action, invoked directly)
# ---------------------------------------------------------------------------


class _FailingRegistry:
    async def run_action(self, action, tool_ctx):
        return ToolResult(
            action_id=action.action_id,
            tool_name="wayback",
            status="failed_terminal",
            error_type="provider_http_500",
            error_message="wayback exploded",
            negative_observations=["wayback returned HTTP 500 for https://www.x.com/"],
        )


def _fallback_action() -> ResearchAction:
    return ResearchAction(
        action_id="ACT-fb",
        action_type="search_wayback",
        company_id="CO-x",
        source_name="wayback",
        parameters={"url": "https://www.x.com/"},
        fallback_action_types=["search_news_launches"],
    )


def _exec_ctx() -> GraphContext:
    ctx = GraphContext(
        repository=None,
        trace=None,
        config=_Cfg(),
        settings=None,
        tool_registry=_FailingRegistry(),
    )
    ctx.scratch["selected_action"] = _fallback_action()
    return ctx


def test_interactive_source_failure_pauses_for_user_decision():
    from competitive_agent.nodes import execute_action

    state = _crafted_state(interactive=True, execution_mode="fixture")
    ctx = _exec_ctx()
    state, next_node = asyncio.run(execute_action(state, ctx))

    assert next_node == "awaiting_user"
    pd = state.pending_decision
    assert pd is not None
    assert set(pd) == {"question", "context", "options"}
    assert isinstance(pd["question"], str) and "wayback" in pd["question"]
    assert isinstance(pd["context"], str) and pd["context"]
    ids = [o["id"] for o in pd["options"]]
    assert ids == ["search_news_launches", "skip"]
    by_id = {o["id"]: o for o in pd["options"]}
    assert by_id["search_news_launches"]["source"] == "exa_search"
    assert by_id["skip"]["source"] is None
    assert all(o["label"] for o in pd["options"])
    # No auto decision is logged while the user decides.
    assert state.decision_log == []


def test_non_interactive_source_failure_auto_falls_back_and_logs():
    from competitive_agent.nodes import execute_action

    state = _crafted_state(interactive=False, execution_mode="fixture")
    ctx = _exec_ctx()
    state, next_node = asyncio.run(execute_action(state, ctx))

    assert next_node == "normalize_and_deduplicate"  # run continues
    assert state.pending_decision is None
    assert len(state.decision_log) == 1
    entry = state.decision_log[0]
    assert entry["via"] == "auto"
    assert entry["choice"] == "search_news_launches"
    assert "wayback" in entry["question"]
    # Failure is still recorded so retries/fallback eligibility work as before.
    assert "wayback:search_wayback" in state.failed_actions


# ---------------------------------------------------------------------------
# Coverage: exhausted Level-B failures mark dims unavailable, not not_attempted
# ---------------------------------------------------------------------------


def test_exhausted_level_b_failure_marks_dim_unavailable():
    from competitive_agent.nodes import update_coverage

    state = _crafted_state()
    state.coverage = {"customer_proof": "not_attempted"}
    action = _reviews_action()
    state.record_failure("reviews", "search_reviews", "provider_http_500")
    state.record_failure("reviews", "search_reviews", "provider_http_500")

    ctx = GraphContext(repository=None, trace=None, config=_Cfg(), settings=None)
    ctx.scratch["last_action"] = action
    ctx.scratch["last_result"] = ToolResult(
        action_id=action.action_id, tool_name="reviews", status="failed_terminal"
    )
    state, _ = asyncio.run(update_coverage(state, ctx))
    assert state.coverage["customer_proof"] == "unavailable"
    assert any("reviews unavailable" in lim for lim in state.limitations)


def test_real_ad_records_raise_paid_media_to_medium():
    from competitive_agent.nodes import update_coverage

    state = _crafted_state()
    state.coverage = {"paid_media": "not_attempted"}
    action = ResearchAction(
        action_id="ACT-ads",
        action_type="search_google_ads",
        company_id="CO-x",
        source_name="google_ads",
        parameters={"advertiser": "X", "domain": "x.com"},
    )
    record = RawArtifact(
        artifact_id="ART-ad",
        company_id="CO-x",
        source_type="google_ads",
        source_name="google_ads",
        url="https://adstransparency.google.com/advertiser/AR1?domain=x.com",
        final_url="https://adstransparency.google.com/advertiser/AR1?domain=x.com",
        retrieved_at=datetime.now(UTC),
        raw_text="Run global payroll in minutes — X.com",
        normalized_text="Run global payroll in minutes — X.com",
        content_hash="ad1",
        collection_method="ads",
        metadata={},  # a real record, not a discovery pointer
    )
    ctx = GraphContext(repository=None, trace=None, config=_Cfg(), settings=None)
    ctx.scratch["last_action"] = action
    ctx.scratch["last_result"] = ToolResult(
        action_id=action.action_id, tool_name="google_ads", status="success", artifacts=[record]
    )
    state, _ = asyncio.run(update_coverage(state, ctx))
    assert state.coverage["paid_media"] == "medium"

    # Discovery pointers alone stay capped at low.
    state2 = _crafted_state()
    state2.coverage = {"paid_media": "not_attempted"}
    pointer = record.model_copy(update={"metadata": {"is_discovery_pointer": True}})
    ctx.scratch["last_result"] = ToolResult(
        action_id=action.action_id, tool_name="google_ads", status="success", artifacts=[pointer]
    )
    state2, _ = asyncio.run(update_coverage(state2, ctx))
    assert state2.coverage["paid_media"] == "low"
