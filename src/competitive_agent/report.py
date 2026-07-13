"""Deterministic report rendering: Markdown brief + JSON package + trace.

Both renderers consume ONLY validated stored records (state + repository) — the
React UI consumes the same JSON package, never separate logic (§40.6). The
Markdown leads with an executive summary and a Rippling-first Action Board,
labels every rating with the evidence behind it, states what the evidence shows
(never converting a proof gap into a capability claim), and collapses empty
sections into an honest one-liner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import GraphContext
from .schemas.common import utcnow
from .state import DirectorState

# 1.4.0: additive keys — buyer_voice rollup, keyword_provider/keyword_metrics/
# opportunity_score in paid_search, ad-record artifact metadata.
# 1.5.0: additive key — similarweb_peers (peer-domain Similarweb enrichments).
JSON_SCHEMA_VERSION = "1.6.0"


def run_output_dir(state: DirectorState, ctx: GraphContext) -> Path:
    base = ctx.settings.outputs_dir if ctx.settings else Path("outputs")
    d = Path(base) / "runs" / state.run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(ctx: GraphContext, state: DirectorState) -> dict[str, Any]:
    """Load every stored record for the run once, split by type."""
    repo = ctx.repository
    out: dict[str, Any] = {
        "artifacts": [],
        "artifact_models": [],
        "classifications": [],
        "classification_models": [],
        "evidence": [],
        "claims": [],
        "change_events": [],
        "opportunities": [],
        "proof_gaps": [],
        "buyer_voice": [],
    }
    if repo is None:
        return out
    for m in repo.list_artifacts(run_id=state.run_id):
        out["artifact_models"].append(m)
        a = json.loads(m.model_dump_json())
        a.pop("raw_text", None)
        if a.get("normalized_text"):
            a["normalized_text"] = a["normalized_text"][:1500]
        out["artifacts"].append(a)
    for m in repo.list_classifications(state.run_id, family="merged"):
        out["classification_models"].append(m)
        out["classifications"].append(json.loads(m.model_dump_json()))
    for m in repo.list_classifications(state.run_id, family="evidence"):
        if m.__class__.__name__ == "EvidenceItem":
            out["evidence"].append(json.loads(m.model_dump_json()))
    for m in repo.list_classifications(state.run_id, family="buyer_voice"):
        if m.__class__.__name__ == "BuyerVoiceSignals":
            out["buyer_voice"].append(json.loads(m.model_dump_json()))
    for m in repo.list_claims(run_id=state.run_id):
        payload = json.loads(m.model_dump_json())
        (out["change_events"] if m.__class__.__name__ == "ChangeEvent" else out["claims"]).append(
            payload
        )
    for m in repo.list_opportunities(run_id=state.run_id):
        payload = json.loads(m.model_dump_json())
        (
            out["proof_gaps"] if m.__class__.__name__ == "MessageProofGap" else out["opportunities"]
        ).append(payload)

    # Ads-transparency DISCOVERY junk (red-team #5: FAQ pages, blank-query
    # pages, OTHER advertisers' pages all stamped advertiser=<competitor>)
    # pollutes the corpus counts and the paid-media signal. Exclude them here —
    # the single choke point every downstream count flows through — but never
    # drop an artifact any evidence record cites (grounding safety).
    from .synthesis import is_junk_ads_artifact

    advertiser_domain = state.company.primary_domain if state.company else ""
    cited_ids = {e.get("artifact_id") for e in out["evidence"]}
    junk_ids = {
        a["artifact_id"]
        for a in out["artifacts"]
        if a.get("source_type") == "google_ads"
        and a["artifact_id"] not in cited_ids
        and is_junk_ads_artifact(a.get("url", ""), a.get("metadata"), advertiser_domain)
    }
    if junk_ids:
        out["ads_junk_excluded"] = len(junk_ids)
        out["artifacts"] = [a for a in out["artifacts"] if a["artifact_id"] not in junk_ids]
        out["artifact_models"] = [
            m for m in out["artifact_models"] if m.artifact_id not in junk_ids
        ]
        out["classifications"] = [
            c for c in out["classifications"] if c.get("artifact_id") not in junk_ids
        ]
        out["classification_models"] = [
            m for m in out["classification_models"] if m.artifact_id not in junk_ids
        ]
    return out


def _focal_run_id(ctx: GraphContext, state: DirectorState) -> str | None:
    """Resolve THIS run's focal (Rippling) mirror id. Prefer the persisted
    ``state.focal_run_id`` (set when the mirror ran), then live scratch. Only as
    a last resort fall back to the most recent focal-domain run — that heuristic
    can pick up an unrelated (e.g. fixture) run, so it is genuinely last."""
    if state.focal_run_id:
        return state.focal_run_id
    if ctx.repository is None or state.focal_company is None:
        return None
    try:
        scratch = getattr(ctx, "scratch", None) or {}
        focal_run = scratch.get("focal_run_id")
        if not focal_run:
            for row in ctx.repository.list_runs(company=state.focal_company.primary_domain):
                focal_run = row["run_id"]
                break
        return focal_run
    except Exception:
        return None


def _focal_classifications(ctx: GraphContext, state: DirectorState):
    """Load the focal company's merged classifications for CEP-ownership."""
    focal_run = _focal_run_id(ctx, state)
    if not focal_run or ctx.repository is None:
        return []
    from .schemas.classification import MarketingClassification

    return [
        m
        for m in ctx.repository.list_classifications(focal_run, family="merged")
        if isinstance(m, MarketingClassification)
    ]


def _focal_artifact_models(ctx: GraphContext, state: DirectorState) -> list[Any]:
    """Load the focal run's artifact MODELS so the package can carry focal-side
    denominators (source distribution, per-vertical counts) — without them a
    niche-vs-big comparison has nothing to normalize against (red-team B16)."""
    focal_run = _focal_run_id(ctx, state)
    if not focal_run or ctx.repository is None:
        return []
    try:
        return list(ctx.repository.list_artifacts(run_id=focal_run))
    except Exception:
        return []


def _focal_evidence(ctx: GraphContext, state: DirectorState) -> dict[str, Any]:
    """Load the focal (Rippling) run's artifacts + evidence so every
    'Rippling proof: …' claim is traceable WITHIN this deliverable (QA
    finding: focal proof was asserted without in-package provenance)."""
    focal_run = _focal_run_id(ctx, state)
    out: dict[str, Any] = {"run_id": focal_run, "artifacts": [], "evidence": []}
    if not focal_run or ctx.repository is None:
        return out
    try:
        for m in ctx.repository.list_artifacts(run_id=focal_run):
            a = json.loads(m.model_dump_json())
            a.pop("raw_text", None)
            if a.get("normalized_text"):
                a["normalized_text"] = a["normalized_text"][:1500]
            out["artifacts"].append(a)
        for m in ctx.repository.list_classifications(focal_run, family="evidence"):
            if m.__class__.__name__ == "EvidenceItem":
                out["evidence"].append(json.loads(m.model_dump_json()))
    except Exception:
        pass
    return out


# Corpus-evidence floors: source_type -> [(threshold, dimension, level)].
# Recomputed at render so a run whose in-loop bookkeeping missed a dimension
# (red-team #2: 30 LinkedIn posts + 8 ads + 4 events all reported
# "not_attempted") still reports what the corpus actually contains. Floors only
# RAISE — they can never lower an honestly earned level. Ad artifacts are
# discovery pointers (no creative text), so paid_media never rises above low.
_CORPUS_COVERAGE_FLOORS: list[tuple[str, int, str, str]] = [
    ("linkedin_post", 1, "public_linkedin", "low"),
    ("linkedin_post", 5, "public_linkedin", "medium"),
    ("linkedin", 1, "public_linkedin", "low"),
    ("google_ads", 1, "paid_media", "low"),
    ("meta_ads", 1, "paid_media", "low"),
    ("linkedin_ads", 1, "paid_media", "low"),
    ("events", 1, "events", "low"),
    ("events", 3, "events", "medium"),
    ("reviews", 1, "customer_proof", "low"),
    ("jobs", 1, "personas_and_jobs", "low"),
    ("ooh", 1, "out_of_home", "low"),
    ("news", 1, "news_and_launches", "low"),
    ("wayback", 1, "historical_website", "low"),
    ("wayback", 1, "historical_messages", "low"),
    ("similarweb", 1, "commercial_motion", "low"),
]


