"""Deterministic action proposal and scoring (§37.16).

Obvious actions come from rules over coverage state; a reasoning model is only
consulted when rules tie or the open question is semantically complex (later
phase). Every selection emits a §37.16 trace with rationale and alternatives.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schemas.common import new_id
from .schemas.source import ResearchAction
from .state import DirectorState

# Strategic importance per coverage dimension the action primarily serves.
_IMPORTANCE = {
    "current_website": 1.0,
    "current_product": 0.95,
    "pricing_and_packaging": 0.9,
    "news_and_launches": 0.7,
    "competitive_stance": 0.75,
    "historical_website": 0.85,
    "historical_messages": 0.8,
    "customer_proof": 0.6,
}

MAX_FETCH_URLS_PER_ACTION = 6
# Only fetch pages scored as genuine priority pages (pricing/product/platform/
# comparison/customers/home ≈ 0.4+). Blog/other pages (0.1) are never worth a
# model-classification budget and would loop forever on a large site.
FETCH_SCORE_THRESHOLD = 0.4
# Hard cap on pages fetched per company — enough to characterize positioning,
# pricing, and products without drowning in a large site's long tail.
MAX_TOTAL_FETCHED_PAGES = 12
# Fetch at least this many pages before moving on, even if the coarse coverage
# dimensions already read "sufficient" from the homepage alone.
MIN_PAGES_BEFORE_MOVING_ON = 5


def action_key(action_type: str, parameters: dict[str, Any]) -> str:
    canon = json.dumps(parameters, sort_keys=True, default=str)
    return f"{action_type}:{hashlib.sha256(canon.encode()).hexdigest()[:12]}"


def _mk(
    state: DirectorState,
    action_type: str,
    source_name: str,
    dimension: str,
    parameters: dict[str, Any],
    rationale: str,
    reliability: float = 0.8,
    cost: float = 0.01,
    latency: float = 5.0,
    fallbacks: list[str] | None = None,
) -> ResearchAction:
    return ResearchAction(
        action_id=new_id("ACT"),
        action_type=action_type,
        company_id=state.company.company_id if state.company else "unresolved",
        time_window_ids=[w.window_id for w in state.time_windows],
        source_name=source_name,
        parameters=parameters,
        rationale=rationale,
        expected_information_gain=1.0,  # refined by score_actions
        strategic_importance=_IMPORTANCE.get(dimension, 0.5),
        expected_reliability=reliability,
        estimated_cost_usd=cost,
        estimated_latency_seconds=latency,
        fallback_action_types=fallbacks or [],
    )


def _needs(state: DirectorState, dimension: str, minimum: str = "medium") -> bool:
    from .coverage import level_at_least

    level = state.coverage.get(dimension, "not_attempted")
    if level == "unavailable":
        return False
    return not level_at_least(state.coverage, dimension, minimum)


def _too_many_failures(state: DirectorState, source: str, action_type: str, cap: int) -> bool:
    rec = state.failed_actions.get(f"{source}:{action_type}")
    return bool(rec and rec.attempts >= cap)


def propose_actions(state: DirectorState, ctx: Any) -> list[ResearchAction]:
    """Rule-driven proposals for the current coverage state."""
    if state.company is None:
        return []
    company = state.company
    cfg = ctx.config
    cap = int(cfg.budgets.get("max_retries_per_source", 2)) if cfg else 2
    proposals: list[ResearchAction] = []

    def allowed(a: ResearchAction) -> bool:
        if action_key(a.action_type, a.parameters) in state.executed_action_keys:
            return False
        if _too_many_failures(state, a.source_name or "", a.action_type, cap):
            return False
        return True

    # 1. Map the site before anything else.
    if _needs(state, "current_website"):
        proposals.append(
            _mk(
                state,
                "map_current_website",
                "website_map",
                "current_website",
                {"domain": company.primary_domain},
                "No current-website map exists; page mapping precedes crawling (§16.1).",
            )
        )

    page_map = ctx.scratch.get(f"page_map:{company.company_id}") or []

    # 2. Fetch the highest-priority unfetched pages once a map exists. Only
    # PRIORITY pages (score above threshold) are eligible, and only up to a
    # total page cap: a large site has dozens of product/solution pages with
    # diminishing marginal signal, so we stop after enough are classified and
    # move on to historical / news / comparison sources ("stop when incremental
    # information gain is low", §5.1).
    if page_map:
        fetched: set[str] = set(ctx.scratch.get(f"fetched_urls:{company.company_id}", []))
        real_fetched = {u for u in fetched if "sitemap" not in u}
        pending = [
            p
            for p in page_map
            if p["url"] not in fetched and float(p.get("score", 0)) >= FETCH_SCORE_THRESHOLD
        ]
        # Page fetches serve current-site dimensions; competitive stance and
        # customer proof are better served by targeted Exa search below.
        need_dims = [
            d for d in ("current_product", "pricing_and_packaging") if _needs(state, d)
        ]
        under_cap = len(real_fetched) < MAX_TOTAL_FETCHED_PAGES
        if pending and (need_dims or len(real_fetched) < MIN_PAGES_BEFORE_MOVING_ON) and under_cap:
            batch = [p["url"] for p in pending[:MAX_FETCH_URLS_PER_ACTION]]
            proposals.append(
                _mk(
                    state,
                    "fetch_webpage",
                    "webpage_fetch",
                    "current_product",
                    {"urls": batch, "source_type": "webpage"},
                    f"Priority pages unfetched ({len(real_fetched)} fetched so far, "
                    f"cap {MAX_TOTAL_FETCHED_PAGES}); current-site coverage below target.",
                    latency=10.0,
                )
            )

    # 3. Historical evidence for longitudinal/comparative modes.
    if state.mode in ("longitudinal", "comparative") and _needs(state, "historical_website"):
        comparison = next((w for w in state.time_windows if w.purpose == "comparison"), None)
        if comparison is not None:
            proposals.append(
                _mk(
                    state,
                    "search_wayback",
                    "wayback",
                    "historical_website",
                    {
                        "url": f"https://www.{company.primary_domain}/",
                        "window_start": comparison.start_at.date().isoformat(),
                        "window_end": comparison.end_at.date().isoformat(),
                        "max_snapshots": 2,
                    },
                    "A temporal claim needs prior-period evidence; the historical homepage is the highest-value missing source.",
                    reliability=0.6,
                    latency=15.0,
                    fallbacks=["search_news_launches"],
                )
            )

    # 4. News and launches.
    if _needs(state, "news_and_launches"):
        proposals.append(
            _mk(
                state,
                "search_news_launches",
                "exa_search",
                "news_and_launches",
                {
                    "query": f'"{company.canonical_name}" product launch OR announcement OR press release',
                    "num_results": 8,
                    "start_published_date": (
                        state.time_windows[-1].start_at.date().isoformat()
                        if state.time_windows
                        else None
                    ),
                    "exclude_domains": [company.primary_domain],
                },
                "Launch and press evidence is missing for the current period.",
                reliability=0.7,
            )
        )

    # 5. Comparison pages / competitive stance.
    if _needs(state, "competitive_stance"):
        proposals.append(
            _mk(
                state,
                "search_comparison_pages",
                "exa_search",
                "competitive_stance",
                {
                    "query": f'"{company.canonical_name}" vs OR versus OR alternative comparison',
                    "num_results": 6,
                },
                "No competitive-stance evidence collected; comparison pages are the strongest public source (§18.2).",
                reliability=0.65,
            )
        )

    return [p for p in proposals if allowed(p)]


def score_and_select(
    state: DirectorState, proposals: list[ResearchAction]
) -> tuple[ResearchAction | None, list[dict[str, Any]]]:
    """Utility ordering (§37.16) with a natural-language selection trace."""
    if not proposals:
        return None, []
    scored = []
    for a in proposals:
        utility = (
            a.strategic_importance * 2.0
            + a.expected_reliability
            - min(a.estimated_cost_usd / 0.05, 1.0) * 0.2
            - min(a.estimated_latency_seconds / 30.0, 1.0) * 0.2
        )
        scored.append((utility, a))
    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0][1]
    trace = [
        {
            "action_type": a.action_type,
            "source": a.source_name,
            "utility": round(u, 3),
            "rationale": a.rationale,
        }
        for u, a in scored
    ]
    return best, trace
