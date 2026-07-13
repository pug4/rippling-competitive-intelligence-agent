"""LLM-in-the-loop plan selection (§37.16).

The rules PROPOSE and score candidate actions; the reasoning model DECIDES the
next candidate (in its own words) or decides to stop. These tests pin the
contract: a model choice is used and its rationale threaded; any model
failure/invalid output falls back to the deterministic top scorer; the model
can never introduce an action outside the supplied candidate set; the config
flag disables the model path entirely; and a model stop is routed + recorded.

Everything runs with ZERO API keys and NO network — a fake gateway stands in for
the model, and the one end-to-end check uses the deterministic FixtureGateway.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from competitive_agent import coverage as cov
from competitive_agent import nodes, planner
from competitive_agent.graph import GraphContext
from competitive_agent.planner import LLMPlanDecision, PlanSelection, llm_plan_selection
from competitive_agent.schemas.common import new_id
from competitive_agent.schemas.company import Company
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.state import DirectorState

MODEL_TEXT = (
    "I am prioritizing the reviews source next because buyer-voice is the thinnest "
    "high-value gap right now and the last two page fetches already saturated "
    "positioning; ads can wait until I know the objections to counter."
)
STOP_TEXT = (
    "Required coverage on positioning, pricing, and the mirror is all in hand and "
    "the recent tool calls stopped returning anything new, so further collection "
    "would add breadth without changing the competitive read — I am stopping here."
)


# ---------------------------------------------------------------------------
# Fakes / builders
# ---------------------------------------------------------------------------


class _FakeGateway:
    """Records calls; returns a preset PlanSelection or raises."""

    def __init__(self, output: PlanSelection | None = None, raises: bool = False) -> None:
        self._output = output
        self._raises = raises
        self.calls = 0
        self.total_cost_usd = 0.0

    async def generate_structured(self, task_name, system, user_content, output_model, **kw):
        self.calls += 1
        if self._raises:
            raise RuntimeError("simulated model timeout")
        from competitive_agent.model_gateway import ModelResult

        return ModelResult(
            output=self._output,
            model_id="fake",
            task_name=task_name,
            prompt_name=kw.get("prompt_name", ""),
            prompt_version=kw.get("prompt_version", ""),
        )


class _Trace:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type: str, payload: dict | None = None) -> None:
        self.events.append((event_type, payload or {}))


def _cfg(llm: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        sources={
            "similarweb": True,
            "exa_linkedin": True,
            "reviews": True,
            "jobs": True,
            "events": True,
            "ooh": True,
            "google_ads": True,
            "meta_ads": True,
            "linkedin_ads": True,
            "keywords": True,
        },
        budgets={"max_retries_per_source": 2},
        windows={},
        collection={"deep_crawl": False},
        exa_agent={},
        execution={"llm_in_the_loop": llm},
        focal_company=SimpleNamespace(name="Rippling", domain="rippling.com"),
    )


def _company() -> Company:
    return Company(
        company_id="CO-x",
        canonical_name="Deel",
        primary_domain="deel.com",
        resolved_at=datetime.now(UTC),
        resolution_confidence="high",
    )


def _state(**overrides) -> DirectorState:
    state = DirectorState(run_id="RUN-llm", company_input="deel.com", **overrides)
    state.company = _company()
    return state


def _action(action_type: str, source: str, si: float = 0.7, rel: float = 0.6) -> ResearchAction:
    return ResearchAction(
        action_id=new_id("ACT"),
        action_type=action_type,
        company_id="CO-x",
        source_name=source,
        rationale=f"deterministic rule rationale for {action_type}",
        strategic_importance=si,
        expected_reliability=rel,
    )


def _ctx(gateway=None, config=None, trace=None) -> GraphContext:
    return GraphContext(
        repository=None,
        trace=trace,
        config=config or _cfg(),
        settings=None,
        gateway=gateway,
    )


# ---------------------------------------------------------------------------
# (a) A model choice is used and its rationale threaded
# ---------------------------------------------------------------------------


def test_model_choice_is_used_and_rationale_threaded():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch"), _action("search_reviews", "reviews")]
    gw = _FakeGateway(PlanSelection(chosen_index=1, model_rationale=MODEL_TEXT))
    decision = asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=gw)))
    assert isinstance(decision, LLMPlanDecision)
    assert decision.action is candidates[1]  # the endorsed candidate, by object
    assert decision.model_rationale == MODEL_TEXT  # the model's own text, verbatim
    assert decision.should_stop is False
    assert gw.calls == 1


def test_model_choice_by_action_type_matches_supplied_candidate():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch"), _action("search_reviews", "reviews")]
    gw = _FakeGateway(
        PlanSelection(
            chosen_action_type="search_reviews", chosen_source="reviews", model_rationale=MODEL_TEXT
        )
    )
    decision = asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=gw)))
    assert decision is not None and decision.action is candidates[1]


# ---------------------------------------------------------------------------
# (b) Any model failure -> None (deterministic fallback)
# ---------------------------------------------------------------------------


def test_gateway_error_returns_none():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch")]
    gw = _FakeGateway(raises=True)
    assert asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=gw))) is None


def test_no_gateway_returns_none():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch")]
    assert asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=None))) is None


# ---------------------------------------------------------------------------
# (c) Out-of-range / unknown action -> None (never a fabricated action)
# ---------------------------------------------------------------------------


def test_chosen_index_out_of_range_returns_none():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch")]
    gw = _FakeGateway(PlanSelection(chosen_index=7, model_rationale=MODEL_TEXT))
    assert asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=gw))) is None


def test_action_not_in_candidates_returns_none():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch")]
    gw = _FakeGateway(
        PlanSelection(chosen_action_type="search_meta_ads", model_rationale=MODEL_TEXT)
    )
    assert asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=gw))) is None


def test_no_candidates_returns_none():
    state = _state()
    gw = _FakeGateway(PlanSelection(chosen_index=0, model_rationale=MODEL_TEXT))
    assert asyncio.run(llm_plan_selection(state, [], [], _ctx(gateway=gw))) is None
    assert gw.calls == 0  # no candidates -> the model is never consulted


# ---------------------------------------------------------------------------
# (d) Flag off -> the model path is never invoked
# ---------------------------------------------------------------------------


def test_flag_off_never_calls_gateway():
    state = _state()
    candidates = [_action("fetch_webpage", "webpage_fetch")]
    gw = _FakeGateway(PlanSelection(chosen_index=0, model_rationale=MODEL_TEXT))
    decision = asyncio.run(
        llm_plan_selection(state, candidates, [], _ctx(gateway=gw, config=_cfg(llm=False)))
    )
    assert decision is None
    assert gw.calls == 0


def test_score_actions_flag_off_never_calls_gateway():
    state = _state()
    ctx = _ctx(
        gateway=_FakeGateway(PlanSelection(chosen_index=1, model_rationale=MODEL_TEXT)),
        config=_cfg(llm=False),
    )
    props = [_action("fetch_webpage", "webpage_fetch"), _action("search_reviews", "reviews")]
    ctx.scratch["proposed_actions"] = props
    asyncio.run(nodes.score_actions(state, ctx))
    assert ctx.gateway.calls == 0
    assert ctx.scratch["selection_decision_by"] == "heuristic"
    # Deterministic winner is used unchanged.
    assert ctx.scratch["selected_action"] is planner.ranked_candidates(state, props)[0]


# ---------------------------------------------------------------------------
# (e) should_stop is honored and carries the model's own rationale
# ---------------------------------------------------------------------------


def test_should_stop_returned_as_decision():
    state = _state()
    candidates = [_action("search_reviews", "reviews")]
    gw = _FakeGateway(
        PlanSelection(
            should_stop=True, model_rationale=STOP_TEXT, stop_rationale="all required covered"
        )
    )
    decision = asyncio.run(llm_plan_selection(state, candidates, [], _ctx(gateway=gw)))
    assert decision is not None
    assert decision.should_stop is True
    assert decision.action is None
    assert decision.model_rationale == STOP_TEXT
    assert decision.stop_rationale == "all required covered"


def test_model_stop_routes_to_stop_path_and_records_rationale():
    state = _state()
    # All REQUIRED dimensions at medium so nothing still needs its fallbacks
    # exhausted -> the model stop is safe to honor (accuracy floor satisfied).
    for dim in cov.required_dimensions(state.mode, False):
        state.coverage[dim] = "medium"
    gw = _FakeGateway(
        PlanSelection(
            should_stop=True, model_rationale=STOP_TEXT, stop_rationale="all required covered"
        )
    )
    ctx = _ctx(gateway=gw)
    ctx.scratch["proposed_actions"] = [_action("search_reviews", "reviews")]

    asyncio.run(nodes.score_actions(state, ctx))
    assert ctx.scratch["model_requested_stop"] is True

    # A model-decided stop first routes through refresh_claims (so the final
    # claim ledger is built against the full corpus before rendering), then to
    # the stop decision. The finalize hop runs exactly once.
    _, nxt = asyncio.run(nodes.select_next_action(state, ctx))
    assert nxt == "refresh_claims"
    _, nxt_again = asyncio.run(nodes.select_next_action(state, ctx))
    assert nxt_again == "decide_continue_or_stop"

    trace = _Trace()
    ctx.trace = trace
    _, nxt2 = asyncio.run(nodes.decide_continue_or_stop(state, ctx))
    assert nxt2 == "render_outputs"
    assert state.stop_reason.startswith("model_judged_complete")
    stop_events = [p for t, p in trace.events if t == "stop_selected"]
    assert stop_events and stop_events[-1]["decision_by"] == "model"
    assert stop_events[-1]["model_rationale"] == STOP_TEXT


def test_model_stop_deferred_when_required_dimension_still_open():
    """Accuracy #1: the model cannot waive a REQUIRED dimension. With a required
    dimension still below medium, a model stop is deferred (not honored) and the
    deterministic winner is kept."""
    state = _state()  # fresh coverage: required dims are not_attempted
    gw = _FakeGateway(PlanSelection(should_stop=True, model_rationale=STOP_TEXT))
    ctx = _ctx(gateway=gw)
    props = [_action("fetch_webpage", "webpage_fetch"), _action("search_reviews", "reviews")]
    ctx.scratch["proposed_actions"] = props
    asyncio.run(nodes.score_actions(state, ctx))
    assert ctx.scratch["model_requested_stop"] is False
    assert ctx.scratch.get("model_stop_deferred") == STOP_TEXT
    assert ctx.scratch["selection_decision_by"] == "heuristic"


# ---------------------------------------------------------------------------
# Node integration: model choice threaded into the action_selected trace
# ---------------------------------------------------------------------------


def test_score_and_select_node_threads_model_decision_into_trace():
    state = _state()
    props = [_action("fetch_webpage", "webpage_fetch"), _action("search_reviews", "reviews")]
    expected = planner.ranked_candidates(state, props)[1]  # endorse the #2-ranked candidate
    idx = planner.ranked_candidates(state, props).index(expected)
    gw = _FakeGateway(
        PlanSelection(
            chosen_index=idx, model_rationale=MODEL_TEXT, deprioritized=["search_meta_ads"]
        )
    )
    ctx = _ctx(gateway=gw)
    ctx.scratch["proposed_actions"] = props

    asyncio.run(nodes.score_actions(state, ctx))
    assert ctx.scratch["selected_action"] is expected
    assert ctx.scratch["selection_decision_by"] == "model"
    assert ctx.scratch["selection_model_rationale"] == MODEL_TEXT

    trace = _Trace()
    ctx.trace = trace
    asyncio.run(nodes.select_next_action(state, ctx))
    sel = [p for t, p in trace.events if t == "action_selected"]
    assert sel, "action_selected must still be emitted"
    payload = sel[-1]
    assert payload["decision_by"] == "model"
    assert payload["model_rationale"] == MODEL_TEXT  # model prose, not an f-string
    assert payload["model_rationale"] != payload["rationale"]  # distinct from the rule rationale
    assert payload["alternatives_considered"]  # alternatives still recorded


def test_heuristic_fallback_node_marks_decision_by_heuristic():
    state = _state()
    props = [_action("fetch_webpage", "webpage_fetch"), _action("search_reviews", "reviews")]
    gw = _FakeGateway(raises=True)  # model unavailable -> deterministic winner
    ctx = _ctx(gateway=gw)
    ctx.scratch["proposed_actions"] = props
    asyncio.run(nodes.score_actions(state, ctx))
    assert ctx.scratch["selected_action"] is planner.ranked_candidates(state, props)[0]
    assert ctx.scratch["selection_decision_by"] == "heuristic"

    trace = _Trace()
    ctx.trace = trace
    asyncio.run(nodes.select_next_action(state, ctx))
    payload = [p for t, p in trace.events if t == "action_selected"][-1]
    assert payload["decision_by"] == "heuristic"
    assert "model_rationale" not in payload


# ---------------------------------------------------------------------------
# recent_tool_outcomes derivation
# ---------------------------------------------------------------------------


def test_recent_tool_outcomes_prefers_scratch_buffer():
    state = _state()
    ctx = _ctx()
    ctx.scratch[planner.RECENT_OUTCOMES_SCRATCH_KEY] = [
        {
            "source": "reviews",
            "action_type": "search_reviews",
            "status": "empty",
            "artifact_count": 0,
        },
        {
            "source": "webpage_fetch",
            "action_type": "fetch_webpage",
            "status": "success",
            "artifact_count": 4,
        },
    ]
    out = planner.recent_tool_outcomes(state, ctx, limit=5)
    assert out[-1]["status"] == "success" and out[-1]["artifact_count"] == 4


def test_recent_tool_outcomes_state_fallback_is_honest():
    state = _state()
    state.executed_action_keys = ["fetch_webpage:aaa", "search_meta_ads:bbb"]
    state.record_failure("meta_ads", "search_meta_ads", "unsupported")
    ctx = _ctx()  # empty scratch -> state fallback
    out = planner.recent_tool_outcomes(state, ctx, limit=5)
    by_type = {o["action_type"]: o for o in out}
    assert by_type["search_meta_ads"]["status"] == "failed"
    assert by_type["fetch_webpage"]["status"] == "success"
    # Artifact counts are unknown on the state fallback -> null, never fabricated.
    assert by_type["fetch_webpage"]["artifact_count"] is None


# ---------------------------------------------------------------------------
# End-to-end: deterministic FixtureGateway drives the loop through the model
# path (default.json), still reaches a terminal stop, and renders a brief.
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch):
    # Hermetic + keyless: empty secrets via setenv (never delenv), tmp outputs.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


def test_fixture_comparative_run_uses_model_path_and_completes(fixture_env):
    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    assert state.stop_reason  # terminal stop reached
    run_dir = get_settings().outputs_dir / "runs" / state.run_id
    assert (run_dir / "brief.md").exists()  # brief produced

    events = [
        json.loads(x) for x in (run_dir / "trace.jsonl").read_text().splitlines() if x.strip()
    ]
    selected = [e["payload"] for e in events if e.get("event_type") == "action_selected"]
    # The FixtureGateway exercised the model path deterministically (default.json
    # endorses candidate 0) -> decision_by:"model" with the fixture's prose.
    assert any(p.get("decision_by") == "model" for p in selected)
    assert any("Coverage is still thin" in (p.get("model_rationale") or "") for p in selected)
