"""Correctness + robustness polish (additions-only).

Two real "works on any competitor" defects, reproduced deterministically:

FIX 1 — the offset-naive vs offset-aware datetime crash. A live workday.com run
crashed in ``run_focal_mirror_check`` with ``TypeError: can't compare
offset-naive and offset-aware datetimes``. Root cause: ``synthesis.assign_window``
compared an artifact date (``archive_capture_at``/``published_at`` — some sources
yield NAIVE datetimes) against tz-AWARE time-window bounds. Fixed by construction
with ``synthesis._as_aware`` at every window comparison.

FIX 2 — the provider circuit breaker. After Exa returned HTTP 402 out-of-credits
mid-run, the loop scheduled more Exa-backed tools into the dead provider, ending
fresh competitors in ``runtime_exhausted``. Fixed by recording the dead provider
on ``state.dead_providers`` (a terminal 402/401 error), skipping its tools in the
planner, and disclosing the outage once.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from competitive_agent import nodes, planner, synthesis
from competitive_agent.config import get_config
from competitive_agent.coverage import initial_coverage
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.classification import MarketingClassification
from competitive_agent.schemas.common import new_id, utcnow
from competitive_agent.schemas.company import Company, TimeWindow
from competitive_agent.schemas.source import ResearchAction, ToolResult
from competitive_agent.state import DirectorState

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)  # aware "now"


def _windows() -> list[TimeWindow]:
    """Aware current + comparison windows, exactly as nodes.py builds them."""
    return [
        TimeWindow(
            window_id="w-current",
            label="current",
            start_at=_NOW - timedelta(days=90),
            end_at=_NOW,
            purpose="current",
        ),
        TimeWindow(
            window_id="w-prior",
            label="comparison",
            start_at=_NOW - timedelta(days=365),
            end_at=_NOW - timedelta(days=90),
            purpose="comparison",
        ),
    ]


def _artifact(
    aid: str,
    *,
    published_at: datetime | None = None,
    archive_capture_at: datetime | None = None,
) -> RawArtifact:
    return RawArtifact(
        artifact_id=aid,
        company_id="c1",
        source_type="news",
        source_name="s",
        url=f"https://x/{aid}",
        final_url=f"https://x/{aid}",
        retrieved_at=_NOW,
        published_at=published_at,
        archive_capture_at=archive_capture_at,
        raw_text="x",
        normalized_text="x",
        content_hash=aid,
        collection_method="m",
    )


# ---------------------------------------------------------------------------
# FIX 1 — the offset-naive vs offset-aware datetime crash
# ---------------------------------------------------------------------------


def test_naive_vs_aware_raw_comparison_is_the_pre_fix_crash():
    """Document the exact defect: comparing an aware window bound against a NAIVE
    artifact date the way the pre-fix code did (``bound <= dated``) raises the
    logged TypeError. This is what took down run_focal_mirror_check; the fix
    below makes assign_window immune to it."""
    aware_bound = _NOW - timedelta(days=90)
    naive_date = datetime(2026, 1, 15, 12, 0, 0)  # no tzinfo — some sources yield this
    assert aware_bound.tzinfo is not None
    assert naive_date.tzinfo is None
    with pytest.raises(TypeError, match="offset-naive and offset-aware"):
        _ = aware_bound <= naive_date


def test_assign_window_naive_published_date_does_not_crash():
    """Post-fix: an artifact carrying a NAIVE published_at is assigned a window
    without raising — and lands in the correct window (assumed UTC)."""
    windows = _windows()
    # A naive date 200 days ago falls inside the comparison (prior) window.
    naive_prior = (_NOW - timedelta(days=200)).replace(tzinfo=None)
    assert naive_prior.tzinfo is None
    art = _artifact("ART-naive-prior", published_at=naive_prior)
    assert art.published_at is not None and art.published_at.tzinfo is None  # survives pydantic
    assert synthesis.assign_window(art, windows) == "prior"

    # A naive date 5 days ago falls inside the current window.
    naive_current = (_NOW - timedelta(days=5)).replace(tzinfo=None)
    art2 = _artifact("ART-naive-current", published_at=naive_current)
    assert synthesis.assign_window(art2, windows) == "current"


def test_assign_window_naive_archive_capture_date_does_not_crash():
    """The other date field (archive_capture_at, preferred over published_at) is
    equally coerced — a naive wayback capture no longer crashes."""
    windows = _windows()
    naive_capture = (_NOW - timedelta(days=150)).replace(tzinfo=None)
    art = _artifact("ART-wb", archive_capture_at=naive_capture, published_at=None)
    assert synthesis.assign_window(art, windows) == "prior"


def test_assign_window_aware_dates_still_correct():
    """The fix must not change behavior for the (aware) happy path."""
    windows = _windows()
    assert (
        synthesis.assign_window(_artifact("a", published_at=_NOW - timedelta(days=5)), windows)
        == "current"
    )
    assert (
        synthesis.assign_window(_artifact("b", published_at=_NOW - timedelta(days=200)), windows)
        == "prior"
    )
    # Undated live content is current (retrieved now).
    assert synthesis.assign_window(_artifact("c"), windows) == "current"
    # A date older than the comparison window is 'outside' (excluded from both).
    assert (
        synthesis.assign_window(_artifact("d", published_at=_NOW - timedelta(days=800)), windows)
        == "outside"
    )


def test_temporal_baseline_with_naive_dates_does_not_crash():
    """Integration-level reproduction through the exact path the focal mirror's
    synthesis walks: temporal_baseline -> assign_window over a corpus that
    contains a NAIVE-dated artifact. Pre-fix this raised inside the mirror."""
    windows = _windows()
    arts = [
        _artifact(
            "ART-p", published_at=(_NOW - timedelta(days=200)).replace(tzinfo=None)
        ),  # naive prior
        _artifact("ART-c", published_at=_NOW - timedelta(days=3)),  # aware current
    ]
    cls = [
        MarketingClassification(
            classification_id="cls-p",
            artifact_id="ART-p",
            company_id="c1",
            primary_theme="compliance",
        ),
        MarketingClassification(
            classification_id="cls-c",
            artifact_id="ART-c",
            company_id="c1",
            primary_theme="automation",
        ),
    ]
    out = synthesis.temporal_baseline(cls, arts, windows)
    assert out["prior_window"]["n_artifacts"] == 1  # the naive-dated artifact counted, not crashed
    assert out["current_window"]["n_artifacts"] == 1


def test_as_aware_assumes_utc_for_naive():
    naive = datetime(2020, 1, 1, 0, 0, 0)
    assert naive.tzinfo is None
    coerced = synthesis._as_aware(naive)
    assert coerced.tzinfo is UTC
    # Same wall-clock time, now interpreted as UTC.
    assert coerced == datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_as_aware_passthrough_for_aware():
    aware = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    coerced = synthesis._as_aware(aware)
    assert coerced is aware or coerced == aware
    assert coerced.tzinfo is not None
    # An already-aware datetime keeps its own offset (not re-stamped).
    from datetime import timezone

    plus5 = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert synthesis._as_aware(plus5).utcoffset() == timedelta(hours=5)


# ---------------------------------------------------------------------------
# FIX 2 — provider circuit breaker
# ---------------------------------------------------------------------------


def _tool_result(tool_name: str, *, status: str, error_type: str | None = None) -> ToolResult:
    return ToolResult(
        action_id="act-1",
        tool_name=tool_name,
        status=status,
        error_type=error_type,
        retryable=(status == "failed_retryable"),
    )


def test_402_out_of_credits_marks_exa_provider_dead():
    r = _tool_result("exa_search", status="failed_terminal", error_type="provider_out_of_credits")
    assert planner.dead_provider_from_result("exa_search", r) == "exa"
    # Every Exa-backed source shares the fate.
    for src in (
        "exa_agent",
        "similarweb",
        "reviews",
        "jobs",
        "events",
        "ooh",
        "news_market",
        "meta_ads",
    ):
        assert planner.provider_for_source(src) == "exa"


def test_401_auth_marks_provider_dead_and_serpapi_is_its_own_provider():
    r_auth = _tool_result("jobs", status="failed_terminal", error_type="provider_auth")
    assert planner.dead_provider_from_result("jobs", r_auth) == "exa"
    r_serp = _tool_result("google_ads", status="failed_terminal", error_type="provider_auth")
    assert planner.dead_provider_from_result("google_ads", r_serp) == "serpapi"


def test_retryable_and_success_do_not_trip_the_breaker():
    # A 429 rate-limit is retryable — NOT a provider-down signal.
    r429 = _tool_result("exa_search", status="failed_retryable", error_type="rate_limited")
    assert planner.dead_provider_from_result("exa_search", r429) is None
    # A 5xx is retryable too.
    r5xx = _tool_result("exa_search", status="failed_retryable", error_type="provider_5xx")
    assert planner.dead_provider_from_result("exa_search", r5xx) is None
    # A successful call obviously does not trip it.
    ok = _tool_result("exa_search", status="success")
    assert planner.dead_provider_from_result("exa_search", ok) is None


def test_non_provider_tool_is_never_circuit_broken():
    # website_map / webpage_fetch / wayback use public HTTP, not a keyed provider.
    for src in ("wayback", "webpage_fetch", "website_map"):
        assert planner.provider_for_source(src) is None
        r = _tool_result(src, status="failed_terminal", error_type="provider_auth")
        assert planner.dead_provider_from_result(src, r) is None


def _breaker_state() -> tuple[DirectorState, SimpleNamespace]:
    s = DirectorState(
        run_id="R", company_input="deel.com", mode="snapshot", execution_mode="fixture"
    )
    s.company = Company(
        company_id=new_id("CO"),
        canonical_name="Deel",
        primary_domain="deel.com",
        resolved_at=utcnow(),
        resolution_confidence="high",
    )
    s.coverage = initial_coverage()
    s.coverage["pricing_and_packaging"] = "low"
    ctx = SimpleNamespace(config=get_config(), scratch={})
    return s, ctx


def test_propose_actions_skips_dead_provider_but_keeps_others():
    state, ctx = _breaker_state()
    before = planner.propose_actions(state, ctx)
    exa_before = [a for a in before if planner.provider_for_source(a.source_name) == "exa"]
    assert exa_before, "precondition: exa-backed actions are normally proposed"

    state.dead_providers = ["exa"]
    after = planner.propose_actions(state, ctx)
    assert [a for a in after if planner.provider_for_source(a.source_name) == "exa"] == []
    # Non-exa sources (wayback public-fetch, google_ads/serpapi, website_map) survive.
    surviving = {a.source_name for a in after}
    assert "exa_search" not in surviving
    assert surviving, "the planner still proposes non-dead-provider actions"


class _FakeRegistry:
    """Registry stub that returns preset ToolResults in order, ignoring routing."""

    def __init__(self, results: list[ToolResult]) -> None:
        self._results = list(results)
        self.calls = 0

    async def run_action(self, action: ResearchAction, tool_ctx: object) -> ToolResult:
        self.calls += 1
        return self._results.pop(0)


def _exec_ctx(registry: _FakeRegistry) -> SimpleNamespace:
    return SimpleNamespace(
        repository=None,
        trace=None,
        config=None,
        settings=None,
        tool_registry=registry,
        http=None,
        scratch={},
    )


def _exa_action() -> ResearchAction:
    return ResearchAction(
        action_id=new_id("act"),
        action_type="search_exa_web",
        company_id="c1",
        source_name="exa_search",
        parameters={"query": "deel pricing"},
    )


def test_execute_action_402_marks_provider_dead_and_discloses_once():
    state, _ = _breaker_state()
    r402 = _tool_result(
        "exa_search", status="failed_terminal", error_type="provider_out_of_credits"
    )
    r402b = _tool_result(
        "exa_agent", status="failed_terminal", error_type="provider_out_of_credits"
    )
    registry = _FakeRegistry([r402, r402b])
    ctx = _exec_ctx(registry)

    # First terminal 402: exa recorded dead + disclosed once.
    ctx.scratch["selected_action"] = _exa_action()
    asyncio.run(nodes.execute_action(state, ctx))
    assert state.dead_providers == ["exa"]
    disclosures = [o for o in state.negative_observations if "Exa credits exhausted" in o]
    assert len(disclosures) == 1
    assert sum(1 for lim in state.limitations if "Exa credits exhausted" in lim) == 1

    # A SECOND exa 402 (another exa-backed tool) must not double-record or
    # double-disclose — the breaker is already open.
    ctx.scratch["selected_action"] = _exa_action()
    asyncio.run(nodes.execute_action(state, ctx))
    assert state.dead_providers == ["exa"]
    assert len([o for o in state.negative_observations if "Exa credits exhausted" in o]) == 1
    assert sum(1 for lim in state.limitations if "Exa credits exhausted" in lim) == 1


def test_execute_action_retryable_error_does_not_open_breaker():
    state, _ = _breaker_state()
    r429 = _tool_result("exa_search", status="failed_retryable", error_type="rate_limited")
    ctx = _exec_ctx(_FakeRegistry([r429]))
    ctx.scratch["selected_action"] = _exa_action()
    asyncio.run(nodes.execute_action(state, ctx))
    assert state.dead_providers == []
