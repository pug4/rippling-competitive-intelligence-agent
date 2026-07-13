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

# Level-B optional (breadth) action types -> the coverage dimension each one
# primarily serves. Single source of truth: nodes.py derives its optional set
# from this map, and score_and_select uses it for the starvation floor.
LEVEL_B_ACTION_DIMENSIONS: dict[str, str] = {
    "search_reviews": "customer_proof",
    "search_jobs": "personas_and_jobs",
    "search_events": "events",
    "search_ooh": "out_of_home",
    "enrich_similarweb": "commercial_motion",
    "research_linkedin": "public_linkedin",
    "search_linkedin_posts": "public_linkedin",
    "search_google_ads": "paid_media",
    "search_meta_ads": "paid_media",
    "search_linkedin_ads": "paid_media",
}

# Synthetic/analysis sources that a source_allowlist never filters out.
_INTERNAL_SOURCES = frozenset({"reuse_evidence"})

# Starvation floor for never-attempted Level-B sources (reference live trace:
# search_reviews proposed 2x / selected 0, search_linkedin_ads proposed 19x /
# selected 0, while search_wayback was re-proposed 62x). Static utilities rank
# breadth sources at the bottom of a ~30-action queue, so a budget-bounded run
# dies before they get a turn. The floor RAMPS with iteration: Level-A
# collection keeps its early priority, but a never-attempted breadth source
# whose dimension is still thin cannot be starved past mid-run.
_STARVATION_FLOOR_BASE = 1.7
_STARVATION_FLOOR_RAMP = 0.08
_STARVATION_FLOOR_CAP = 2.4

# Peer Similarweb enrichment is bounded to the top-affinity digital
# competitors of the researched company.
_MAX_SIMILARWEB_PEERS = 3

# Seed phrases for a focused keyword-intelligence pass (chat: research
# "keywords"): up to 8 humanized CEP labels, else name + user-focus fallback.
_MAX_KEYWORD_SEEDS = 8

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

MAX_FETCH_URLS_PER_ACTION = 10
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


def _site_discovery_query(company: Any) -> str:
    return f"{company.canonical_name} product OR platform OR solutions OR pricing OR customers OR security"


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


def _attempted_action_types(state: DirectorState) -> set[str]:
    """Action types executed at least once (keys are '<action_type>:<hash>')."""
    return {k.split(":", 1)[0] for k in state.executed_action_keys}


def _similarweb_peer_domains(state: DirectorState, ctx: Any) -> list[str]:
    """Top-affinity peer domains from this run's own Similarweb artifact.

    Reads the stored (non-peer) similarweb artifact's capability-checked
    ``metrics.digital_competitors`` — never synthesizes peers. Cached in
    ``ctx.scratch`` once found; the repository fallback keeps resumed runs
    (fresh scratch) working."""
    if state.company is None:
        return []
    scratch = getattr(ctx, "scratch", None)
    key = f"similarweb_peers:{state.company.company_id}"
    if scratch is not None and key in scratch:
        return list(scratch[key])
    repo = getattr(ctx, "repository", None)
    if repo is None:
        return []
    try:
        artifacts = repo.list_artifacts(run_id=state.run_id)
    except Exception:  # storage hiccups must never break planning
        return []
    own = state.company.primary_domain.lower().removeprefix("www.")
    ranked: list[tuple[float, str]] = []
    for artifact in artifacts:
        meta = getattr(artifact, "metadata", None) or {}
        if getattr(artifact, "source_type", "") != "similarweb" or meta.get("peer"):
            continue
        competitors = ((meta.get("metrics") or {}).get("digital_competitors") or {}).get("value")
        for item in competitors or []:
            if isinstance(item, str):
                domain, affinity = item, 0.0
            elif isinstance(item, dict):
                domain = str(item.get("domain") or "")
                try:
                    affinity = float(item.get("affinity") or 0.0)
                except (TypeError, ValueError):
                    affinity = 0.0
            else:
                continue
            domain = domain.strip().lower().removeprefix("www.")
            if domain and domain != own:
                ranked.append((affinity, domain))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    peers: list[str] = []
    for _, domain in ranked:
        if domain not in peers:
            peers.append(domain)
    peers = peers[:_MAX_SIMILARWEB_PEERS]
    if peers and scratch is not None:
        scratch[key] = peers
    return peers