def _honest_coverage(
    state: DirectorState,
    data: dict[str, Any],
    ceps: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Recompute-then-cap coverage from the (junk-filtered) corpus.

    1. RAISE: apply corpus-evidence floors — a dimension with real collected
       artifacts can't read "not_attempted" (red-team false absences).
    2. Derived dims: category_entry_points from the CEP rows, launches_current
       from current-window news.
    3. CAP: keep the thin-first-party downgrade — 'high' on 6 first-party
       fetches after a runtime_exhausted stop overstated coverage (audit)."""
    from . import coverage as cov

    coverage = dict(state.coverage)
    by_type: dict[str, int] = {}
    for a in data["artifacts"]:
        st = a.get("source_type") or ""
        by_type[st] = by_type.get(st, 0) + 1
    for source_type, threshold, dim, level in _CORPUS_COVERAGE_FLOORS:
        if by_type.get(source_type, 0) >= threshold:
            cov.raise_coverage(coverage, dim, level)
    observed_ceps = [r for r in (ceps or []) if r.get("competitor_pages", 0) > 0]
    if observed_ceps:
        cov.raise_coverage(
            coverage, "category_entry_points", "medium" if len(observed_ceps) >= 5 else "low"
        )
    # Classification-derived dims (verifier: funnel/proof_strategy/focal_
    # vulnerabilities were PERMANENTLY not_attempted while the same brief
    # displayed funnel stages, a proof-gap table, and focal-proof ratings).
    from .synthesis import is_placeholder_label

    n_funnel = sum(
        1
        for c in data.get("classifications", [])
        if any(not is_placeholder_label(s) for s in (c.get("funnel_stages") or []))
    )
    if n_funnel:
        cov.raise_coverage(coverage, "funnel", "medium" if n_funnel >= 20 else "low")
    n_proof = sum(1 for c in data.get("classifications", []) if c.get("proof_types"))
    if n_proof:
        cov.raise_coverage(coverage, "proof_strategy", "medium" if n_proof >= 20 else "low")
    focal_rated_gaps = [
        g for g in data.get("proof_gaps", []) if g.get("focal_proof_strength") is not None
    ]
    if focal_rated_gaps:
        cov.raise_coverage(
            coverage, "focal_vulnerabilities", "medium" if len(focal_rated_gaps) >= 3 else "low"
        )
    current_w = next(
        (w for w in state.time_windows if getattr(w, "purpose", None) == "current"), None
    )
    if current_w is not None:
        current_news = [
            a
            for a in data["artifacts"]
            if a.get("source_type") == "news"
            and str(a.get("published_at") or "") >= current_w.start_at.isoformat()[:10]
        ]
        if current_news:
            cov.raise_coverage(
                coverage, "launches_current", "medium" if len(current_news) >= 3 else "low"
            )

    first_party = sum(1 for a in data["artifacts"] if a.get("source_type") == "webpage")
    # Thin = few first-party pages. A runtime-capped stop only downgrades when
    # the page evidence is ALSO modest — 60+ fetched pages is genuinely high
    # coverage regardless of why the loop stopped.
    thin = first_party < 10 or (state.stop_reason == "runtime_exhausted" and first_party < 20)
    if thin:
        for dim in ("current_website", "pricing_and_packaging"):
            if coverage.get(dim) == "high":
                coverage[dim] = "medium"
    return coverage


def _author_from_linkedin_url(url: str) -> str | None:
    """Derive the poster's name from a LinkedIn post URL slug when the provider
    didn't return an author field (linkedin.com/posts/<author-slug>_rest...)."""
    import re as _re

    m = _re.search(r"linkedin\.com/posts/([a-z0-9-]+?)_", str(url or ""))
    if not m:
        return None
    # Strip trailing profile-id tokens (numeric or hex — 'dan-westgarth-710650a4'
    # must not render as author 'Dan Westgarth 710650a4').
    words = [w for w in m.group(1).split("-") if w]
    while words and _re.fullmatch(r"[0-9]+|[0-9a-f]{6,}", words[-1]):
        words.pop()
    return " ".join(w.capitalize() for w in words) or None


def _linkedin_posts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Join each LinkedIn post artifact with its merged classification so the
    brief/dashboard can showcase per-post: author, link, theme, stance, excerpt."""
    cls_by_art = {c.get("artifact_id"): c for c in data["classifications"]}
    out: list[dict[str, Any]] = []
    for a in data["artifacts"]:
        if a.get("source_type") != "linkedin_post":
            continue
        c = cls_by_art.get(a["artifact_id"], {})
        meta = a.get("metadata", {}) or {}
        out.append(
            {
                "artifact_id": a["artifact_id"],
                "author": a.get("author") or _author_from_linkedin_url(a.get("url", "")),
                "author_role": meta.get("author_role"),
                "post_url": a.get("url"),
                "posted_at": a.get("published_at"),
                "theme": c.get("primary_theme") or meta.get("theme"),
                "primary_message": c.get("primary_message"),
                "competitive_stance": c.get("competitive_stance"),
                "personas": c.get("personas", []),
                "excerpt": _clean_linkedin_excerpt(
                    a.get("normalized_text") or "", c.get("primary_message")
                ),
            }
        )
    return out


def _similarweb_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Pull the Similarweb (estimated) traffic block from its artifact, if any."""
    for a in data["artifacts"]:
        if a.get("source_type") == "similarweb":
            meta = a.get("metadata", {}) or {}
            if meta.get("peer"):
                # Peer-domain enrichment (audience-overlap context): its
                # traffic must never be reported as the researched company's.
                continue
            return {
                "domain": meta.get("domain"),
                "data_source": meta.get("data_source", "similarweb"),
                "observation_period": meta.get("observation_period"),
                "estimated": True,
                "metrics": meta.get("metrics", {}),
                "url": a.get("url"),
            }
    return {}


def _serpapi_ads_summary(data: dict[str, Any]) -> dict[str, Any] | None:
    """Deterministic summary of Google Ads Transparency creatives collected via
    SerpApi (``collection_method == 'serpapi_transparency'``).

    Claims ONLY what the API returns: advertiser (+ verified id), format mix,
    run-date window (first_shown -> last_shown), and VIDEO ad headlines (real
    machine-readable copy). Image/text creative copy is a rendered image and is
    never surfaced as text. No spend / impressions / performance — Google
    Transparency shows none for commercial ads. Returns None when there are no
    such records.
    """
    rows = [a for a in data["artifacts"] if a.get("collection_method") == "serpapi_transparency"]
    if not rows:
        return None
    advertisers: dict[str, str | None] = {}
    fmt_counts = {"video": 0, "image": 0, "text": 0, "other": 0}
    first_dates: list[str] = []
    last_dates: list[str] = []
    video_examples: list[dict[str, str]] = []
    non_machine_readable = 0
    for a in rows:
        meta = a.get("metadata") or {}
        rec = meta.get("ad_record") or {}
        adv = str(rec.get("advertiser") or meta.get("advertiser") or "").strip()
        if adv:
            advertisers[adv] = meta.get("advertiser_id") or advertisers.get(adv)
        fmt = str(rec.get("format") or meta.get("serpapi_format") or "").strip().lower()
        fmt_counts[fmt if fmt in fmt_counts else "other"] += 1
        if rec.get("first_seen"):
            first_dates.append(str(rec["first_seen"]))
        if rec.get("last_seen"):
            last_dates.append(str(rec["last_seen"]))
        if not meta.get("copy_machine_readable"):
            non_machine_readable += 1
        headline = str(rec.get("headline") or "").strip()
        if fmt == "video" and headline and len(video_examples) < 5:
            video_examples.append(
                {
                    "headline": headline,
                    "permalink": str(rec.get("source_url") or a.get("url") or ""),
                }
            )
    return {
        "n_creatives": len(rows),
        "advertisers": [{"name": k, "advertiser_id": v} for k, v in advertisers.items()],
        "format_mix": {k: v for k, v in fmt_counts.items() if v},
        "date_range": {
            "earliest_first_shown": min(first_dates) if first_dates else None,
            "latest_last_shown": max(last_dates) if last_dates else None,
        },
        "video_examples": video_examples,
        "non_machine_readable_creatives": non_machine_readable,
    }


def _similarweb_peers(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic rollup of stored PEER Similarweb artifacts (metadata.peer).

    Surfaces the peer-domain enrichments (planner §7b) that
    :func:`_similarweb_summary` deliberately skips: estimated demand context
    for the audience-overlap set. ``affinity`` joins from the OWN-domain
    artifact's ``digital_competitors`` when available; a metric a peer
    artifact does not carry stays ``None`` — honest absence, never a
    fabricated number. Empty list when no peer enrichment ran.
    """

    def _norm(domain: Any) -> str:
        return str(domain or "").strip().lower().removeprefix("www.")

    def _value(metrics: dict[str, Any], key: str) -> Any:
        raw = metrics.get(key)
        return raw.get("value") if isinstance(raw, dict) else raw

    affinity: dict[str, float] = {}
    for a in data["artifacts"]:
        meta = a.get("metadata", {}) or {}
        if a.get("source_type") != "similarweb" or meta.get("peer"):
            continue
        for item in _value(meta.get("metrics", {}) or {}, "digital_competitors") or []:
            if isinstance(item, dict) and item.get("domain"):
                try:
                    affinity[_norm(item["domain"])] = float(item.get("affinity") or 0.0)
                except (TypeError, ValueError):
                    continue
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in data["artifacts"]:
        meta = a.get("metadata", {}) or {}
        if a.get("source_type") != "similarweb" or not meta.get("peer"):
            continue
        domain = _norm(meta.get("domain"))
        if not domain or domain in seen:
            continue
        seen.add(domain)
        visits = _value(meta.get("metrics", {}) or {}, "estimated_monthly_visits")
        rows.append(
            {
                "domain": domain,
                "estimated_monthly_visits": (
                    visits
                    if isinstance(visits, (int, float)) and not isinstance(visits, bool)
                    else None
                ),
                "affinity": affinity.get(domain),
            }
        )
    rows.sort(key=lambda r: (r["affinity"] is None, -(r["affinity"] or 0.0), r["domain"]))
    return rows


_BUYER_VOICE_NOTE = (
    "Selection-biased sample of public reviews (G2/Capterra/TrustRadius): buyer "
    "language and direction only — never representative sentiment, satisfaction "
    "rates, or market share. n = signal occurrences across the mined pages."
)


def buyer_voice_rollup(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic rollup of stored family="buyer_voice" classifications.

    Pure counting over persisted records — no model call at render (§40.6).
    Empty input returns the honest empty shape (``n_reviews: 0``), never a
    fabricated section.
    """

    def _row_key(row: dict[str, Any]) -> tuple[int, str]:
        return (-int(row["n"]), str(row["theme"]))

    Entry = tuple[str, dict[str, Any], dict[str, Any], str]  # theme, extra, signal, url

    def _group(entries: list[Entry]) -> list[dict[str, Any]]:
        """Group (theme, extra fields, signal, source url) into counted rows.

        The grouping key includes the extra fields, so e.g. the same
        alternative seen as both switched_from and switched_to stays two rows.
        """
        rows: dict[tuple[str, ...], dict[str, Any]] = {}
        for theme, extra, signal, url in entries:
            key = (theme, *(str(v) for v in extra.values()))
            row = rows.get(key)
            if row is None:
                rows[key] = {
                    "theme": theme,
                    **extra,
                    "n": 1,
                    "example_quote": signal.get("quote", ""),
                    "source_url": url,
                }
            else:
                row["n"] += 1
        return sorted(rows.values(), key=_row_key)

    objection_entries: list[Entry] = []
    praise_entries: list[Entry] = []
    switching_entries: list[Entry] = []
    considered_entries: list[Entry] = []
    reality_entries: list[Entry] = []
    for record in records:
        url = record.get("source_url", "")
        for s in record.get("objections") or []:
            if s.get("theme"):
                objection_entries.append((s["theme"], {"sentiment": "negative"}, s, url))
        for s in record.get("praise") or []:
            if s.get("theme"):
                praise_entries.append((s["theme"], {"sentiment": "positive"}, s, url))
        for s in record.get("alternatives") or []:
            if not s.get("alternative"):
                continue
            if s.get("direction") in ("switched_from", "switched_to"):
                switching_entries.append((s["alternative"], {"direction": s["direction"]}, s, url))
            elif s.get("direction") in ("evaluated", "unclear"):
                # NOT a switching trigger — surfaced separately so evaluated
                # alternatives are never silently dropped nor mislabeled as
                # observed switches (review nit: dropped from every surface).
                considered_entries.append((s["alternative"], {"direction": s["direction"]}, s, url))
        for s in record.get("message_reality_signals") or []:
            if s.get("claim_theme") and s.get("relation") in ("contradicts", "confirms"):
                reality_entries.append((s["claim_theme"], {"relation": s["relation"]}, s, url))

    objections = _group(objection_entries)
    return {
        # Review pages actually mined (one stored record per review artifact).
        "n_reviews": len(records),
        "themes": sorted(_group(praise_entries) + objections, key=_row_key),
        "switching_triggers": _group(switching_entries),
        # Alternatives reviewers EVALUATED (or mentioned unclearly) without an
        # observed switch — competitive shortlist context, not churn evidence.
        "alternatives_considered": _group(considered_entries),
        "objections": objections,
        "message_reality": _group(reality_entries),
        "note": _BUYER_VOICE_NOTE,
    }


def build_json_package(state: DirectorState, ctx: GraphContext) -> dict[str, Any]:
    from . import synthesis

    data = _load(ctx, state)
    dom = synthesis.dominant_message(data["classification_models"], data["artifact_models"])
    skew = synthesis.corpus_skew(data["artifact_models"])
    dist = synthesis.source_distribution(data["artifact_models"])
    motion = synthesis.commercial_motion(data["classification_models"])
    positioning = synthesis.product_positioning(
        data["classification_models"],
        company_name=state.company.canonical_name if state.company else None,
    )
    artifact_source = {a["artifact_id"]: a["source_type"] for a in data["artifacts"]}
    matrix = synthesis.persona_channel_funnel(data["classification_models"], artifact_source)
    focal_cls_models = _focal_classifications(ctx, state)
    focal_artifact_models = _focal_artifact_models(ctx, state)
    ceps = synthesis.category_entry_points(data["classification_models"], focal_cls_models)
    # Coverage is recomputed from the corpus AFTER the CEP rows exist (the
    # category_entry_points dimension derives from them).
    honest_coverage = _honest_coverage(state, data, ceps)
    coverage_detail = [
        json.loads(cd.model_dump_json())
        for cd in synthesis.coverage_details(
            state,
            data["artifact_models"],
            data["classification_models"],
            coverage=honest_coverage,
        )
    ]
    # Reconcile persisted (mid-run) change events against the FINAL corpus so
    # the package can never assert an emergence its own baseline refutes.
    from .processing.temporal import reconcile_change_events

    change_events, reconciliation_notes = reconcile_change_events(
        data["change_events"],
        data["classification_models"],
        data["artifact_models"],
        state.time_windows,
    )
    # Key-topic comparison data: theme counts for BOTH companies so the UI can
    # graph 'key related topics per company' side by side. Selection = union of
    # each side's top-10 BY SHARE; every selected theme carries its TRUE count
    # on both sides (red-team: independent top-10 truncation made the UI render
    # fabricated zeros for themes outside one side's top-10).
    from collections import Counter as _Counter

    _comp_themes: _Counter[str] = _Counter(
        c.primary_theme for c in data["classification_models"] if c.primary_theme
    )
    _focal_themes: _Counter[str] = _Counter(
        c.primary_theme for c in focal_cls_models if c.primary_theme
    )
    _nc = max(1, len(data["classification_models"]))
    _nf = max(1, len(focal_cls_models))
    _has_focal = bool(focal_cls_models)
    # Outlier floor: a theme with a single page on BOTH sides is an anecdote,
    # not a chart row.
    _selected = {
        t
        for t in (
            {t for t, _ in _comp_themes.most_common(10)}
            | {t for t, _ in _focal_themes.most_common(10)}
        )
        if _comp_themes.get(t, 0) >= 2 or _focal_themes.get(t, 0) >= 2
    }
    # With no focal mirror the focal side is EMPTY, not zero — emitting 0s
    # fabricates a measured absence (verifier: snapshot runs rendered
    # "X vs Rippling" topic bars with fabricated 0 (0%) right sides).
    theme_comparison = {
        "competitor_themes": {t: _comp_themes.get(t, 0) for t in _selected},
        "focal_themes": {t: _focal_themes.get(t, 0) for t in _selected} if _has_focal else {},
        "competitor_shares": {t: round(_comp_themes.get(t, 0) / _nc, 4) for t in _selected},
        "focal_shares": (
            {t: round(_focal_themes.get(t, 0) / _nf, 4) for t in _selected} if _has_focal else {}
        ),
        "competitor_n_classified": len(data["classification_models"]),
        "focal_n_classified": len(focal_cls_models),
        "note": (
            "shares = count / n classified artifacts of that company; cross-company "
            "bars compare shares, raw counts shown alongside"
            if _has_focal
            else "no focal mirror collected this run — cross-company comparison unavailable"
        ),
    }
    # Persisted proof gaps predate the normalization fields (render never
    # re-runs comparison.py), so legacy runs would carry schema defaults
    # forever (verifier). Fill shares + sample sufficiency at render from the
    # same theme counters; never overwrite a stricter persisted value.
    _suff = "ok"
    if focal_cls_models and len(focal_cls_models) < 15 and _nc < 15:
        _suff = "insufficient_both"
    elif focal_cls_models and len(focal_cls_models) < 15:
        _suff = "insufficient_focal_sample"
    elif _nc < 15:
        _suff = "insufficient_competitor_sample"
    from .comparison import ATTACK_MIN_PAGES, ATTACK_MIN_SHARE

    for g in data["proof_gaps"]:
        theme_key = str(g.get("short_label", "")).replace(" ", "_")
        if g.get("competitor_theme_share") is None and theme_key in _comp_themes:
            g["competitor_theme_share"] = round(_comp_themes[theme_key] / _nc, 4)
        if g.get("focal_theme_share") is None and _has_focal and theme_key in _focal_themes:
            g["focal_theme_share"] = round(_focal_themes[theme_key] / _nf, 4)
        if g.get("sample_sufficiency", "ok") == "ok" and _suff != "ok":
            g["sample_sufficiency"] = _suff
        # Outlier annotation for PERSISTED verdicts (render never re-runs the
        # comparison — the verdict stays, the display gains a caution): a HIGH
        # attackability resting on fewer pages than the ATTACK floor gets
        # flagged and the UI shows a visible 'THIN n=X' tag.
        if theme_key in _comp_themes:
            g["theme_page_count"] = _comp_themes[theme_key]
        if (
            g.get("attackability") == "high"
            and g.get("theme_page_count") is not None
            and g["theme_page_count"] < ATTACK_MIN_PAGES
            and (g.get("competitor_theme_share") or 0) < ATTACK_MIN_SHARE
        ):
            g["outlier_flag"] = "thin_theme"
    # Corpus-size normalization context: with a niche competitor (12-page site)
    # vs a large focal corpus, raw counts fabricate verdicts — every consumer
    # (UI banner, dashboard, brief callout) shares this ONE rule.
    _n_min = (
        min(len(data["classification_models"]), len(focal_cls_models))
        if focal_cls_models
        else len(data["classification_models"])
    )
    _asym = (
        round(
            max(len(data["classification_models"]), len(focal_cls_models)) / max(1, _n_min),
            2,
        )
        if focal_cls_models
        else None
    )
    corpus_normalization = {
        "competitor": {
            "name": state.company.canonical_name if state.company else state.company_input,
            "n_artifacts": len(data["artifacts"]),
            "n_classified": len(data["classification_models"]),
        },
        "focal": {
            "name": state.focal_company.canonical_name if state.focal_company else None,
            "n_artifacts": len(focal_artifact_models),
            "n_classified": len(focal_cls_models),
        },
        "asymmetry_ratio": _asym,
        "small_corpus": _n_min < 15,
        # The asymmetry banner is about CROSS-COMPANY comparability — with no
        # focal mirror there is nothing to compare, so it must not fire
        # (verifier: snapshot briefs rendered "X vs focal 0 (ratio None)").
        "show_banner": bool(_has_focal and ((_asym or 0) > 3 or _n_min < 20)),
        "normalization_note": (
            "All cross-company comparisons (CEP ownership, key topics, per-vertical) are "
            "computed on share-of-corpus (count / classified artifacts per company); raw "
            "counts are retained but are not directly comparable across corpora of "
            "different sizes."
            if _has_focal
            else "No focal mirror collected this run — cross-company comparisons and "
            "ownership verdicts are not available (CEP rows are competitor-observed "
            "triggers only)."
        ),
    }
    focal_evidence = _focal_evidence(ctx, state)
    linkedin_posts = _linkedin_posts(data)
    similarweb = _similarweb_summary(data)
    similarweb_peers = _similarweb_peers(data)
    serpapi_ads = _serpapi_ads_summary(data)
    channel_alignment = synthesis.message_channel_alignment(
        data["classification_models"], data["artifact_models"]
    )
    temporal_baseline = synthesis.temporal_baseline(
        data["classification_models"], data["artifact_models"], state.time_windows
    )
    _cfg = getattr(ctx, "config", None)
    taxonomy = _cfg.taxonomy if _cfg else {}
    vertical_analysis = synthesis.product_vertical_analysis(
        data["classification_models"], data["artifact_models"], taxonomy
    )
    # Focal-side denominators for per-product (vertical-scoped) comparison —
    # same function, same taxonomy, zero new analytics in the UI (§40.6).
    focal_vertical_analysis = (
        synthesis.product_vertical_analysis(focal_cls_models, focal_artifact_models, taxonomy)
        if focal_cls_models
        else {"verticals": [], "by_artifact": {}}
    )
    _focal_verts_by_name = {v["vertical"]: v for v in focal_vertical_analysis.get("verticals", [])}
    for v in vertical_analysis.get("verticals", []):
        fv = _focal_verts_by_name.get(v["vertical"], {})
        v["focal_n_artifacts"] = fv.get("n_artifacts", 0)
        v["focal_top_themes"] = fv.get("top_themes", [])
        v["focal_theme_counts"] = fv.get("theme_counts", {})
        v["competitor_share"] = round(v.get("n_artifacts", 0) / _nc, 4)
        v["focal_share"] = round(fv.get("n_artifacts", 0) / _nf, 4)
    # EDA-verified insight graphics (marketing-ops): five cross-cutting joins
    # (proof×demand, funnel×vertical, channel×proof, affinity×sitemap) that the
    # single-dimension charts can't show. Deterministic; each block self-omits
    # when its inputs are missing.
    insight_gfx = synthesis.insight_graphics(
        data["classification_models"],
        data["artifact_models"],
        focal_cls_models,
        ceps,
        similarweb,
        vertical_analysis.get("by_artifact", {}),
        focal_vertical_analysis.get("by_artifact", {}),
        {a.artifact_id: a.source_type for a in focal_artifact_models},
        state.company.canonical_name if state.company else state.company_input,
        state.focal_company.canonical_name if state.focal_company else "Rippling",
    )
    # Tag each LinkedIn post with its product verticals (per-offering view).
    for lp in linkedin_posts:
        lp["verticals"] = vertical_analysis["by_artifact"].get(lp["artifact_id"], [])
    # Tag each CEP row with the top verticals whose pages carry that buying
    # trigger (audit: CEPs are vertical-shaped; a flat label hides that).
    _by_artifact = vertical_analysis["by_artifact"]
    for row in ceps:
        _vc: dict[str, int] = {}
        for c in data["classifications"]:
            # CEP keys are label-normalized at synthesis; normalize here too.
            raw_ceps = {
                synthesis._norm_label(x) for x in (c.get("category_entry_points") or []) if x
            }
            if row.get("cep") in raw_ceps:
                for v in _by_artifact.get(c.get("artifact_id"), []):
                    _vc[v] = _vc.get(v, 0) + 1
        row["top_verticals"] = [v for v, _ in sorted(_vc.items(), key=lambda kv: -kv[1])[:2]]
    # Source-URL registry (traceability chain): every collected source with its
    # provenance, so a claim's evidence id -> artifact id -> URL+timestamp is
    # resolvable from the JSON alone.
    source_registry = [
        {
            "artifact_id": a["artifact_id"],
            "url": a.get("url"),
            "source_type": a["source_type"],
            "retrieved_at": a.get("retrieved_at"),
            "archive_capture_at": a.get("archive_capture_at"),
        }
        for a in data["artifacts"]
        if a.get("url")
    ]
    # Named matrices (also exposed as their own top-level keys for convenience).
    matrices = [
        {"name": "persona_channel_funnel", "matrix": matrix},
        {"name": "category_entry_point_ownership", "matrix": ceps},
    ]
    claims_list = data["claims"]
    # Cited/total are computed over the SAME claim set (red-team #6: cited had
    # no status filter, so a rejected-but-cited claim could push cited > total).
    material_claims = [
        c for c in claims_list if c.get("status") not in ("rejected", "contradicted")
    ]
    eval_summary = {
        "n_artifacts": len(data["artifacts"]),
        "n_classifications": len(data["classifications"]),
        "n_claims": len(claims_list),
        "n_opportunities": len(data["opportunities"]),
        "n_proof_gaps": len(data["proof_gaps"]),
        "n_change_events": len(change_events),
        "material_claims_total": len(material_claims),
        "material_claims_cited": sum(1 for c in material_claims if c.get("evidence_ids")),
        "corpus_skew_warnings": len(skew),
        "note": "In-package self-summary. Full graded benchmark: `competitive-agent eval-benchmark`.",
    }
    classified_ids = {c.get("artifact_id") for c in data["classifications"]}
    unclassified = [
        {
            "artifact_id": a["artifact_id"],
            "source_type": a["source_type"],
            "url": a["url"],
            "reason": "site map (URL list, not marketing content)"
            if a["source_type"] in ("sitemap", "robots")
            else "too little extractable text"
            if len(a.get("normalized_text", "")) < 200
            else "classification unavailable",
        }
        for a in data["artifacts"]
        if a["artifact_id"] not in classified_ids
    ]

    # THE BOTTOM LINE — 2-3 sentences composed ONLY from verified numbers
    # (top play, ownership split, momentum). Deterministic template, no model
    # prose: every clause traces to a chart in the package.
    def _plural(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    def _bottom_line() -> str | None:
        comp_name = state.company.canonical_name if state.company else state.company_input
        focal_name = state.focal_company.canonical_name if state.focal_company else "Rippling"
        bits: list[str] = []
        own = {
            k: sum(1 for r in ceps if r.get("ownership") == k)
            for k in ("competitor_advantage", "contested", "focal_owns")
        }
        if ceps and (own["competitor_advantage"] or own["contested"] or own["focal_owns"]):
            bits.append(
                f"{comp_name} owns {own['competitor_advantage']} of {len(ceps)} buying "
                f"intents to {focal_name}'s {own['focal_owns']} "
                f"({_plural(own['contested'], 'contested intent')})"
            )
        # Momentum: NEVER count 'expanding' — with asymmetric windows raw-count
        # growth is guaranteed (reviewer P0). Repositioned + emerging only.
        repositioned = [c for c in change_events if c.get("lifecycle") == "repositioned"]
        n_new = sum(1 for c in change_events if c.get("lifecycle") == "emerging")
        if repositioned:
            r0 = repositioned[0]
            bits.append(
                f"their story is shifting — {r0.get('dimension', 'message').replace('_', ' ')} "
                f"{r0.get('prior_state')} → {r0.get('current_state')} (low-confidence)"
            )
        elif n_new:
            bits.append(f"{_plural(n_new, 'new theme')} appeared in their messaging")
        elif change_events is not None:
            bits.append("their core story is static")
        verbs = [
            (g.get("attackability_detail") or {}).get("overall")
            or ("attack" if g.get("attackability") == "high" else "investigate")
            for g in data["proof_gaps"]
        ]
        n_attack = sum(1 for v in verbs if v == "attack")
        if data["proof_gaps"]:
            n_g = len(data["proof_gaps"])
            bits.append(
                f"{n_attack} of {_plural(n_g, 'repeated claim')} "
                f"{'is a clean attack opening' if n_attack == 1 else 'are clean attack openings'}"
                + ("" if n_attack else " — build proof before attacking")
            )
        if not bits:
            return None
        line = f"{'; '.join(bits)}."
        if data["opportunities"]:
            top = data["opportunities"][0]
            line += (
                f" Start with “{top.get('title')}” (metric: "
                f"{top.get('primary_metric') or top.get('kill_rule') or 'see Action Board'})."
            )
            # THIN disclosure on the #1 play (reviewer P1: the deep-dive said
            # THIN while the exec surface sold the play unqualified).
            top_gap = next(
                (
                    g
                    for gid in top.get("supporting_claim_ids", [])
                    for g in data["proof_gaps"]
                    if g.get("claim_id") == gid
                ),
                None,
            )
            if top_gap and (
                top_gap.get("outlier_flag") == "thin_theme"
                or (top_gap.get("theme_page_count") or 99) < 5
            ):
                line += (
                    f" Caveat: that play rests on {_plural(top_gap.get('theme_page_count') or 0, 'competitor page')}"
                    " — verify their pages before committing spend."
                )
        return line

    pkg = {
        "schema_version": JSON_SCHEMA_VERSION,
        "bottom_line": _bottom_line(),
        "run": {
            "run_id": state.run_id,
            "parent_run_id": state.parent_run_id,
            "mode": state.mode,
            "execution_mode": state.execution_mode,
            "generated_at": utcnow().isoformat(),
            "stop_reason": state.stop_reason,
            "stop_reason_label": _stop_reason_label(state.stop_reason),
            "iterations": state.iteration,
            "tool_cost_usd": round(state.spent_usd, 4),
            "model_cost_usd": round(state.model_cost_usd, 4),
            "total_spend_usd": round(state.total_spend_usd(), 4),
        },
        "scope": {
            "company_input": state.company_input,
            "compare_to": state.compare_to,
            "lookback_days": state.lookback_days,
            "user_focus": state.user_focus,
            "time_windows": [json.loads(w.model_dump_json()) for w in state.time_windows],
        },
        "companies": [
            json.loads(c.model_dump_json())
            for c in (state.company, state.focal_company)
            if c is not None
        ],
        "dominant_message": dom,
        "source_distribution": dist,
        "focal_source_distribution": synthesis.source_distribution(focal_artifact_models)
        if focal_artifact_models
        else {},
        "corpus_skew_warnings": skew,
        "corpus_normalization": corpus_normalization,
        "coverage": honest_coverage,
        "coverage_detail": coverage_detail,
        "commercial_motion": motion,
        "product_positioning": positioning,
        "category_entry_points": ceps,
        "persona_channel_matrix": matrix,
        "sources": source_registry,
        "artifacts": data["artifacts"],
        "unclassified_artifacts": unclassified,
        "evidence": data["evidence"],
        # Focal (Rippling) mirror evidence — so every "Rippling proof: …" claim is
        # traceable within this deliverable, not just in the sibling mirror run.
        "focal_evidence": focal_evidence,
        # Competitor LinkedIn employee posts (one per post) + Similarweb traffic.
        "linkedin_posts": linkedin_posts,
        "similarweb": similarweb,
        # Peer-domain Similarweb enrichments (metadata.peer=true): estimated
        # demand context for the audience-overlap set — the rows
        # _similarweb_summary skips on purpose so the competitor's own traffic
        # view stays untouched.
        "similarweb_peers": similarweb_peers,
        # Observed paid ads: real Google Ads Transparency creatives collected via
        # the SerpApi seam (deterministic; None when the seam collected nothing).
        "serpapi_ads": serpapi_ads,
        # Buyer voice: deterministic rollup of stored family="buyer_voice"
        # classifications (review pages mined in-loop). Selection-biased
        # sample — language/direction only, never representative sentiment.
        "buyer_voice": buyer_voice_rollup(data["buyer_voice"]),
        # Per-offering view: how the competitor positions in each product
        # vertical (deterministic keyword mapping; method disclosed inside).
        "product_vertical_analysis": vertical_analysis,
        "focal_vertical_analysis": focal_vertical_analysis,
        "insight_graphics": insight_gfx,
        # Paid-vs-organic + employee-advocacy theme alignment (deterministic).
        "channel_alignment": channel_alignment,
        "theme_comparison": theme_comparison,
        "temporal_baseline": temporal_baseline,
        "classifications": data["classifications"],
        "claims": data["claims"],
        # product_portfolios/launches require the deep §38 product-entity loop
        # (documented as deferred in IMPLEMENTATION_STATUS.md); product intel is
        # exposed via product_positioning + category_entry_points today.
        "product_portfolios": [],
        "launches": [],
        "change_events": change_events,
        "change_event_reconciliation": reconciliation_notes,
        "matrices": matrices,
        "proof_gaps": data["proof_gaps"],
        "opportunities": data["opportunities"],
        "opportunities_rejected": (
            (getattr(ctx, "scratch", None) or {}).get("opportunities_rejected", [])
        ),
        "limitations": _derived_limitations(
            state, honest_coverage, {"artifacts": data["artifacts"]}
        ),
        "negative_observations": _reconcile_negatives(
            state.negative_observations,
            {"similarweb": similarweb},
            data.get("ads_junk_excluded", 0),
        ),
        "tool_failures": [json.loads(f.model_dump_json()) for f in state.failed_actions.values()],
        "trace_summary": {"tool_calls": state.tool_calls_made},
        "eval_summary": eval_summary,
    }
    # The assignment's four questions, answered from the package itself —
    # composed LAST so every ledger it cites is already assembled.
    pkg["assignment_answers"] = build_assignment_answers(pkg)
    return pkg


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _badge(level: str, n: int | None = None, sources: list[str] | None = None) -> str:
    bits = [level]
    if n is not None:
        bits.append(f"{n} artifacts")
    if sources:
        bits.append(" + ".join(sources[:3]))
    return "[" + " · ".join(bits) + "]"


def render_markdown(state: DirectorState, pkg: dict[str, Any]) -> str:
    company = state.company.canonical_name if state.company else state.company_input
    focal = state.focal_company.canonical_name if state.focal_company else "Rippling"
    L: list[str] = []
    add = L.append

    add(f"# Competitive Marketing Intelligence Brief — {company}")
    add("")
    add(
        f"*Run `{state.run_id}` · mode `{state.mode}` · execution `{state.execution_mode}` · "
        f"generated {pkg['run']['generated_at']}*"
    )
    if state.execution_mode == "fixture":
        add("")
        add("> **Fixture mode — synthetic, deterministic data.** Not live public evidence.")

    changes = pkg["change_events"]
    gaps = pkg["proof_gaps"]
    opps = pkg["opportunities"]
    cls = pkg["classifications"]
    dom = pkg["dominant_message"]

    # --- Executive summary (feedback #27) -----------------------------------
    add("\n## Executive summary\n")
    if pkg.get("bottom_line"):
        add(f"> **The bottom line:** {pkg['bottom_line']}\n")
    if dom.get("theme"):
        label = (
            "Dominant company message"
            if dom.get("is_company_level")
            else "Most prominent message in the collected corpus"
        )
        add(f"- **{label}:** {dom.get('label')} ({dom['reason']}).")
    add(f"- **Product-positioning read:** {_positioning_oneliner(cls, company)}")
    if changes:
        # Prefer the highest-confidence change and label it by its ACTUAL
        # confidence/lifecycle — an emerging, low-confidence, absence-based signal
        # is not a "confirmed change" (a both-periods high/medium change is).
        rank = {"high": 3, "medium": 2, "low": 1}
        top = max(changes, key=lambda c: rank.get(str(c.get("confidence", "low")), 0))
        conf = str(top.get("confidence", "low"))
        lifecycle = str(top.get("lifecycle", ""))
        if conf in ("high", "medium") and lifecycle not in ("emerging", "expanding"):
            label = "Confirmed change"
        elif lifecycle == "expanding":
            label = f"Expanding theme ({conf} confidence — present prior, more present now)"
        else:
            label = f"Emerging signal ({conf} confidence, needs a prior-window baseline)"
        add(f"- **{label}:** {top['dimension']} — {top['prior_state']} → {top['current_state']}.")
    else:
        add(
            "- **Change over time:** none met the both-period evidence bar this run (see Strategy-over-time)."
        )
    if opps:
        # "Top-ranked" reflects the engine's overall ordering (defensibility is
        # one input, not the only one); don't imply it has the strongest proof
        # when it may not (QA finding #6).
        add(f"- **Top-ranked {focal} opening:** {opps[0]['title']}.")
    add(f"- **Largest uncertainty:** {_largest_uncertainty(pkg)}.")

    # --- Scorecard: the whole analysis as action counts (exec feedback:
    # findings must read as verbs, not prose) --------------------------------
    _sc_ceps = pkg.get("category_entry_points") or []
    _own = {
        k: sum(1 for r in _sc_ceps if r.get("ownership") == k)
        for k in ("competitor_advantage", "contested", "focal_owns", "insufficient_sample")
    }
    _verbs = {"attack": 0, "investigate": 0, "reframe": 0}
    for g in gaps:
        v = (g.get("attackability_detail") or {}).get("overall") or (
            "attack"
            if g.get("attackability") == "high"
            else "investigate"
            if g.get("attackability") == "medium"
            else "reframe"
        )
        _verbs["reframe" if v == "concede" else v] = (
            _verbs.get("reframe" if v == "concede" else v, 0) + 1
        )
    _n_emerging = sum(1 for c in changes if c.get("lifecycle") == "emerging")
    _n_expanding = sum(1 for c in changes if c.get("lifecycle") == "expanding")
    _n_stable = len((pkg.get("temporal_baseline") or {}).get("stable_themes") or [])
    if _sc_ceps or gaps or changes:
        add("\n### Scorecard\n")
        if _sc_ceps:
            add(
                f"- **Search intents ({len(_sc_ceps)}):** {_own['competitor_advantage']} {company}-owned · "
                f"{_own['contested']} contested · {_own['focal_owns']} {focal}-owned · "
                f"{_own['insufficient_sample']} too thin to call → **target the contested set; "
                f"defend what {focal} owns**"
            )
        if gaps:
            _atk_action = (
                "start where they claim what they can't prove"
                if _verbs["attack"]
                else "no clean attack this run — build proof on the INVESTIGATE list first"
            )
            add(
                f"- **Attack surface ({len(gaps)} repeated claims):** {_verbs['attack']} ATTACK · "
                f"{_verbs['investigate']} INVESTIGATE · {_verbs['reframe']} AVOID/REFRAME → "
                f"**{_atk_action}**"
            )
        if changes or _n_stable:
            add(
                f"- **Theme momentum:** {_n_emerging} emerging · {_n_expanding} expanding · "
                f"{_n_stable} stable → **counter the moving themes before they harden**"
            )

    # --- The assignment deliverable: four questions, answered ---------------
    # Rendered EXCLUSIVELY from pkg["assignment_answers"] (single source of
    # truth, composed in build_json_package) so brief and data.json can never
    # diverge.
    L.extend(_assignment_section_lines(pkg))

    # --- Action Board — Rippling-first (feedback #28) -----------------------
    add("\n## Action Board\n")
    add(f"### What {focal} should do")
    if opps:
        for o in opps[:3]:
            add(
                f"- **{o['title']}** `{o.get('action_category', o['deliverable_type'])}` "
                f"{_badge(o['structural_defensibility'])}"
            )
            add(f"  - Why now: {o['message_angle']}")
            comp = o.get("product_comparability") or {}
            if comp:
                add(
                    f"  - Product comparability: **{comp.get('comparability', 'weak')}** "
                    f"({comp.get('competitor_product', '?')} vs {comp.get('focal_product', '?')})"
                )
            add(
                f"  - {focal} proof: {o['focal_proof_status']} · already-saying-it: {o['focal_current_usage']} "
                f"· legal review: {'required' if o['legal_review_required'] else 'not required'}"
            )
            add(f"  - Risk (backfire): {o['why_this_could_backfire']}")
            if o.get("campaign_plan"):
                cp = o["campaign_plan"]
                add(
                    f"  - Campaign: audience {', '.join(cp.get('audience', [])[:3]) or '—'}; "
                    f"channels {', '.join(cp.get('channels', [])[:3]) or '—'}; metric {cp.get('primary_metric', '—')}"
                )
            stages = o.get("staged_plan") or []
            if stages:
                add(
                    f"  - Staged: {' → '.join(s['stage'] for s in stages)} (each with its own proceed/stop rule)"
                )
            add(f"  - Kill rule: {o['kill_rule']}")
    else:
        add("- _No opportunity survived the genericness / superiority critics this run._")
        for r in pkg.get("opportunities_rejected", [])[:3]:
            add(f"  - rejected: {r}")

    add(f"\n### Strongest message–proof gaps (competitor vs {focal})")
    if gaps:
        add(
            "| Gap | Competitor proof | "
            + focal
            + " proof | Weakest vertical | Stance | Specificity |"
        )
        add("|---|---|---|---|---|---|")
        for g in gaps[:5]:
            label = g.get("short_label") or g["claim_text"][:40]
            stance = (g.get("attackability_detail") or {}).get("overall", g["attackability"])
            wv = g.get("weakest_vertical")
            wv_cell = (
                f"{str(wv).replace('_', ' ')} "
                f"({(g.get('vertical_strengths', {}).get(wv) or {}).get('strength', '?')})"
                if wv
                else "—"
            )
            add(
                f"| {label} | {g['proof_strength']} | {g.get('focal_proof_strength', 'n/a')} | "
                f"{wv_cell} | {stance} | {g.get('claim_specificity', 'unknown')} |"
            )
        add("")
        add("_Proof distributions and the exact repeated claims are in the Evidence appendix._")
        add(
            f"\n_Rating rubric: proof strength is the MODAL per-page strength for the theme "
            f"(ties break toward the weaker rating), so a single strong page can't inflate a "
            f"theme and one quantified outcome may still read 'none' overall — the full "
            f"distribution is in the JSON. 'Weakest vertical' flags a product vertical where "
            f"proof diverges below the corpus verdict (attack there first). 'moderate' = "
            f"some named-customer or partial quantified proof; 'strong' = repeated quantified/"
            f"third-party proof. '{focal} proof: missing/partial/available' rates {focal}'s own "
            f"publishable proof for the equivalent claim._"
        )
    else:
        add("- _No repeated competitor theme with a proof gap was observed in this corpus._")

    add("\n### What not to attack")
    concede = [
        g
        for g in gaps
        if (g.get("attackability_detail") or {}).get("overall") in ("reframe", "concede")
    ]
    # Dedup by theme label — the gap engine can emit several records for one
    # theme; "don't attack consolidation" should be said once.
    seen_labels: set[str] = set()
    concede_unique = []
    for g in concede:
        label = str(g.get("short_label", ""))
        if label in seen_labels:
            continue
        seen_labels.add(label)
        concede_unique.append(g)
    if concede_unique:
        for g in concede_unique[:2]:
            add(f"- **{g.get('short_label')}** — {g['actionable_interpretation']}")
    else:
        add("- _No theme reached the 'reframe/concede' bar; none flagged as off-limits._")

    # --- Current positioning (feedback #1, #23, #24) -----------------------
    add("\n## Current public positioning\n")
    if dom.get("theme"):
        label = (
            "Dominant company message"
            if dom.get("is_company_level")
            else "Most prominent in the collected corpus"
        )
        add(f"- **{label}:** {dom.get('label')}")
        add(
            f"  - Basis: surfaces {', '.join(s for s in dom.get('surfaces', []) if s and s != 'None')}; "
            f"source classes {', '.join(dom.get('source_classes', []))}"
        )
    themes = _theme_counts(cls)
    if themes:
        add(
            "- **Themes observed (primary theme per page):** "
            + ", ".join(f"{t} ({n})" for t, n in themes[:8])
        )
    # Divergent verticals (audit): call out product verticals whose narrative
    # departs from the company-level dominant message so the exec read never
    # masks a per-offering story. Deterministic join, no model call.
    _dom_theme = (dom.get("theme") or "").strip()
    _divergent = [
        v
        for v in (pkg.get("product_vertical_analysis") or {}).get("verticals", [])
        if v.get("n_artifacts", 0) >= 5
        and _dom_theme
        and _dom_theme not in (v.get("top_themes") or [])
    ]
    if _divergent:
        add("- **Verticals diverging from the dominant message:**")
        for v in _divergent[:4]:
            add(
                f"  - {v['vertical'].replace('_', ' ')} ({v['n_artifacts']} pages) leads with "
                f"{', '.join(v.get('top_themes', [])[:2]) or 'other themes'} instead"
            )
    villains = _villain_wording(cls)
    if villains["exact"]:
        add("- **Villain / status-quo wording (exact):**")
        for w in villains["exact"][:5]:
            add(f"  - “{w}”")
    if villains["normalized"]:
        add(f"  - normalized: {', '.join(sorted(villains['normalized']))}")
    stance = _stance_distribution(cls)
    if stance:
        add("- **Competitive stance distribution:**")
        add("  | Stance | Artifacts |")
        add("  |---|---:|")
        for s, n in stance:
            add(f"  | {s} | {n} |")

    # --- Commercial motion (feedback #20) ----------------------------------
    m = pkg.get("commercial_motion", {})
    if m and m.get("primary_motion") != "unclear":
        add(
            f"\n## Commercial motion ({m.get('confidence', 'low')} confidence — {m.get('basis', '')})\n"
        )
        mix = m.get("pricing_disclosure_mix") or {}
        mix_str = (
            " (observed mix: "
            + ", ".join(f"{k}:{v}" for k, v in sorted(mix.items(), key=lambda kv: -kv[1]))
            + ")"
            if mix
            else ""
        )
        add(
            f"- **Inferred motion:** {m['primary_motion']} · **pricing disclosure:** "
            f"{m.get('pricing_disclosure')}{mix_str}"
        )
        add(
            "  - _Pricing disclosure is the most-open level observed on ≥2 pages "
            "(best-evidence, noise-guarded); the mix shows every observed level. "
            "Corpus-wide read — disclosure can differ by product line._"
        )
        ctas = m.get("dominant_ctas") or {}
        if ctas:
            add(
                "- **Dominant CTAs (share of observed):** "
                + ", ".join(f"{k} {v}" for k, v in ctas.items())
            )
        seg = m.get("segment_focus") or {}
        if seg:
            add(
                "- **Apparent segment focus (by mentions):** "
                + ", ".join(f"{k} ({v})" for k, v in seg.items())
            )
        add("- _Public-signal inference only — not CAC, conversion, or spend._")

    # --- Product positioning (feedback #18) --------------------------------
    positioning = pkg.get("product_positioning") or []
    if positioning:
        add("\n## Product positioning\n")
        add("| Product | Pages | Themes | Personas | Proof |")
        add("|---|---:|---|---|---|")
        for p in positioning[:10]:
            add(
                f"| {p['product'][:28]} | {p['pages']} | {', '.join(p['themes'][:2]) or '—'} | "
                f"{', '.join(p['personas'][:2]) or '—'} | {', '.join(p['proof_types'][:2]) or '—'} |"
            )

    # --- Positioning by product vertical -----------------------------------
    pva = pkg.get("product_vertical_analysis") or {}
    verts = pva.get("verticals") or []
    add(f"\n## Positioning by product vertical ({company})\n")
    if verts:
        add(
            "How the competitor positions in each product category it touches "
            "(keyword-derived mapping — method in JSON `product_vertical_analysis.method`). "
            f"{focal} competes across many of these; per-vertical reads prevent one "
            "category's narrative from masking another's.\n"
        )
        add("| Vertical | Pages/posts | LinkedIn posts | Top themes | Stance mix | Personas |")
        add("|---|---:|---:|---|---|---|")
        for v in verts:
            stance = ", ".join(f"{k}:{n}" for k, n in (v.get("stance_mix") or {}).items()) or "—"
            add(
                f"| {v['vertical'].replace('_', ' ')} | {v['n_artifacts']} | {v['n_linkedin_posts']} | "
                f"{', '.join(v['top_themes']) or '—'} | {stance} | {', '.join(v['personas']) or '—'} |"
            )
    else:
        add("_No product-vertical signals matched this corpus (see taxonomy.product_verticals)._")

    # --- Category entry points (feedback #22) ------------------------------
    ceps = pkg.get("category_entry_points") or []
    if ceps:
        add(f"\n## Category entry points ({company} vs {focal})\n")
        shown = [r for r in ceps if r.get("ownership") != "insufficient_sample"]
        omitted = len(ceps) - len(shown)
        add("| Buying trigger | Competitor | " + focal + " | Ownership | Basis | Verticals |")
        add("|---|---:|---:|---|---|---|")
        for r in shown[:14]:
            tv = ", ".join(str(v).replace("_", " ") for v in (r.get("top_verticals") or [])) or "—"
            cn = f"{r['competitor_pages']}"
            if r.get("competitor_share") is not None:
                cn += f" ({r['competitor_share']:.0%})"
            if r.get("focal_pages") is None:
                fn = "— (no focal mirror)"
            else:
                fn = f"{r['focal_pages']}"
                if r.get("focal_share") is not None:
                    fn += f" ({r['focal_share']:.0%})"
            add(
                f"| {str(r['cep']).replace('_', ' ')} | {cn} | {fn} | "
                f"{r['ownership']} | {r.get('ownership_basis', '—')} | {tv} |"
            )
        if omitted:
            add(
                f"\n_{omitted} low-sample trigger(s) (too few pages on either side to call) "
                "omitted here — full list incl. `insufficient_sample` rows in the JSON/UI._"
            )
        add(
            "\n_Ownership is SHARE-normalized (count ÷ that company's classified corpus) so "
            "different corpus sizes can't fabricate a verdict; the Verticals column shows which "
            "product categories carry each trigger. For a per-vertical read, scope the chat to "
            "that vertical._"
        )

    # --- EDA insight graphics (marketing-ops joins) -------------------------
    ig = pkg.get("insight_graphics") or {}
    if ig:
        add("\n## EDA insights — the joins the single charts can't show\n")
        add(
            "_Deterministic cross-cutting analysis (proof×demand, funnel×vertical, "
            "channel×proof, audience×sitemap); every number carries its denominator. "
            "Full series in the JSON `insight_graphics`; interactive versions in the UI._\n"
        )
        cvr = ig.get("claim_vs_record")
        if cvr:
            c, f = cvr["competitor"], cvr.get("focal")
            add(f"**[{cvr['board_column']}] {cvr['title']}**")
            add(
                f"- {company}: compliance voiced on {c['voice_n']}/{c['n_classified']} pages "
                f"({c['voice_share']:.0%}); certification shown on {c['cert_n']}/{c['voice_n']} "
                f"({c['cert_rate']:.0%}); quantified-outcome stand-in {c['quant_standin_rate']:.0%}"
            )
            if f:
                add(
                    f"- {focal}: voiced {f['voice_n']}/{f['n_classified']} ({f['voice_share']:.0%}); "
                    f"record shown {f['cert_rate']:.0%}"
                )
            for h in cvr.get("cep_hit_list", [])[:2]:
                add(
                    f"- Hit list — {h['cep'].replace('_', ' ')}: {focal} record rate "
                    f"{h['focal']['rate']:.0%} (n={h['focal']['n']}) vs {company} "
                    f"{h['competitor']['rate']:.0%} (n={h['competitor']['n']})"
                )
            for g_ in cvr.get("guardrail", [])[:1]:
                add(
                    f"- Guardrail — {g_['cep'].replace('_', ' ')}: rates match "
                    f"({g_['competitor']['rate']:.0%} vs {g_['focal']['rate']:.0%}) — do not attack there"
                )
            add(f"- **Play:** {cvr['action']}")
        pvv = ig.get("proof_vs_voice")
        if pvv:
            add(f"\n**[{pvv['board_column']}] {pvv['title']}**")
            for r in pvv["rows"][:4]:
                add(
                    f"- {r['cep'].replace('_', ' ')} ({str(r['ownership']).replace('_', ' ')}): "
                    f"{company} quantifies {r['competitor']['rate']:.0%} (n={r['competitor']['n']}) "
                    f"vs {focal} {r['focal']['rate']:.0%} (n={r['focal']['n']})"
                )
            nm = pvv.get("naming", {})
            add(
                f"- Naming war is one-way: {company} names {focal} on "
                f"{nm.get('competitor_names_focal', 0)} pages; {focal} names {company} on "
                f"{nm.get('focal_names_competitor', 0)}"
            )
            add(f"- **Play:** {pvv['action']}")
        fv_ = ig.get("funnel_voids")
        if fv_:
            add(f"\n**[{fv_['board_column']}] {fv_['title']}**")
            for r in [x for x in fv_["rows"] if x.get("void")][:3]:
                cc, ff = r["competitor"], r["focal"]
                add(
                    f"- {r['vertical'].replace('_', ' ')}: {company} 0/{cc['n']} decision "
                    f"(evaluation {cc['evaluation_n']}/{cc['n']}) vs {focal} "
                    f"{ff['decision_n']}/{ff['n']}"
                )
            add(f"- **Play:** {fv_['action']}")
        ad = ig.get("affinity_defense")
        if ad:
            add(f"\n**[{ad['board_column']}] {ad['title']}**")
            for r in ad["rows"][:4]:
                add(
                    f"- {r['domain']}: affinity {r['affinity']:.2f} — "
                    f"{'defended (vs-page exists)' if r['defended'] else 'NO comparison page'} · "
                    f"{r['mentions']} mention(s) in {company}'s classified corpus"
                )
            if ad.get("orphan_comparison_slugs"):
                add(
                    "- vs-pages spent outside the top-affinity audience: "
                    + ", ".join(ad["orphan_comparison_slugs"][:4])
                )
            add(f"- **Play:** {ad['action']} _(affinity = estimated overlap index)_")
        cps = ig.get("channel_proof_split")
        if cps:
            c = cps["competitor"]
            add(f"\n**[{cps['board_column']}] {cps['title']}**")
            add(
                f"- Product demos: LinkedIn {c['demo_linkedin']}/{c['linkedin_n']} "
                f"({c['demo_linkedin'] / max(1, c['linkedin_n']):.0%}) vs website "
                f"{c['demo_web']}/{c['web_n']} ({c['demo_web'] / max(1, c['web_n']):.0%}); "
                f"quantified outcomes flip the other way "
                f"({c['quant_linkedin']}/{c['linkedin_n']} vs {c['quant_web']}/{c['web_n']})"
            )
            add(
                f"- Website friction: {c['no_public_pricing_web']}/{c['web_n']} pages hide "
                f"pricing; {c['no_cta_web']}/{c['web_n']} carry no CTA at all"
            )
            add(f"- **Play:** {cps['action']}")

    # --- Persona × channel matrix (feedback #21) ---------------------------
    mtx = pkg.get("persona_channel_matrix") or {}
    if mtx.get("personas") and mtx.get("channels"):
        add("\n## Persona × channel coverage (observed)\n")
        add(
            "_Corpus-wide aggregation across all product verticals; use the vertical-scoped "
            "chat for per-vertical persona reads._\n"
        )
        add(
            "_Cells are observed-page counts; an empty cell is **not observed**, not proof of absence._\n"
        )
        channels = mtx["channels"][:6]
        add("| Persona | " + " | ".join(channels) + " |")
        add("|---|" + "|".join("---:" for _ in channels) + "|")
        for persona in mtx["personas"][:8]:
            cells = mtx["cells"].get(persona, {})
            add(f"| {persona} | " + " | ".join(str(cells.get(ch, "")) for ch in channels) + " |")

    # --- Competitor LinkedIn employee posts --------------------------------
    posts = pkg.get("linkedin_posts") or []
    _n_posts_shown = min(15, len(posts))
    add(f"\n## {company} LinkedIn employee posts (showing {_n_posts_shown} of {len(posts)})\n")
    if posts:
        add(
            "Individual public posts (Exa-extracted text + real post link), classified. "
            "Click a link to review the post on LinkedIn. Full list in the JSON/UI.\n"
        )
        add("| Author | Theme | Stance | Post | Excerpt |")
        add("|---|---|---|---|---|")
        for p in posts[:15]:
            who = p.get("author") or "?"
            if p.get("author_role"):
                who += f" ({p['author_role']})"
            excerpt = (p.get("excerpt") or "").replace("\n", " ")[:90]
            add(
                f"| {who} | {p.get('theme') or '—'} | {p.get('competitive_stance') or '—'} | "
                f"[link]({p.get('post_url')}) | {excerpt} |"
            )
    else:
        add(
            "_No individual LinkedIn posts were collected this run (needs Exa credits + the "
            "`exa_linkedin` source enabled)._"
        )

    # --- Traffic & channel mix (Similarweb, estimated) ---------------------
    sw = pkg.get("similarweb") or {}
    if sw.get("metrics"):
        label = "Similarweb" if sw.get("data_source") == "similarweb" else "public-web estimate"
        add(f"\n## Traffic & channel mix — {company} ({label}, estimated)\n")
        m = sw["metrics"]
        for key in (
            "estimated_monthly_visits",
            "channel_mix",
            "top_countries",
            "digital_competitors",
        ):
            if key not in m:
                continue
            val = m[key].get("value") if isinstance(m[key], dict) else m[key]
            if key == "digital_competitors" and isinstance(val, list):
                comp_bits = [
                    f"{c.get('domain')} ({float(c.get('affinity', 0)):.2f})"
                    for c in val[:8]
                    if isinstance(c, dict)
                ]
                add(
                    "- **digital competitors (audience affinity):** "
                    + ", ".join(comp_bits)
                    + " _(estimated)_"
                )
            elif key == "estimated_monthly_visits" and isinstance(val, (int, float)):
                add(f"- **estimated monthly visits:** {int(val):,} _(estimated)_")
            else:
                # Shape-aware fallback: channel_mix is a dict, top_countries can
                # be a list of dicts — a raw f-string would print Python reprs.
                if isinstance(val, dict):
                    text = " · ".join(f"{k}: {v}" for k, v in list(val.items())[:8])
                elif isinstance(val, list):
                    text = ", ".join(
                        " ".join(str(x) for x in item.values())
                        if isinstance(item, dict)
                        else str(item)
                        for item in val[:8]
                    )
                else:
                    text = str(val)
                add(f"- **{key.replace('_', ' ')}:** {text} _(estimated)_")
        # Peer-domain enrichments (audience-overlap set) — only peers whose
        # artifact actually carries a visits estimate are listed (honest
        # absence, never a fabricated number).
        peer_bits = [
            f"{p['domain']} ~{int(p['estimated_monthly_visits']):,}/mo"
            for p in pkg.get("similarweb_peers") or []
            if p.get("estimated_monthly_visits") is not None
        ]
        if peer_bits:
            add(
                "- **peer traffic (top-affinity audience-overlap set):** "
                + ", ".join(peer_bits)
                + " _(estimated)_"
            )

    # --- Buyer voice (reviews mining, REVIEWS contract) ---------------------
    bv = pkg.get("buyer_voice") or {}
    if bv.get("n_reviews"):
        add(f"\n## Buyer voice — review pages mined ({bv['n_reviews']})\n")
        if bv.get("objections"):
            add(
                "- **Objections (this sample):** "
                + ", ".join(f"{r['theme']} ({r['n']})" for r in bv["objections"][:4])
            )
        praise_rows = [r for r in bv.get("themes", []) if r.get("sentiment") == "positive"]
        if praise_rows:
            add(
                "- **Praise (their real strengths — don't attack these):** "
                + ", ".join(f"{r['theme']} ({r['n']})" for r in praise_rows[:4])
            )
        if bv.get("switching_triggers"):
            add(
                "- **Switching:** "
                + ", ".join(
                    f"{r['direction'].replace('_', ' ')} {r['theme']} ({r['n']})"
                    for r in bv["switching_triggers"][:3]
                )
            )
        if bv.get("alternatives_considered"):
            add(
                "- **Also evaluated (no switch observed):** "
                + ", ".join(f"{r['theme']} ({r['n']})" for r in bv["alternatives_considered"][:4])
            )
        # Message vs reality: buyer language that CONFIRMS or CONTRADICTS a
        # marketing claim theme. Reviews were mined (n_reviews > 0), so an
        # empty list is an honest finding, never a silent omission.
        if bv.get("message_reality"):
            add("- **Message vs reality (their claims, checked against buyer language):**")
            for r in bv["message_reality"][:3]:
                add(
                    f"  - {str(r['theme']).replace('_', ' ')} — "
                    f"**{str(r['relation']).upper()}** — "
                    f"“{r.get('example_quote', '')}” ([source]({r.get('source_url', '')}))"
                )
        else:
            add(
                "- **Message vs reality:** no review language matched their "
                "marketing claims either way."
            )
        add(f"  - _{bv.get('note', '')}_")

    # --- Observed paid ads: Google Ads Transparency via SerpApi ------------
    sa = pkg.get("serpapi_ads")
    if sa:
        add(f"\n## Observed paid ads (Google Ads Transparency via SerpApi) ({company})\n")
        adv_line = ", ".join(
            x["name"]
            + (f" (verified advertiser id {x['advertiser_id']})" if x.get("advertiser_id") else "")
            for x in sa["advertisers"]
        )
        fm = sa["format_mix"]
        add(f"- **Advertiser (verified):** {adv_line or 'unnamed advertiser'}")
        add(
            f"- **Active creatives observed:** {sa['n_creatives']} "
            f"({fm.get('video', 0)} video / {fm.get('image', 0)} image / {fm.get('text', 0)} text)"
        )
        dr = sa["date_range"]
        if dr.get("earliest_first_shown") or dr.get("latest_last_shown"):
            add(
                "- **Run window (as shown by Transparency):** "
                f"{dr.get('earliest_first_shown') or '?'} → "
                f"{dr.get('latest_last_shown') or '?'} (first_shown → last_shown)"
            )
        if sa["video_examples"]:
            add("- **Example VIDEO ad headlines (real machine-readable API copy):**")
            for ex in sa["video_examples"]:
                add(f"  - “{ex['headline']}” — [Transparency permalink]({ex['permalink']})")
        add(
            "- Image/text creative copy is a rendered image — not machine-readable; "
            f"{sa['non_machine_readable_creatives']} such creative(s) observed "
            "(copy is never OCR'd or invented)."
        )
        add(
            "  - _Google Ads Transparency shows no spend, impressions, CPC, or "
            "performance for commercial ads — none is claimed._"
        )

    # --- Channel alignment: paid vs organic vs employee advocacy -----------
    ca = pkg.get("channel_alignment") or {}
    if ca.get("paid_themes") or ca.get("employee_themes"):
        add(f"\n## Message investment & advocacy alignment ({company})\n")
        if ca.get("paid_themes"):
            add(
                "- **What they PAY to say (ads):** "
                + ", ".join(f"{t} ({n})" for t, n in ca["paid_themes"].items())
                + f" · alignment with website: {ca.get('paid_organic_alignment', 0):.0%}"
            )
            if ca.get("paid_only_themes"):
                add(
                    "  - **Paid-only themes (investment the site doesn't reflect):** "
                    + ", ".join(ca["paid_only_themes"])
                )
        if not ca.get("paid_themes"):
            add(
                "- **Paid messaging:** ad-library artifacts are discovery pointers only — US "
                "commercial ad creative text is not publicly extractable, so no paid-vs-organic "
                "theme comparison is possible (disclosed, not estimated)."
            )
        if ca.get("employee_themes"):
            add(
                "- **What employees amplify (LinkedIn):** "
                + ", ".join(f"{t} ({n})" for t, n in ca["employee_themes"].items())
                + f" · alignment with website: {ca.get('employee_advocacy_alignment', 0):.0%}"
            )
            if ca.get("employee_only_themes"):
                add(
                    "  - **Employee-only themes (the unofficial story):** "
                    + ", ".join(ca["employee_only_themes"])
                )
        add(
            "  - _Theme distributions per channel from classified artifacts — deterministic "
            "counting, not a model judgment. High alignment = disciplined narrative; "
            "divergence = where investment or advocacy runs ahead of the site._"
        )

    # --- Strategy over time (feedback #25) ---------------------------------
    add("\n## Strategy over time\n")
    tb = pkg.get("temporal_baseline") or {}
    if tb.get("prior_window"):
        pw = tb["prior_window"]
        _pw_items = list(pw.get("themes", {}).items())
        _pw_line = ", ".join(f"{t} ({n})" for t, n in _pw_items[:12]) or "none classified"
        if len(_pw_items) > 12:
            _pw_line += f" (+{len(_pw_items) - 12} more in the JSON)"
        add(
            f"**Prior window baseline ({pw['start']} → {pw['end']}, {pw['n_artifacts']} dated "
            f"artifacts):** themes observed then — " + _pw_line
        )
        if tb.get("stable_themes"):
            add("- **Stable (both windows):** " + ", ".join(tb["stable_themes"]))
        if tb.get("emerged_themes"):
            add("- **Emerged (current only):** " + ", ".join(tb["emerged_themes"]))
        if tb.get("receded_themes"):
            add(
                "- **Receded (prior only — possibly de-emphasized):** "
                + ", ".join(tb["receded_themes"])
            )
        add(f"- _{tb.get('note')}_\n")
    if changes:
        # Render ALL events — a silent [:4] cap hid the strongest prior-presence
        # case (cross_system_workflow) from the brief while the dashboard showed it.
        for ch in changes:
            add(
                f"- **{ch['dimension']}** ({ch['lifecycle']}, confidence {ch['confidence']}): "
                f"“{ch['prior_state']}” → “{ch['current_state']}”"
            )
            if ch.get("alternative_explanations"):
                add(f"  - alternatives: {'; '.join(ch['alternative_explanations'][:2])}")
    else:
        snaps = [a for a in pkg["artifacts"] if a["source_type"] == "wayback"]
        snap_dates = sorted(
            {
                str(a.get("archive_capture_at") or a.get("published_at") or "")[:10]
                for a in snaps
                if a.get("archive_capture_at") or a.get("published_at")
            }
        )
        depth = "insufficient_history" if len(snaps) < 3 else "partial"
        add(f"**No confirmed strategic change** (historical support: `{depth}`).")
        add(
            f"- Compared {len(snaps)} archived snapshot(s)"
            + (f" from {', '.join(snap_dates)}" if snap_dates else "")
            + " against current pages; the normalized theme did not differ enough to clear the "
            "both-period bar."
        )
        add(
            "- Pages compared: "
            + (
                ", ".join(
                    sorted(
                        {a.get("metadata", {}).get("original_url", a["url"])[:50] for a in snaps}
                    )
                )
                or "homepage only"
            )
        )
        if len(snaps) < 3:
            add(
                "- **Caveat:** this is too few historical observations to confirm OR rule out a trend. "
                "A deeper archive sample (more snapshots across the window on platform/pricing pages) is needed."
            )
        add(
            "- Unresolved hypotheses a deeper sample could test: whether enterprise/platform language is "
            "increasing; whether API/automation language is broadening."
        )

    # --- Scope, coverage, sources (feedback #7, #8, #9, #36) ---------------
    add("\n## Research scope and coverage\n")
    add(
        f"- Competitor **{company}** · focal **{focal}** · lookback {state.lookback_days}d across {len(state.time_windows)} windows"
    )
    add(
        f"- Artifacts collected: {len(pkg['artifacts'])} · classified: {len(cls)} · claims: {len(pkg['claims'])}"
    )
    _cn = pkg.get("corpus_normalization") or {}
    if _cn.get("show_banner"):
        add(
            f"\n> **Corpus-size asymmetry:** {_cn['competitor']['name']} "
            f"{_cn['competitor']['n_classified']} classified artifacts vs "
            f"{_cn['focal'].get('name') or 'focal'} {_cn['focal']['n_classified']} "
            f"(ratio {_cn.get('asymmetry_ratio')}). {_cn['normalization_note']}\n"
        )
    if pkg["corpus_skew_warnings"]:
        add("- **Corpus-skew warnings:**")
        for w in pkg["corpus_skew_warnings"]:
            add(f"  - {w}")
    add("\n**Source distribution**\n")
    add("| Source | Count |")
    add("|---|---:|")
    for label, n in sorted(pkg["source_distribution"].items(), key=lambda kv: -kv[1]):
        add(f"| {label} | {n} |")
    if pkg["unclassified_artifacts"]:
        add("\n**Not classified**\n")
        for u in pkg["unclassified_artifacts"]:
            add(f"- {u['source_type']} ({u['url'][:60]}) — {u['reason']}")
    add("\n**Coverage (what each rating is based on)**\n")
    add("| Dimension | Level | Basis |")
    add("|---|---|---|")
    for cd in pkg["coverage_detail"]:
        add(f"| {cd['dimension']} | {cd['level']} | {cd['reason']} |")

    # --- Limitations (feedback #32) ----------------------------------------
    add("\n## Limitations and missing data\n")
    limitations = pkg.get("limitations") or []
    if limitations:
        for item in limitations:
            add(f"- {item}")
    else:
        add("- No blocking limitations recorded.")
    # Negative observations ("searched X, found nothing / provider empty") are
    # findings — a brief-only reader must see them, not just the trace (audit).
    negs = list(dict.fromkeys(pkg.get("negative_observations") or []))[:10]
    if negs:
        add("\n**Sources attempted with no usable data (negative observations):**\n")
        for neg in negs:
            add(f"- {neg}")
    add(
        "- Not publicly knowable (excluded): actual ad performance/ROAS, exact spend, negotiated pricing, "
        "complete OOH/employee-post coverage, internal intent."
    )

    # --- Evidence appendix (feedback #10, #31) -----------------------------
    add("\n## Evidence appendix\n")
    focal_ev = pkg.get("focal_evidence") or {}
    focal_arts = focal_ev.get("artifacts") or []
    n_comp, n_focal = len(pkg["artifacts"]), len(focal_arts)
    add(
        f"All {n_comp} competitor ({company}) sources below; all {n_focal} focal ({focal}) "
        f"mirror sources follow so every '{focal} proof: …' rating is traceable within this "
        "deliverable. Claims resolve to evidence IDs (see JSON `claims[].evidence_ids`, "
        "`focal_evidence`). Note: `normalized_text` in the JSON is truncated to 1,500 chars "
        "per artifact for size; full text lives in the run store.\n"
    )
    add(f"**{company} sources ({n_comp})**\n")
    add("| Artifact | Source | Date | URL |")
    add("|---|---|---|---|")
    for a in pkg["artifacts"]:
        date = a.get("archive_capture_at") or a.get("published_at") or a.get("retrieved_at") or ""
        add(
            f"| {a['artifact_id'][:14]} | {a['source_type']} | {str(date)[:10]} | {a['url'][:60]} |"
        )

    if focal_arts:
        add(
            f"\n**{focal} (focal mirror) sources ({n_focal})** — run `{focal_ev.get('run_id', '?')}`\n"
        )
        add("| Artifact | Source | Date | URL |")
        add("|---|---|---|---|")
        for a in focal_arts:
            date = (
                a.get("archive_capture_at") or a.get("published_at") or a.get("retrieved_at") or ""
            )
            add(
                f"| {a['artifact_id'][:14]} | {a['source_type']} | {str(date)[:10]} | {a['url'][:60]} |"
            )
    else:
        add(
            f"\n_No {focal} mirror evidence available in this run — {focal}-proof ratings "
            "are drawn from the focal run's classifications and should be treated as "
            "provisional until the mirror is attached._"
        )

    add(
        f"\n---\n*Stop reason: {_stop_reason_label(state.stop_reason)} "
        f"(raw: `{state.stop_reason}`) · iterations {state.iteration} · "
        f"tool calls {state.tool_calls_made}*"
    )
    return "\n".join(x for x in L if x is not None)


# ---- small render helpers --------------------------------------------------


def _theme_counts(cls: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter

    c: Counter[str] = Counter()
    for x in cls:
        if x.get("primary_theme"):
            c[x["primary_theme"]] += 1
    return c.most_common()


def _bar(share: float | None, width: int = 18) -> str:
    """Text bar chart cell: share of corpus -> proportional block glyphs."""
    if not share or share <= 0:
        return ""
    return "█" * max(1, round(min(1.0, share) * width))


def _short_id(artifact_id: str) -> str:
    return artifact_id[:10] if artifact_id else ""


def _coerce_text(value: Any) -> str:
    """Coerce an untrusted classifier/opportunity field to a plain string before
    it is sliced or string-formatted inside :func:`build_assignment_answers`.

    A schema types a field like ``primary_message`` as ``str | None``, but the
    live pipeline can still hand render a non-string shape (a classifier that
    emits ``primary_message`` as a wrapped object ``{"text": ...}`` /
    ``{"label": ...}`` / ``{"statement": ...}``, or a list of fragments). Doing
    ``value[:220]`` on a dict raises ``KeyError: slice(None, 220, None)`` — the
    exact crash that took down ``render_outputs`` / ``run_focal_mirror_check``.
    Routing every untrusted-string slice/format through this coercion makes the
    render robust by construction: a non-string shape degrades honestly to text
    instead of crashing.

    - ``str``  -> passthrough.
    - ``dict`` -> its ``text``/``label``/``statement`` value when that is a
      string, else a compact JSON dump (never a fabricated summary).
    - ``list``/``tuple`` -> the coerced items joined by a space.
    - ``None`` -> ``""``.
    - anything else -> ``str(value)``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "label", "statement"):
            inner = value.get(key)
            if isinstance(inner, str):
                return inner
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (list, tuple)):
        return " ".join(part for part in (_coerce_text(v) for v in value) if part)
    return str(value)


def build_assignment_answers(pkg: dict[str, Any]) -> dict[str, Any]:
    """The assignment's four questions, answered from the run's own evidence.

    Deterministic composition over the validated package — no model calls.
    EVERY row carries ``citations``: the artifact(s) the statement rests on
    (id, url, source type, retrieval/publish timestamp), so each claim is one
    click from its source. The same structure renders the brief's markdown
    section and ships in data.json (``assignment_answers``).
    """
    companies = pkg.get("companies") or []
    competitor = companies[0].get("canonical_name") if companies else "the competitor"
    focal = companies[1].get("canonical_name") if len(companies) > 1 else "the focal company"
    arts = {a.get("artifact_id"): a for a in pkg.get("artifacts") or []}
    evs = {e.get("evidence_id"): e for e in pkg.get("evidence") or []}
    claims_by_id = {c.get("claim_id"): c for c in pkg.get("claims") or []}

    def cite(artifact_id: str | None) -> dict[str, Any] | None:
        a = arts.get(artifact_id or "")
        if not a:
            return None
        return {
            "artifact_id": a.get("artifact_id"),
            "url": a.get("url"),
            "source_type": a.get("source_type"),
            "timestamp": a.get("published_at")
            or a.get("archive_capture_at")
            or a.get("retrieved_at"),
        }

    def cites_from_evidence(evidence_ids: list[str], limit: int = 2) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for eid in evidence_ids or []:
            e = evs.get(eid)
            if not e:
                continue
            c = cite(e.get("artifact_id"))
            if c:
                c = {
                    **c,
                    "evidence_id": eid,
                    "exact_excerpt": _coerce_text(e.get("exact_excerpt"))[:200],
                }
                out.append(c)
            if len(out) >= limit:
                break
        return out

    def cites_from_claims(claim_ids: list[str], limit: int = 2) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cid in claim_ids or []:
            claim = claims_by_id.get(cid)
            if not claim:
                continue
            out.extend(cites_from_evidence(claim.get("evidence_ids") or [], limit=1))
            if len(out) >= limit:
                break
        return out[:limit]

    cls = pkg.get("classifications") or []
    n_cls = max(1, len(cls))

    def _theme_key(t: Any) -> str:
        """Normalize a theme label for matching: gap short_labels are the
        humanized form ('native platform breadth') of the snake_case taxonomy
        theme ('native_platform_breadth') classifications carry."""
        return "_".join(_coerce_text(t).strip().casefold().replace("-", " ").split())

    def cites_from_theme(theme: str | None, limit: int = 2) -> list[dict[str, Any]]:
        """Highest-salience classified pages carrying the theme — the pages
        the repeated message was actually observed on (distinct artifacts)."""
        key = _theme_key(theme)
        rows = sorted(
            (c for c in cls if key and _theme_key(c.get("primary_theme")) == key),
            key=lambda r: -(r.get("message_salience") or 0),
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            c1 = cite(r.get("artifact_id"))
            if c1 and c1 not in out:
                out.append(c1)
            if len(out) >= limit:
                break
        return out

    # ---- Q1: messaging angles & themes they're running ----------------------
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for c in cls:
        theme = _coerce_text(c.get("primary_theme"))
        if theme:
            by_theme.setdefault(theme, []).append(c)
    theme_rows: list[dict[str, Any]] = []
    for theme, rows in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
        best = max(rows, key=lambda r: r.get("message_salience") or 0)
        theme_rows.append(
            {
                "theme": theme,
                "pages": len(rows),
                "share": round(len(rows) / n_cls, 4),
                "example_message": _coerce_text(best.get("primary_message"))[:220],
                "citations": cites_from_theme(theme),
            }
        )
    posts = pkg.get("linkedin_posts") or []
    li_by_theme: dict[str, list[dict[str, Any]]] = {}
    for p in posts:
        theme = _coerce_text(p.get("theme"))
        if theme:
            li_by_theme.setdefault(theme, []).append(p)
    linkedin_rows = [
        {
            "theme": theme,
            "posts": len(rows),
            "example_excerpt": _coerce_text(rows[0].get("excerpt"))[:180],
            "citations": [
                {
                    "url": rows[0].get("url") or rows[0].get("post_url"),
                    "source_type": "linkedin_post",
                    "timestamp": rows[0].get("published_at") or rows[0].get("posted_at"),
                }
            ],
        }
        for theme, rows in sorted(li_by_theme.items(), key=lambda kv: -len(kv[1]))[:3]
    ]
    q1 = {
        "question": "What messaging angles and themes are they running?",
        "themes": theme_rows[:10],
        "linkedin_amplification": linkedin_rows,
        "method_note": (
            "Each collected page/post was individually classified; a theme row "
            "counts pages whose PRIMARY theme matched. Share is of the "
            f"{len(cls)} classified {competitor} pages. The example message is "
            "the classifier's summary of the highest-salience page for that "
            "theme — the citation opens the page itself."
        ),
    }

    # ---- Q2: how they position their product(s) -----------------------------
    dom = pkg.get("dominant_message") or {}
    dom_theme_rows = by_theme.get(_coerce_text(dom.get("theme")), [])
    dom_cites = [
        c
        for c in (
            cite(r.get("artifact_id"))
            for r in sorted(dom_theme_rows, key=lambda r: -(r.get("message_salience") or 0))[:2]
        )
        if c
    ]
    villain_rows: list[dict[str, Any]] = []
    seen_villains: set[str] = set()
    for c in cls:
        for raw_wording in c.get("villain_exact_wording") or []:
            wording = _coerce_text(raw_wording)
            key = " ".join(wording.split()).casefold()
            if key and key not in seen_villains:
                seen_villains.add(key)
                vc = cite(c.get("artifact_id"))
                villain_rows.append({"verbatim": wording, "citations": [x for x in [vc] if x]})
        if len(villain_rows) >= 6:
            break
    product_rows: list[dict[str, Any]] = []
    for p in (pkg.get("product_positioning") or [])[:10]:
        example = next(
            (c for c in cls if p.get("product") in (c.get("products") or [])),
            None,
        )
        pc = cite(example.get("artifact_id")) if example else None
        product_rows.append(
            {
                "product": p.get("product"),
                "pages": p.get("pages"),
                "themes": (p.get("themes") or [])[:3],
                "personas": (p.get("personas") or [])[:3],
                "proof_types": (p.get("proof_types") or [])[:3],
                "citations": [x for x in [pc] if x],
            }
        )
    q2 = {
        "question": "How do they position their product(s)?",
        "dominant_message": {
            "label": dom.get("label"),
            "theme": dom.get("theme"),
            "justification": dom.get("reason"),
            "citations": dom_cites,
        },
        "villain_wording": villain_rows,
        "products": product_rows,
        "method_note": (
            "Positioning is counted, not summarized: the dominant message must "
            "recur across surfaces; villain wording is verbatim from the cited "
            "pages; the product table maps every classified page to the "
            "product(s) it sells."
        ),
    }

    # ---- Q3: what's changed recently ----------------------------------------
    rank = {"high": 3, "medium": 2, "low": 1}
    events = sorted(
        pkg.get("change_events") or [],
        key=lambda e: (-rank.get(str(e.get("confidence")), 0), str(e.get("apparent_change_at"))),
    )
    change_rows = [
        {
            "what": e.get("theme") or e.get("dimension"),
            "dimension": e.get("dimension"),
            "prior_state": e.get("prior_state"),
            "current_state": e.get("current_state"),
            "apparent_change_at": e.get("apparent_change_at"),
            "confidence": e.get("confidence"),
            "lifecycle": e.get("lifecycle"),
            "citations": cites_from_evidence(e.get("current_evidence_ids") or []),
        }
        for e in events[:8]
    ]
    # ICP read: persona mix in prior-window vs current-window artifacts.
    windows = {
        w.get("window_id"): w.get("purpose")
        for w in (pkg.get("scope") or {}).get("time_windows") or []
    }
    persona_counts: dict[str, dict[str, int]] = {"prior": {}, "current": {}}
    window_n = {"prior": 0, "current": 0}
    for c in cls:
        a = arts.get(c.get("artifact_id") or "")
        if not a:
            continue
        purposes = {windows.get(tw) for tw in a.get("time_window_ids") or []}
        bucket = (
            "current" if "current" in purposes else ("prior" if "comparison" in purposes else None)
        )
        if not bucket:
            continue
        window_n[bucket] += 1
        for persona in c.get("personas") or []:
            persona_counts[bucket][persona] = persona_counts[bucket].get(persona, 0) + 1
    icp_rows: list[dict[str, Any]] = []
    if window_n["prior"] >= 3:
        # Union of both windows: a persona they STOPPED targeting (present
        # prior, absent current) is as much an ICP shift as a newly added one.
        for persona in set(persona_counts["current"]) | set(persona_counts["prior"]):
            cur_share = persona_counts["current"].get(persona, 0) / max(1, window_n["current"])
            pri_share = persona_counts["prior"].get(persona, 0) / max(1, window_n["prior"])
            delta = cur_share - pri_share
            if abs(delta) >= 0.15:
                icp_rows.append(
                    {
                        "persona": persona,
                        "prior_share": round(pri_share, 3),
                        "current_share": round(cur_share, 3),
                        "delta": round(delta, 3),
                    }
                )
        icp_rows = sorted(icp_rows, key=lambda r: -abs(r["delta"]))[:4]
    q3 = {
        "question": "What's changed recently (new campaigns, new ICPs targeted, messaging pivots)?",
        "verified_changes": change_rows,
        "icp_shift_signals": icp_rows,
        "icp_note": (
            f"Persona mix compared across dated windows (prior n={window_n['prior']}, "
            f"current n={window_n['current']} classified pages). "
            + (
                "Prior-window sample is too thin for ICP deltas — not asserted."
                if window_n["prior"] < 3
                else "Deltas under 15 points are not reported; archive coverage "
                "is sparser than the live site, so treat these as signals."
            )
        ),
        "method_note": (
            "Every change event needed evidence in BOTH windows or is labeled "
            "by its lifecycle (emerging/expanding); events were re-reconciled "
            "against the final corpus. Citations open the current-window "
            "evidence behind each change."
        ),
    }

    # ---- Q4: gaps & what we'd exploit ----------------------------------------
    gap_rows: list[dict[str, Any]] = []
    gap_cites_by_id: dict[str, list[dict[str, Any]]] = {}
    for g in (pkg.get("proof_gaps") or [])[:6]:
        # Citation chain: the claims ledger, then the gap's strongest proof
        # evidence, then the highest-salience pages of the theme the repeated
        # claim was counted on — always the run's own evidence, never invented.
        g_cites = cites_from_claims([g["claim_id"]] if g.get("claim_id") else [])
        if not g_cites and g.get("strongest_proof_id"):
            g_cites = cites_from_evidence([g["strongest_proof_id"]])
        if not g_cites:
            g_cites = cites_from_theme(g.get("theme") or g.get("short_label"))
        if g.get("claim_id"):
            gap_cites_by_id[str(g["claim_id"])] = g_cites
        gap_rows.append(
            {
                "their_claim": _coerce_text(g.get("claim_text"))[:180],
                "attackability": g.get("attackability"),
                "their_proof": g.get("proof_strength"),
                f"{focal.lower()}_proof": g.get("focal_proof_strength"),
                "justification": _coerce_text(
                    (g.get("attackability_detail") or {}).get("rationale")
                    or g.get("actionable_interpretation")
                )[:220],
                "citations": g_cites,
            }
        )
    plays: list[dict[str, Any]] = []
    for o in (pkg.get("opportunities") or [])[:5]:
        support = [str(cid) for cid in o.get("supporting_claim_ids") or []]
        o_cites = cites_from_claims(support)
        if not o_cites:
            # supporting ids may reference gap records rather than raw claims —
            # inherit the cited pages of the gap the play attacks.
            seen_urls: set[str] = set()
            for cid in support:
                for c1 in gap_cites_by_id.get(cid, []):
                    u = str(c1.get("url") or "")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        o_cites.append(c1)
            o_cites = o_cites[:2]
        plays.append(
            {
                "play": o.get("title"),
                "message_angle": o.get("message_angle"),
                "target_personas": (o.get("target_personas") or [])[:3],
                "primary_metric": o.get("primary_metric"),
                "kill_rule": o.get("kill_rule"),
                "justification": _coerce_text(
                    o.get("competitor_pattern") or o.get("experiment_hypothesis")
                )[:240],
                "citations": o_cites,
            }
        )
    q4 = {
        "question": (
            f"What positioning gaps or opportunities does this surface for {focal}'s "
            "marketing — and what would we exploit writing campaigns against them?"
        ),
        "message_proof_gaps": gap_rows,
        "campaign_plays": plays,
        "method_note": (
            "Gaps score every repeated competitor claim against observed proof "
            "on BOTH sides (share-normalized for corpus size; thin themes are "
            "flagged, never inflated). Each play ships with its metric and a "
            "kill rule — a campaign board, not a book report."
        ),
    }

    return {
        "note": (
            "The assignment's four questions answered from this run's collected "
            "evidence. Every row carries citations (artifact id, URL, source "
            "type, timestamp); confidence levels and the full source/claim "
            "ledgers live alongside in this file."
        ),
        "competitor": competitor,
        "focal_company": focal,
        "generated_at": pkg.get("run", {}).get("generated_at"),
        "q1_messaging_themes": q1,
        "q2_product_positioning": q2,
        "q3_recent_changes": q3,
        "q4_gaps_and_opportunities": q4,
    }


def _md_url(url: str) -> str:
    """Percent-encode the characters that would break a markdown link or a
    table cell (competitor URLs are untrusted input, not layout)."""
    return (
        url.replace("|", "%7C")
        .replace("(", "%28")
        .replace(")", "%29")
        .replace("\n", "%0A")
        .replace(" ", "%20")
    )


def _md_cites(citations: list[dict[str, Any]]) -> str:
    """Render citation dicts as markdown source links."""
    links = [
        f"[{_safe_cell(_short_id(str(c.get('artifact_id') or 'src')))}]({_md_url(str(c['url']))})"
        for c in citations or []
        if c.get("url")
    ]
    return " ".join(links) if links else "—"


def _safe_cell(text: Any, limit: int = 0) -> str:
    """Markdown-table-safe cell from an untrusted package string: pipes and
    newlines are stripped (a `|` inside a competitor claim must never break
    the table), whitespace collapsed, optionally truncated with an ellipsis."""
    s = " ".join(str(text if text is not None else "").replace("|", " ").split())
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s or "—"


def _pct(share: Any) -> str:
    """Honest percent cell: small nonzero shares keep one decimal so a 0.4%
    theme never renders as '0%'."""
    v = float(share or 0)
    return f"{v:.1%}" if 0 < v < 0.10 else f"{v:.0%}"


def _assignment_section_lines(pkg: dict[str, Any]) -> list[str]:
    """The brief's four-question deliverable section, rendered EXCLUSIVELY from
    ``pkg['assignment_answers']`` (the composer is the single source of truth —
    nothing here recomputes from raw package fields). Every cell of untrusted
    competitor text passes through ``_safe_cell``; empty lists render honest
    one-liners, never empty tables."""
    aa = pkg["assignment_answers"]
    competitor = aa.get("competitor") or "the competitor"
    focal = aa.get("focal_company") or "the focal company"
    L: list[str] = []
    add = L.append

    add("\n## The assignment deliverable — four questions, answered from the evidence\n")
    add(
        f"The assignment's four questions, answered directly from the evidence this run "
        f"collected on {competitor}. Every claim below carries its source citations — each "
        f"`[ART-…](link)` is an artifact id resolving to the cited URL — and the same answers "
        f"ship structured in `data.json` under `assignment_answers`. The Evidence appendix at "
        f"the end of this brief holds the full source ledger (every artifact with its retrieval "
        f"timestamp)."
    )

    # --- Q1: messaging angles & themes --------------------------------------
    q1 = aa.get("q1_messaging_themes") or {}
    add("\n### 1. What messaging angles and themes are they running?\n")
    themes = q1.get("themes") or []
    if themes:
        add("| Theme | Pages | Share | Investment | Example message (from their page) | Sources |")
        add("|---|---:|---:|---|---|---|")
        for t in themes:
            share = float(t.get("share") or 0)
            add(
                f"| {_safe_cell(t.get('theme'))} | {int(t.get('pages') or 0)} | {_pct(share)} | "
                f"{_bar(share)} | {_safe_cell(t.get('example_message'), 160)} | "
                f"{_md_cites(t.get('citations') or [])} |"
            )
    else:
        add("- Classified messaging themes: none observed on this run.")
    li = q1.get("linkedin_amplification") or []
    if li:
        add("\n**LinkedIn amplification (employee posts echoing the themes):**\n")
        for row in li:
            first = (row.get("citations") or [{}])[0]
            link = f" ([post]({_md_url(str(first['url']))}))" if first.get("url") else ""
            add(
                f"- **{_safe_cell(row.get('theme'))}** — {int(row.get('posts') or 0)} post(s): "
                f"“{_safe_cell(row.get('example_excerpt'), 180)}”{link}"
            )
    else:
        add("\n- LinkedIn amplification: none observed on this run.")
    if q1.get("method_note"):
        add(f"\n_{q1['method_note']}_")

    # --- Q2: product positioning ---------------------------------------------
    q2 = aa.get("q2_product_positioning") or {}
    add("\n### 2. How do they position their product(s)?\n")
    dm = q2.get("dominant_message") or {}
    if dm.get("label"):
        just = f" — _{_safe_cell(dm.get('justification'))}_" if dm.get("justification") else ""
        add(
            f"- **Dominant message:** {_safe_cell(dm.get('label'))}{just} · "
            f"Sources: {_md_cites(dm.get('citations') or [])}"
        )
    else:
        add("- Dominant message: none observed on this run.")
    villains = q2.get("villain_wording") or []
    if villains:
        add("- **Villain / status-quo wording (verbatim from their pages):**")
        for v in villains:
            add(f"  - “{_safe_cell(v.get('verbatim'))}” — {_md_cites(v.get('citations') or [])}")
    else:
        add("- Villain / status-quo wording: none observed on this run.")
    products = q2.get("products") or []
    if products:
        add("")
        add("| Product | Pages | Themes | Personas | Proof | Sources |")
        add("|---|---:|---|---|---|---|")
        for p in products:
            add(
                f"| {_safe_cell(p.get('product'), 32)} | {int(p.get('pages') or 0)} | "
                f"{_safe_cell(', '.join(p.get('themes') or []))} | "
                f"{_safe_cell(', '.join(p.get('personas') or []))} | "
                f"{_safe_cell(', '.join(p.get('proof_types') or []))} | "
                f"{_md_cites(p.get('citations') or [])} |"
            )
    else:
        add("- Product-level positioning rows: none observed on this run.")
    if q2.get("method_note"):
        add(f"\n_{q2['method_note']}_")

    # --- Q3: recent changes ---------------------------------------------------
    q3 = aa.get("q3_recent_changes") or {}
    add("\n### 3. What's changed recently (new campaigns, new ICPs targeted, messaging pivots)?\n")
    changes = q3.get("verified_changes") or []
    if changes:
        add("| What moved | Prior → Current | When | Confidence | Lifecycle | Sources |")
        add("|---|---|---|---|---|---|")
        for e in changes:
            transition = (
                f"{_safe_cell(e.get('prior_state'), 70)} → {_safe_cell(e.get('current_state'), 70)}"
            )
            add(
                f"| {_safe_cell(e.get('what'))} | {transition} | "
                f"{_safe_cell(str(e.get('apparent_change_at') or '')[:10])} | "
                f"{_safe_cell(e.get('confidence'))} | {_safe_cell(e.get('lifecycle'))} | "
                f"{_md_cites(e.get('citations') or [])} |"
            )
    else:
        add("- Verified change events: none observed on this run.")
    icp = q3.get("icp_shift_signals") or []
    if icp:
        add(
            "\n**ICP-shift signals (persona share of classified pages, prior → current window):**\n"
        )
        for r in icp:
            delta_pts = float(r.get("delta") or 0) * 100
            add(
                f"- **{_safe_cell(r.get('persona'))}**: {_pct(r.get('prior_share'))} → "
                f"{_pct(r.get('current_share'))} ({delta_pts:+.0f} pts)"
            )
        if q3.get("icp_note"):
            add(f"\n_{q3['icp_note']}_")
    elif q3.get("icp_note"):
        add(f"- ICP-shift signals: none cleared the reporting bar. {q3['icp_note']}")
    if q3.get("method_note"):
        add(f"\n_{q3['method_note']}_")

    # --- Q4: gaps & campaign plays ---------------------------------------------
    q4 = aa.get("q4_gaps_and_opportunities") or {}
    add(f"\n### 4. What gaps does this surface for {focal} — and what we'd exploit\n")
    gaps = q4.get("message_proof_gaps") or []
    focal_proof_key = f"{focal.lower()}_proof"
    if gaps:
        add(
            f"| Their claim | Attackability | Their proof | {focal} proof | "
            f"Why it's open | Sources |"
        )
        add("|---|---|---|---|---|---|")
        for g in gaps:
            add(
                f"| {_safe_cell(g.get('their_claim'), 90)} | {_safe_cell(g.get('attackability'))} | "
                f"{_safe_cell(g.get('their_proof'))} | {_safe_cell(g.get(focal_proof_key))} | "
                f"{_safe_cell(g.get('justification'), 140)} | {_md_cites(g.get('citations') or [])} |"
            )
    else:
        add("- Message–proof gaps: none observed on this run.")
    plays = q4.get("campaign_plays") or []
    add("\n**What we'd run against them**\n")
    if plays:
        for p in plays:
            personas = ", ".join(p.get("target_personas") or [])
            add(
                f"- **{_safe_cell(p.get('play'))}** — {_safe_cell(p.get('message_angle'))} · "
                f"target: {_safe_cell(personas)} · metric: {_safe_cell(p.get('primary_metric'))} · "
                f"kill rule: {_safe_cell(p.get('kill_rule'))}"
            )
            just = f"_{_safe_cell(p.get('justification'))}_ · " if p.get("justification") else ""
            add(f"  - {just}Sources: {_md_cites(p.get('citations') or [])}")
    else:
        add("- Campaign plays: none survived the critics on this run.")
    if q4.get("method_note"):
        add(f"\n_{q4['method_note']}_")
    add(
        "\n_Full staged campaign plans (audiences, channels, proceed/stop gates) are in the "
        "Action Board section below; the structured mirror of this whole section is "
        "`assignment_answers` in `data.json`._"
    )
    return L


def _positioning_oneliner(cls: list[dict], company: str) -> str:
    themes = _theme_counts(cls)
    if not themes:
        return "insufficient classified evidence to summarize positioning"
    top = ", ".join(t for t, _ in themes[:3])
    return f"{company}'s public pages cluster around {top}"


def _villain_wording(cls: list[dict]) -> dict[str, Any]:
    exact: list[str] = []
    normalized: set[str] = set()
    for x in cls:
        for w in x.get("villain_exact_wording", []) or []:
            if w and w not in exact:
                exact.append(w)
        for v in x.get("villain_normalized", []) or []:
            normalized.add(v)
    return {"exact": exact, "normalized": normalized}


def _stance_distribution(cls: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter

    c: Counter[str] = Counter()
    for x in cls:
        s = x.get("competitive_stance")
        if s:
            c[s] += 1
    return c.most_common()


def _largest_uncertainty(pkg: dict) -> str:
    """Derived ONLY from data already in the package so it can never contradict
    the source table (red-team #3: the old hardcoded fallback said paid
    media/social 'were not collected' while the same brief listed 30 LinkedIn
    posts and 8 ads)."""
    if pkg["corpus_skew_warnings"]:
        return pkg["corpus_skew_warnings"][0]
    lows = [cd for cd in pkg["coverage_detail"] if cd["level"] in ("low", "unavailable")]
    if lows:
        return f"coverage is thin on {', '.join(cd['dimension'] for cd in lows[:3])}"
    nas = [cd for cd in pkg["coverage_detail"] if cd["level"] == "not_attempted"]
    if nas:
        return (
            f"{len(nas)} dimensions were not attempted this run "
            f"({', '.join(cd['dimension'] for cd in nas[:3])}…)"
        )
    tb = pkg.get("temporal_baseline") or {}
    n_prior = (tb.get("prior_window") or {}).get("n_artifacts", 0)
    n_current = (tb.get("current_window") or {}).get("n_artifacts", 0)
    if n_prior and n_current and n_prior * 4 <= n_current:
        return (
            f"the prior window holds only {n_prior} dated artifacts vs {n_current} current — "
            "temporal reads are asymmetric"
        )
    return "no single dominant uncertainty; see the coverage table"


_STOP_REASON_LABELS = {
    "runtime_exhausted": "budget-bounded (hit the research-time cap)",
    "budget_exhausted": "budget-bounded (hit the spend cap)",
    "max_iterations_reached": "budget-bounded (iteration cap)",
    "tool_call_cap_reached": "budget-bounded (tool-call cap)",
    "required_coverage_reached": "completed (required coverage reached)",
}


def _stop_reason_label(reason: str | None) -> str:
    """Human phrasing for the stop enum — 'runtime_exhausted' reads as failure
    to an exec when it actually means 'ran to its research budget'."""
    r = str(reason or "")
    if r in _STOP_REASON_LABELS:
        return _STOP_REASON_LABELS[r]
    if r.startswith("required_dimensions_unavailable_after_exhaustion"):
        return "completed (remaining required dimensions publicly unavailable)"
    if r.startswith("no_remaining_actions_with_expected_value"):
        return "completed (no further actions with expected value)"
    if r.startswith("node_error") or r.startswith("unknown_node"):
        return "stopped on an internal error"
    return r or "unknown"


def _derived_limitations(state: DirectorState, coverage: dict[str, str], pkg: dict) -> list[str]:
    """Merge recorded limitations with render-derived ones. A budget-bounded
    stop with unattempted dimensions IS a limitation even when no collection
    step actively failed (red-team #3: 'No blocking limitations recorded' next
    to runtime_exhausted + 11 untouched dims)."""
    out = list(state.limitations)
    unattempted = sorted(d for d, level in coverage.items() if level == "not_attempted")
    if str(state.stop_reason or "").startswith(
        ("runtime_exhausted", "budget_exhausted", "max_iterations", "tool_call_cap")
    ):
        n_sources = len({a.get("source_type") for a in pkg.get("artifacts", [])})
        item = (
            f"Run ended {_stop_reason_label(state.stop_reason)} after {state.iteration} "
            f"iterations covering {n_sources} source classes"
        )
        if unattempted:
            item += (
                f"; {len(unattempted)} dimensions remain unattempted: "
                f"{', '.join(unattempted[:8])}" + ("…" if len(unattempted) > 8 else "")
            )
        out.append(item)
    elif unattempted:
        out.append(
            f"{len(unattempted)} dimensions not attempted this run: {', '.join(unattempted[:8])}"
            + ("…" if len(unattempted) > 8 else "")
        )
    return list(dict.fromkeys(out))


def _reconcile_negatives(negatives: list[str], pkg: dict, ads_junk_excluded: int) -> list[str]:
    """Annotate (never drop) negative observations contradicted by later
    successes (red-team #4: 'Similarweb … nothing synthesized' shipped next to
    a fully populated Similarweb block)."""
    out: list[str] = []
    similarweb_ok = bool((pkg.get("similarweb") or {}).get("metrics"))
    for neg in dict.fromkeys(negatives):
        if similarweb_ok and "similarweb" in neg.lower() and "superseded" not in neg:
            neg += " (superseded: a later Similarweb call succeeded — see Traffic & channel mix)"
        out.append(neg)
    if ads_junk_excluded:
        out.append(
            f"{ads_junk_excluded} google_ads discovery artifacts excluded at render "
            "(FAQ/blank/other-advertiser transparency pages); retained /advertiser pages "
            "are unverified discovery pointers"
        )
    return out


_LINKEDIN_WALL_MARKERS = (
    "Agree & Join LinkedIn",
    "Agree & Join",
    "Sign in to view",
    "By clicking Continue",
    "Join now Sign in",
    "Skip to main content",
    "Join LinkedIn",
)
_LINKEDIN_HEADER_RX = None  # compiled lazily


def _clean_linkedin_excerpt(raw: str, fallback: str | None) -> str:
    """Strip LinkedIn login-wall boilerplate and share-card headers from a post
    excerpt (red-team: 16/30 excerpts were 'Agree & Join…' walls, not posts).
    Falls back to the classifier's primary_message when nothing survives."""
    global _LINKEDIN_HEADER_RX
    import re as _re

    if _LINKEDIN_HEADER_RX is None:
        _LINKEDIN_HEADER_RX = _re.compile(
            r"(^#{1,2}\s*(Post by\s+)?[^\n]*·\s*LinkedIn[^\n]*$)|"
            r"(\|\s*[^|\n]*posted on the topic\s*\|\s*LinkedIn.*)",
            _re.MULTILINE,
        )
    text = raw or ""
    for marker in _LINKEDIN_WALL_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
    text = _LINKEDIN_HEADER_RX.sub(" ", text)
    text = _re.sub(r"\s+", " ", text).strip()
    if len(text) < 40:
        return (fallback or "")[:280]
    return text[:280]


def render_run_outputs(state: DirectorState, ctx: GraphContext) -> dict[str, Path]:
    out = run_output_dir(state, ctx)
    package = build_json_package(state, ctx)
    json_path = out / "data.json"
    json_path.write_text(json.dumps(package, indent=2, default=str), encoding="utf-8")
    md_path = out / "brief.md"
    md_path.write_text(render_markdown(state, package), encoding="utf-8")
    return {"json": json_path, "markdown": md_path, "dir": out}
