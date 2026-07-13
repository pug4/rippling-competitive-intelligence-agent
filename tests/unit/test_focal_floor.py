"""Focal-mirror minimum-corpus FLOOR (comparison.run_focal_mirror + nodes).

Regression: a comparative run collected 151 competitor pages while the focal
mirror stopped at only 15 once the LLM planner judged the snapshot "complete" —
share-normalization is noisy at n=15, the asymmetry banner fires, and a
30+-product focal company (Rippling) is badly misrepresented. The floor keeps
the mirror crawling until its corpus is comparable to the competitor's (or the
site's fetchable pages are exhausted), while the hard budget/runtime/iteration/
tool-call caps stay the backstops and a floor of 0 (every normal run) is a no-op.

Additions-only unit tests: states are crafted directly (no full run, no network,
no keys).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from competitive_agent import planner
from competitive_agent.graph import GraphContext
from competitive_agent.nodes import _focal_corpus_below_floor, decide_continue_or_stop
from competitive_agent.schemas.company import Company
from competitive_agent.state import DirectorState


@pytest.fixture(autouse=True)
def _hermetic_keys(monkeypatch):
    # Zero-key, deterministic: never read real secrets, never hit the network.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")


class _Cfg:
    """Minimal AppConfig stand-in for direct planner/node calls (fully hermetic —
    no config file, no env). deep_crawl off keeps the proposal set to the
    site-collection + required-fallback actions the floor reasons about."""

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


# One classified snapshot page per merged/message classification (1:1). The focal
# MIRROR is a snapshot run whose focal_company is None (it never mirrors itself);
# run_focal_mirror is the only place that sets focal_min_pages > 0.
_BASE_DIMS = (
    "identity",
    "current_website",
    "current_product",
    "pricing_and_packaging",
    "news_and_launches",
    "portfolio_discovery",
    "product_positioning",
    "commercial_motion",
)

# A single high-score unfetched product page -> propose_actions surfaces a
# fetch_webpage (a site-collection action) that the floor can keep pulling.
_PENDING_PAGE = [
    {"url": "https://www.rippling.com/hr", "path": "/hr", "category": "product", "score": 0.9}
]


def _company() -> Company:
    return Company(
        company_id="CO-focal",
        canonical_name="Rippling",
        primary_domain="rippling.com",
        resolved_at=datetime.now(UTC),
        resolution_confidence="high",
    )


def _covered() -> dict[str, str]:
    # Every snapshot-required dimension satisfied -> coverage sufficient and no
    # required dimension still needs its public fallbacks exhausted.
    return dict.fromkeys(_BASE_DIMS, "high")


def _mirror_state(*, focal_min_pages: int, classified: int) -> DirectorState:
    state = DirectorState(
        run_id="RUN-mirror",
        company_input="rippling.com",
        mode="snapshot",
        execution_mode="fixture",
        focal_min_pages=focal_min_pages,
    )
    state.company = _company()
    state.coverage = _covered()
    # classification_ids holds one merged classification id per classified page.
    state.classification_ids = [f"C{i}" for i in range(classified)]
    return state


def _ctx(*, page_map: list | None = None) -> GraphContext:
    ctx = GraphContext(repository=None, trace=None, config=_Cfg(), settings=None)
    if page_map is not None:
        ctx.scratch["page_map:CO-focal"] = page_map
        ctx.scratch["fetched_urls:CO-focal"] = []
    return ctx


# ---------------------------------------------------------------------------
# _focal_corpus_below_floor
# ---------------------------------------------------------------------------


def test_below_floor_true_when_pages_short_and_fetch_action_remains():
    state = _mirror_state(focal_min_pages=50, classified=10)
    ctx = _ctx(page_map=_PENDING_PAGE)
    # Sanity: a fetch_webpage really is among the proposals (more focal pages).
    assert "fetch_webpage" in {a.action_type for a in planner.propose_actions(state, ctx)}
    assert _focal_corpus_below_floor(state, ctx) is True


def test_below_floor_false_when_floor_zero_normal_run():
    # Every non-mirror run keeps focal_min_pages == 0: a no-op even with a tiny
    # corpus and a fetchable page still queued.
    state = _mirror_state(focal_min_pages=0, classified=1)
    ctx = _ctx(page_map=_PENDING_PAGE)
    assert _focal_corpus_below_floor(state, ctx) is False


def test_below_floor_false_when_corpus_meets_floor():
    state = _mirror_state(focal_min_pages=40, classified=40)
    ctx = _ctx(page_map=_PENDING_PAGE)
    assert _focal_corpus_below_floor(state, ctx) is False


def test_below_floor_false_when_site_exhausted():
    # Below the floor, but no map (current_website covered) and no page_map means
    # no fetch: the focal site's fetchable pages are exhausted, so stopping is
    # honest and the floor must NOT keep the loop alive.
    state = _mirror_state(focal_min_pages=50, classified=10)
    ctx = _ctx()  # no page_map queued
    types = {a.action_type for a in planner.propose_actions(state, ctx)}
    assert "fetch_webpage" not in types and "map_current_website" not in types
    assert _focal_corpus_below_floor(state, ctx) is False


# ---------------------------------------------------------------------------
# decide_continue_or_stop
# ---------------------------------------------------------------------------


def test_decide_defers_stop_when_below_floor_but_stops_at_floor_zero():
    # The model judged the snapshot "complete" (the exact bug trigger).
    below = _mirror_state(focal_min_pages=50, classified=10)
    ctx = _ctx(page_map=_PENDING_PAGE)
    ctx.scratch["model_requested_stop"] = True
    state, nxt = asyncio.run(decide_continue_or_stop(below, ctx))
    assert nxt == "assess_coverage"  # floor defers the model "complete" stop
    assert state.stop_reason is None

    # SAME setup, floor disabled (every normal run): the model stop is honored.
    normal = _mirror_state(focal_min_pages=0, classified=10)
    ctx0 = _ctx(page_map=_PENDING_PAGE)
    ctx0.scratch["model_requested_stop"] = True
    state0, nxt0 = asyncio.run(decide_continue_or_stop(normal, ctx0))
    assert nxt0 == "render_outputs"
    assert state0.stop_reason is not None
    assert state0.stop_reason.startswith("model_judged_complete")


def test_hard_caps_still_stop_below_floor():
    # Budget exhausted: stops despite being below the floor with a fetch queued.
    budget = _mirror_state(focal_min_pages=50, classified=10)
    budget.budget_usd = 5.0
    budget.spent_usd = 100.0
    _, nxt = asyncio.run(decide_continue_or_stop(budget, _ctx(page_map=_PENDING_PAGE)))
    assert nxt == "render_outputs"
    assert budget.stop_reason == "budget_exhausted"

    # Iteration cap: same — the floor only defers the "complete" stops.
    iters = _mirror_state(focal_min_pages=50, classified=10)
    iters.max_iterations = 1
    iters.iteration = 1  # -> 2 after the in-node increment, >= cap
    _, nxt2 = asyncio.run(decide_continue_or_stop(iters, _ctx(page_map=_PENDING_PAGE)))
    assert nxt2 == "render_outputs"
    assert iters.stop_reason == "max_iterations_reached"