def _keyword_seed_phrases(state: DirectorState, ctx: Any) -> list[str]:
    """Seed phrases for a focused enrich_keywords pass (bounded, real).

    Prefers up to :data:`_MAX_KEYWORD_SEEDS` humanized category-entry-point
    labels from this run's stored classifications (observed buying triggers —
    the phrases worth looking up); falls back to the competitor's name plus
    the user's focus terms so the pass always has something real to research.
    Never synthesizes phrases.
    """
    phrases: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        text = " ".join(str(raw or "").replace("_", " ").split())
        key = text.casefold()
        if text and key not in seen and len(phrases) < _MAX_KEYWORD_SEEDS:
            seen.add(key)
            phrases.append(text)

    repo = getattr(ctx, "repository", None)
    if repo is not None:
        try:
            for model in repo.list_classifications(state.run_id):
                for cep in getattr(model, "category_entry_points", None) or []:
                    _add(cep)
        except Exception:  # storage hiccups must never break planning
            pass
    if not phrases and state.company is not None:
        _add(state.company.canonical_name)
        for term in getattr(state, "user_focus", None) or []:
            _add(term)
    return phrases


def _diversify_pending(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-robin score-descending pages across categories so no single
    high-scoring category (e.g. dozens of /solutions/* pages) floods the fetch
    budget and starves pricing/customers/platform (audit: per-category quota)."""
    from collections import OrderedDict

    buckets: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for p in pending:  # pending is already score-descending
        buckets.setdefault(str(p.get("category", "other")), []).append(p)
    out: list[dict[str, Any]] = []
    while any(buckets.values()):
        for cat in list(buckets):
            if buckets[cat]:
                out.append(buckets[cat].pop(0))
    return out


def propose_actions(state: DirectorState, ctx: Any) -> list[ResearchAction]:
    """Rule-driven proposals for the current coverage state."""
    if state.company is None:
        return []
    company = state.company

    # Reuse mode (retry: reanalyze_same_evidence / challenge_conclusion): do not
    # collect new evidence. Propose a single reuse action to pull the parent's
    # artifacts into this run once; afterwards propose nothing so the loop moves
    # straight to synthesis on the reused corpus.
    if state.reuse_evidence_only:
        # The dedup key MUST match the params the action carries, or execute's
        # recorded key won't match and reuse would be re-proposed every iteration
        # (re-classifying the same artifacts to the iteration cap).
        reuse_params = {"parent_run_id": state.parent_run_id}
        reuse_key = action_key("reuse_evidence", reuse_params)
        if reuse_key in state.executed_action_keys or not state.parent_run_id:
            return []
        return [
            _mk(
                state,
                "reuse_evidence",
                "reuse_evidence",
                "current_website",
                reuse_params,
                "Retry reuse mode: re-analyze the parent run's evidence without new collection.",
                reliability=1.0,
                cost=0.0,
                latency=0.0,
            )
        ]
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

    collection = cfg.collection if cfg else {}
    max_pages = int(collection.get("max_fetched_pages", MAX_TOTAL_FETCHED_PAGES))
    threshold = float(collection.get("fetch_score_threshold", FETCH_SCORE_THRESHOLD))

    # 1b. Deep site discovery via Exa (user directive: characterize the WHOLE
    # site, not a sliver). A domain-scoped Exa search surfaces product /
    # solution / customer / security / integration pages the sitemap or homepage
    # anchors miss; discovered URLs are merged into the fetch queue downstream.
    if collection.get("deep_crawl", True):
        discovered_key = f"site_discovery_done:{company.company_id}"
        already_ran = action_key(
            "search_exa_web",
            {
                "query": _site_discovery_query(company),
                "num_results": 0,
                "_purpose": "site_discovery",
            },
        )
        if not ctx.scratch.get(discovered_key) and already_ran not in state.executed_action_keys:
            proposals.append(
                _mk(
                    state,
                    "search_exa_web",
                    "exa_search",
                    "product_positioning",
                    {
                        "query": _site_discovery_query(company),
                        "num_results": int(collection.get("exa_site_discovery_results", 10)),
                        "include_domains": [company.primary_domain],
                        "_purpose": "site_discovery",
                    },
                    "Deep site discovery: domain-scoped Exa search to characterize the whole site, not just top nav.",
                    reliability=0.6,
                )
            )

    page_map = ctx.scratch.get(f"page_map:{company.company_id}") or []

    # 2. Fetch the highest-priority unfetched pages once a map exists. Priority
    # pages (score >= threshold) up to a total cap; a lower threshold + higher
    # cap (config `collection`) collect product/solution/use-case pages for
    # whole-site coverage rather than a handful.
    if page_map:
        fetched: set[str] = set(ctx.scratch.get(f"fetched_urls:{company.company_id}", []))
        real_fetched = {u for u in fetched if "sitemap" not in u}
        pending = _diversify_pending(
            [
                p
                for p in page_map
                if p["url"] not in fetched and float(p.get("score", 0)) >= threshold
            ]
        )
        under_cap = len(real_fetched) < max_pages
        # Fetch toward the CONFIGURED cap while priority pages remain — the cap
        # is the coverage control (audit: the old need_dims gate stopped at 6
        # pages despite a 48-page budget once coverage dims were satisfied).
        # need_dims stays as a priority signal via _IMPORTANCE, not a gate.
        if pending and under_cap:
            batch = [p["url"] for p in pending[:MAX_FETCH_URLS_PER_ACTION]]
            proposals.append(
                _mk(
                    state,
                    "fetch_webpage",
                    "webpage_fetch",
                    "current_product",
                    {"urls": batch, "source_type": "webpage"},
                    f"Priority pages unfetched ({len(real_fetched)} fetched so far, cap {max_pages}); "
                    "current-site coverage below target.",
                    latency=10.0,
                )
            )

    # 3. Historical evidence for longitudinal/comparative modes. Temporal depth
    # (priority: time-based accuracy) needs MORE than one homepage snapshot:
    # sample several homepage captures across the window AND archive the key
    # positioning pages (platform/pricing/product) so change detection has both
    # periods on comparable surfaces.
    if state.mode in ("longitudinal", "comparative"):
        comparison = next((w for w in state.time_windows if w.purpose == "comparison"), None)
        if comparison is not None:
            # Snapshot counts + extra positioning pages are config-tunable so a
            # deeper temporal run (priority #2) can sample finer-grained history
            # without a code change. Defaults preserve prior behavior.
            hist_cfg = (cfg.collection.get("historical", {}) if cfg else {}) or {}
            home_snaps = int(hist_cfg.get("homepage_snapshots", 4))
            pos_snaps = int(hist_cfg.get("positioning_snapshots", 2))
            hist_targets = [
                ("/", "homepage", home_snaps),
                ("/platform", "platform", pos_snaps),
                ("/pricing", "pricing", pos_snaps),
            ]
            for extra in hist_cfg.get("extra_pages", []) or []:
                path = extra if str(extra).startswith("/") else f"/{extra}"
                hist_targets.append((path, path.strip("/") or "page", pos_snaps))
            for path, label, snaps in hist_targets:
                dim = "historical_website" if path == "/" else "historical_messages"
                if not _needs(state, dim, "medium") and path != "/":
                    continue
                proposals.append(
                    _mk(
                        state,
                        "search_wayback",
                        "wayback",
                        "historical_website",
                        {
                            "url": f"https://www.{company.primary_domain}{path}",
                            "window_start": comparison.start_at.date().isoformat(),
                            "window_end": comparison.end_at.date().isoformat(),
                            "max_snapshots": snaps,
                        },
                        f"Temporal depth: archive the {label} across the prior window so change "
                        "detection has both periods on a comparable surface.",
                        reliability=0.6,
                        latency=15.0,
                        fallbacks=["search_news_launches"],
                    )
                )
            # Prior-window sampling from Exa's index (start+end published dates):
            # real published-in-window content/news — a second temporal source
            # beyond Wayback's sparse archive. Retrieval only; Claude classifies.
            if _needs(state, "historical_messages", "medium"):
                proposals.append(
                    _mk(
                        state,
                        "search_exa_web",
                        "exa_search",
                        "historical_messages",
                        {
                            "query": f'"{company.canonical_name}" product OR platform OR pricing OR launch',
                            "num_results": 6,
                            "start_published_date": comparison.start_at.date().isoformat(),
                            "end_published_date": comparison.end_at.date().isoformat(),
                        },
                        "Prior-window Exa sampling (published-date bounded) adds real "
                        "comparison-period evidence beyond the Wayback archive.",
                        reliability=0.55,
                    )
                )

    # 3b. Exa-crawler fallback for priority pages whose DIRECT fetch failed with
    # an HTTP error (anti-bot / JS pages). Robots-disallowed pages are excluded —
    # we respect robots on competitor sites; this recovers fetch FAILURES.
    failed_urls = []
    for obs in state.negative_observations:
        if company.primary_domain not in obs:
            continue
        if "robots disallowed" in obs:
            continue
        if ": HTTP " in obs or ": fetch failed" in obs:
            url = obs.split(": ")[0].strip()
            if url.startswith("http"):
                failed_urls.append(url)
    if failed_urls:
        proposals.append(
            _mk(
                state,
                "fetch_via_exa",
                "exa_contents",
                "current_website",
                {"urls": sorted(set(failed_urls))[:8], "source_type": "webpage"},
                "Exa-crawler fallback: recover priority pages whose direct fetch failed "
                "(JS-rendered or anti-bot) — retrieval only, provenance labeled.",
                reliability=0.6,
            )
        )

    # 4. News and launches (Exa category=news sharpens retrieval; additional
    # query variants widen the sweep in ONE call).
    if _needs(state, "news_and_launches"):
        proposals.append(
            _mk(
                state,
                "search_news_launches",
                "exa_search",
                "news_and_launches",
                {
                    "query": f'"{company.canonical_name}" product launch OR announcement OR press release',
                    "category": "news",
                    "additional_queries": [
                        f'"{company.canonical_name}" new product feature release',
                        f'"{company.canonical_name}" funding OR acquisition OR partnership',
                    ],
                    "num_results": 5,
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
                    "num_results": 5,
                },
                "No competitive-stance evidence collected; comparison pages are the strongest public source (§18.2).",
                reliability=0.65,
            )
        )

    # 6. Pricing fallback chain (feedback #6/#34): a required dimension must
    # exhaust its public fallbacks before the loop may stop. Direct pages are
    # already seeded in the map; here we add Exa and Wayback pricing fallbacks.
    if _needs(state, "pricing_and_packaging"):
        proposals.append(
            _mk(
                state,
                "search_exa_web",
                "exa_search",
                "pricing_and_packaging",
                {
                    "query": f'"{company.canonical_name}" pricing OR plans OR "per employee" cost',
                    "num_results": 5,
                    "include_domains": [company.primary_domain],
                },
                "Pricing is a required dimension and not yet covered; searching for a public pricing/plans page.",
                reliability=0.55,
            )
        )
        comparison = next((w for w in state.time_windows if w.purpose == "comparison"), None)
        proposals.append(
            _mk(
                state,
                "search_wayback",
                "wayback",
                "pricing_and_packaging",
                {
                    "url": f"https://www.{company.primary_domain}/pricing",
                    "window_start": (
                        comparison.start_at.date().isoformat() if comparison else None
                    ),
                    "window_end": (comparison.end_at.date().isoformat() if comparison else None),
                    "max_snapshots": 1,
                },
                "Pricing fallback: an archived pricing/plans page can establish pricing disclosure even if the live page is gated.",
                reliability=0.5,
                latency=15.0,
            )
        )

    # 7. Level-B optional sources (feature-flagged, non-blocking). Proposed once
    # each when their coverage dimension is thin and the source is enabled; a
    # disabled or failing source simply never contributes (the boundary returns
    # a typed skipped/unsupported result).
    flags = cfg.sources if cfg else {}
    name = company.canonical_name

    def _optional(flag, source, action, dim, params, rationale, rel=0.5):
        # Optional sources are attempted once each: allowed() filters executed
        # keys, so an enabled source is proposed until its single attempt runs.
        # Deliberately NOT gated on the dimension's coverage level (starvation
        # audit: a fetched /customers page raised customer_proof to medium and
        # search_reviews stopped being proposed after 2 iterations — dedicated
        # buyer-voice/ads sources must not be starved by INCIDENTAL coverage
        # from another source). A per-run opt-out (e.g. the UI's LinkedIn
        # toggle) beats the global flag.
        if flag in (getattr(state, "disabled_sources", None) or []):
            return
        if flags.get(flag):
            proposals.append(_mk(state, action, source, dim, params, rationale, reliability=rel))

    _optional(
        "similarweb",
        "similarweb",
        "enrich_similarweb",
        "commercial_motion",
        {"domain": company.primary_domain},
        "Similarweb (estimated) traffic and channel mix add a demand-side view.",
    )
    # LinkedIn / social presence via the Exa Agent (agentic research). The plain
    # search never reaches LinkedIn; this surfaces how the company + its
    # employees position the product publicly, as a cited synthesis.
    _linkedin_slug = company.primary_domain.split(".")[0]
    _num_posts = int((cfg.exa_agent.get("linkedin_num_posts", 15)) if cfg else 15)
    _optional(
        "exa_linkedin",
        "exa_agent",
        "research_linkedin",
        "public_linkedin",
        {
            "company": name,
            "domain": company.primary_domain,
            "linkedin_url": f"https://www.linkedin.com/company/{_linkedin_slug}",
            "num_posts": _num_posts,
        },
        "Exa Agent researches the competitor's LinkedIn: per-post employee content + synthesis.",
    )
    # Complementary discovery: LinkedIn-scoped Exa search (one artifact per post).
    _optional(
        "exa_linkedin",
        "exa_search",
        "search_linkedin_posts",
        "public_linkedin",
        {
            # Domain scoping does the targeting; Exa's current category list has
            # no LinkedIn value (an invalid category 400s the request).
            "query": f'"{name}" LinkedIn post OR update from employees about product, hiring, or launches',
            "include_domains": ["linkedin.com"],
            "num_results": _num_posts,
        },
        "LinkedIn-scoped Exa search surfaces individual employee/company posts as their own artifacts.",
    )
    _optional(
        "reviews",
        "reviews",
        "search_reviews",
        "customer_proof",
        {"company": name, "num_results": 4},
        "Review-site buyer language surfaces pains and objections (non-representative).",
    )
    _optional(
        "jobs",
        "jobs",
        "search_jobs",
        "commercial_motion",
        {"company": name, "num_results": 4},
        "Job postings are leading indicators of GTM motion and segment focus.",
    )
    _optional(
        "events",
        "events",
        "search_events",
        "events",
        {"company": name, "num_results": 4},
        "Conference/event presence is a discoverable brand-investment signal.",
    )
    _optional(
        "ooh",
        "ooh",
        "search_ooh",
        "out_of_home",
        {"company": name, "num_results": 4},
        "OOH discovery (low coverage by nature) can reveal category-building spend.",
    )
    _optional(
        "google_ads",
        "google_ads",
        "search_google_ads",
        "paid_media",
        {"advertiser": name, "domain": company.primary_domain},
        "Google Ads Transparency shows observed public creatives (no performance).",
    )
    # Meta / LinkedIn ad libraries: named in the assignment. Live commercial-ad
    # APIs don't exist publicly (§39.7) so these degrade to a typed
    # unsupported/skip — but the loop must PROPOSE them so the attempt and its
    # honest limitation are visible in the trace (audit: previously dead code —
    # never proposed at all).
    _optional(
        "meta_ads",
        "meta_ads",
        "search_meta_ads",
        "paid_media",
        {"advertiser": name, "domain": company.primary_domain},
        "Meta Ad Library attempt: US commercial ads are UI-only; typed skip when unreachable.",
    )
    _optional(
        "linkedin_ads",
        "linkedin_ads",
        "search_linkedin_ads",
        "paid_media",
        {"advertiser": name, "domain": company.primary_domain},
        "LinkedIn Ad Library attempt: API is political/EU-only; typed skip when unreachable.",
    )

    # 7b. Per-competitor Similarweb peers: once this run's own similarweb
    # artifact reports digital_competitors, enrich up to the top-3 affinity
    # peer domains (params carry peer=true; the resulting artifacts are stamped
    # metadata.peer=true downstream so the report's competitor view is
    # untouched). allowed() dedupes executed peers; each runs once.
    if flags.get("similarweb") and "similarweb" not in (
        getattr(state, "disabled_sources", None) or []
    ):
        for peer_domain in _similarweb_peer_domains(state, ctx):
            proposals.append(
                _mk(
                    state,
                    "enrich_similarweb",
                    "similarweb",
                    "commercial_motion",
                    {"domain": peer_domain, "peer": True},
                    f"Similarweb peer enrichment: {peer_domain} is a top-affinity digital "
                    "competitor — estimated demand context for the audience-overlap set.",
                )
            )

    # 7c. Keyword intelligence (source "keywords") — proposed ONLY when a
    # focused research pass explicitly allowlists it (chat: research
    # "keywords"). Default batch runs (allowlist None) NEVER propose it: the
    # paid-search draft owns default-run enrichment, and an unfocused pass
    # would burn bounded provider calls on guesses. Proposed once per pass:
    # keyword artifacts are themselves classifiable, so seed drift could
    # otherwise re-propose (and re-spend) every iteration; a new "keywords"
    # research pass clears the executed keys (conversation._reopen_sources)
    # and re-arms it.
    keywords_allowlist = getattr(state, "source_allowlist", None)
    if (
        keywords_allowlist is not None
        and "keywords" in keywords_allowlist
        and flags.get("keywords")
        and not any(k.split(":", 1)[0] == "enrich_keywords" for k in state.executed_action_keys)
    ):
        seeds = _keyword_seed_phrases(state, ctx)
        if seeds:
            proposals.append(
                _mk(
                    state,
                    "enrich_keywords",
                    "keywords",
                    "commercial_motion",
                    {"keywords": seeds},
                    "Focused keyword research: enrich the observed buying triggers with "
                    "live SERP intelligence / provider keyword metrics.",
                    reliability=0.6,
                )
            )

    out = [p for p in proposals if allowed(p)]

    # Source allowlist (CONTRACTS.md): when set (e.g. focused in-place research),
    # ONLY actions from allowlisted sources are proposed. This is an additional
    # filter over the FULL proposal list; internal/analysis actions (e.g.
    # reuse_evidence) always stay allowed.
    allowlist = getattr(state, "source_allowlist", None)
    if allowlist is not None:
        permitted = set(allowlist) | set(_INTERNAL_SOURCES)
        out = [p for p in out if (p.source_name or "") in permitted]
    return out


# Required dimensions whose fallbacks must be exhausted before a low-value stop.
def required_dims_needing_exhaustion(state: DirectorState) -> list[str]:
    from .coverage import level_at_least, required_dimensions

    # Reuse mode never collects, so there is nothing to "exhaust" — reused
    # evidence either establishes a dimension or it doesn't; don't force fallbacks.
    if state.reuse_evidence_only:
        return []
    out = []
    for d in required_dimensions(state.mode, state.focal_company is not None):
        level = state.coverage.get(d, "not_attempted")
        if level == "unavailable":
            continue
        if not level_at_least(state.coverage, d, "medium"):
            out.append(d)
    return out


def score_and_select(
    state: DirectorState, proposals: list[ResearchAction]
) -> tuple[ResearchAction | None, list[dict[str, Any]]]:
    """Utility ordering (§37.16) with a natural-language selection trace.

    Never-attempted Level-B sources whose coverage dimension is still thin get
    an iteration-ramped utility floor: early cycles keep the Level-A ordering,
    but a breadth source cannot sit at the bottom of the queue until the
    budget dies (starvation rebalance — see _STARVATION_FLOOR_* above)."""
    if not proposals:
        return None, []
    from .coverage import level_at_least

    attempted = _attempted_action_types(state)
    floor = min(
        _STARVATION_FLOOR_BASE + _STARVATION_FLOOR_RAMP * state.iteration,
        _STARVATION_FLOOR_CAP,
    )
    scored = []
    for a in proposals:
        utility = (
            a.strategic_importance * 2.0
            + a.expected_reliability
            - min(a.estimated_cost_usd / 0.05, 1.0) * 0.2
            - min(a.estimated_latency_seconds / 30.0, 1.0) * 0.2
        )
        dim = LEVEL_B_ACTION_DIMENSIONS.get(a.action_type)
        if (
            dim is not None
            and a.action_type not in attempted
            and state.coverage.get(dim) != "unavailable"
            # "thin" is below HIGH: incidental coverage from another source
            # (e.g. a /customers page raising customer_proof to medium) must
            # not permanently starve the never-attempted dedicated source.
            and not level_at_least(state.coverage, dim, "high")
        ):
            utility = max(utility, floor)
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
