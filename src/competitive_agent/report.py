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

JSON_SCHEMA_VERSION = "1.3.0"


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
            return {
                "domain": meta.get("domain"),
                "data_source": meta.get("data_source", "similarweb"),
                "observation_period": meta.get("observation_period"),
                "estimated": True,
                "metrics": meta.get("metrics", {}),
                "url": a.get("url"),
            }
    return {}


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
                f"{comp_name} owns {own['competitor_advantage']} buying intents to "
                f"{focal_name}'s {own['focal_owns']}, with {own['contested']} contested"
            )
        n_moving = sum(
            1 for c in change_events if c.get("lifecycle") in ("emerging", "expanding")
        )
        if n_moving:
            bits.append(f"{n_moving} theme(s) are gaining ground in their messaging")
        elif change_events is not None:
            bits.append("their core story is static")
        verbs = [
            (g.get("attackability_detail") or {}).get("overall")
            or ("attack" if g.get("attackability") == "high" else "investigate")
            for g in data["proof_gaps"]
        ]
        n_attack = sum(1 for v in verbs if v == "attack")
        if data["proof_gaps"]:
            bits.append(
                f"{n_attack} of {len(data['proof_gaps'])} repeated claims are clean attack "
                "openings" + ("" if n_attack else " — build proof before attacking")
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
        return line

    return {
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
