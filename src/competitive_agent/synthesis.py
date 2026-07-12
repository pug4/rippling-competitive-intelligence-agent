"""Cross-cutting synthesis helpers that raise strategic fidelity (feedback P0).

These turn a website-heavy corpus into *honest* company-level conclusions:
authority-weighted positioning (not raw frequency), corpus-skew detection,
source distribution, per-dimension coverage detail, and proof distributions.
Shared by the comparison engine, the graph nodes, and the report renderer so
the same rules apply everywhere.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .schemas.artifact import RawArtifact
from .schemas.classification import MarketingClassification
from .schemas.opportunity import CoverageDetail, ProofDistribution

# Marketing-surface authority (feedback #1): homepage/platform/product pages are
# company-level; blog/docs/support are product- or topic-level. A detailed
# tax-form claim on a blog page must not become the "dominant company message".
_SURFACE_AUTHORITY: dict[str, float] = {
    "home": 1.0,
    "platform": 0.95,
    "product": 0.85,
    "pricing": 0.7,
    "comparison": 0.6,
    "customers": 0.55,
    "segment": 0.5,
    "press": 0.4,
    "news": 0.4,
    "changelog": 0.3,
    "about": 0.3,
    "other": 0.15,
}
_SOURCE_TYPE_AUTHORITY: dict[str, float] = {
    "webpage": 0.8,  # refined by page_category below
    "sitemap": 0.0,
    "wayback": 0.5,
    "news": 0.4,
    "comparison": 0.6,
    "exa_web": 0.35,
}

# Proof types that count as genuinely strong.
_STRONG_PROOF = {
    "quantified_customer_outcome",
    "independent_validation",
    "product_demonstration",
    "named_customer_story",
    "certification_or_compliance_record",
}
_MODERATE_PROOF = {"customer_quotation", "customer_logo"}


def _url_path(url: str) -> str:
    from urllib.parse import urlsplit

    # Strip a wayback prefix if present, then take the path.
    if "web.archive.org" in url and "/https://" in url:
        url = url.split("id_/", 1)[-1] if "id_/" in url else url
    return (urlsplit(url).path or "/").rstrip("/") or "/"


def _path_surface(path: str) -> str | None:
    """Map a URL path to a marketing surface, independent of source_type — so an
    Exa-discovered or archived homepage is still recognized as the homepage
    (reviewer R2: an exa_web homepage must not be flattened to 0.35)."""
    if path in ("", "/"):
        return "home"
    first = path.strip("/").split("/")[0].lower()
    if first in ("platform",):
        return "platform"
    if first in ("pricing", "plans"):
        return "pricing"
    if first in ("products", "product", "solutions", "solution"):
        return "product"
    if first in ("customers", "customer", "case-studies"):
        return "customers"
    return None


def artifact_authority(artifact: RawArtifact) -> float:
    """0..1 weight for how company-representative a surface is (feedback #1).

    Authority is driven by the URL PATH first (a homepage is a homepage whether
    fetched first-party, discovered via Exa, or archived), then by page_category,
    then by source type. This stops a niche product page out-weighting the real
    homepage just because the homepage arrived as an ``exa_web`` result (R2).
    """
    if artifact.source_type == "sitemap":
        return 0.0
    surface = _path_surface(_url_path(artifact.final_url or artifact.url))
    category = str(artifact.metadata.get("page_category", "")).lower()
    if surface and surface in _SURFACE_AUTHORITY:
        base = _SURFACE_AUTHORITY[surface]
        # A first-party fetch of the surface is fully authoritative; an archived
        # or Exa-discovered copy of the same surface is slightly discounted.
        if artifact.source_type == "webpage":
            return base
        if artifact.source_type in ("wayback", "exa_web"):
            return max(base * 0.85, _SOURCE_TYPE_AUTHORITY.get(artifact.source_type, 0.2))
    if artifact.source_type == "webpage" and category in _SURFACE_AUTHORITY:
        return _SURFACE_AUTHORITY[category]
    return _SOURCE_TYPE_AUTHORITY.get(artifact.source_type, 0.2)


def _artifacts_by_id(artifacts: list[RawArtifact]) -> dict[str, RawArtifact]:
    return {a.artifact_id: a for a in artifacts}


def dominant_message(
    classifications: list[MarketingClassification], artifacts: list[RawArtifact]
) -> dict[str, Any]:
    """Authority-weighted dominant theme + whether it qualifies as *company*
    level (feedback #1): repeated across ≥2 source classes AND present on a
    top-level marketing surface. Otherwise it's only 'most prominent in the
    collected corpus', not the company's dominant message.
    """
    by_id = _artifacts_by_id(artifacts)
    theme_weight: dict[str, float] = {}
    theme_surfaces: dict[str, set[str]] = {}
    theme_source_classes: dict[str, set[str]] = {}
    theme_label: dict[str, str] = {}
    theme_best: dict[str, float] = {}
    for c in classifications:
        theme = c.primary_theme or c.primary_message
        if not theme:
            continue
        art = by_id.get(c.artifact_id)
        authority = artifact_authority(art) if art else 0.2
        sal = c.message_salience if c.message_salience is not None else 0.5
        weight = authority * (0.5 + sal)
        theme_weight[theme] = theme_weight.get(theme, 0.0) + weight
        # Recognize the surface by URL PATH first (a discovered/archived homepage
        # is still the homepage), then page_category, then source type.
        surface = None
        if art:
            surface = (
                _path_surface(_url_path(art.final_url or art.url))
                or str(art.metadata.get("page_category")) or None
                or art.source_type
            )
        theme_surfaces.setdefault(theme, set()).add(surface or "unknown")
        theme_source_classes.setdefault(theme, set()).add(art.source_type if art else "unknown")
        # The human-readable LABEL comes from the page that best REPRESENTS the
        # theme: authority AND salience together, with a salience floor so a
        # very-low-salience niche page (e.g. a mobility page at 0.24) cannot
        # supply the label even if its surface authority is high (R1).
        if sal >= 0.35:
            label_score = authority * (0.4 + sal)
            if label_score > theme_best.get(theme, -1):
                theme_best[theme] = label_score
                theme_label[theme] = c.primary_message or theme

    if not theme_weight:
        return {"theme": None, "is_company_level": False, "label": None, "reason": "no themes"}

    top = max(theme_weight, key=lambda t: theme_weight[t])
    # Fallback label if no page cleared the salience floor for the top theme.
    if top not in theme_label:
        for c in classifications:
            if (c.primary_theme or c.primary_message) == top and c.primary_message:
                theme_label[top] = c.primary_message
                break
    # Company-level requires a genuine home/platform surface — NOT a product page
    # alone (R2): 6 product pages must not certify a company-level claim.
    company_surfaces = {"home", "platform"}
    on_company_surface = bool(theme_surfaces[top] & company_surfaces)
    multi_source = len(theme_source_classes[top]) >= 2
    is_company_level = on_company_surface and multi_source
    return {
        "theme": top,
        "label": theme_label.get(top, top),
        "is_company_level": is_company_level,
        "surfaces": sorted(theme_surfaces[top]),
        "source_classes": sorted(theme_source_classes[top]),
        "reason": (
            "repeated across ≥2 source classes and present on a top-level surface"
            if is_company_level
            else "most prominent in the collected corpus, but not confirmed on a top-level "
            "surface across multiple source classes"
        ),
    }


def corpus_skew(artifacts: list[RawArtifact]) -> list[str]:
    """Warn when the corpus is too concentrated to support company-level claims
    (feedback #36)."""
    warnings: list[str] = []
    collectible = [a for a in artifacts if a.source_type != "sitemap"]
    n = len(collectible)
    if n == 0:
        return ["corpus is empty"]
    by_source = Counter(a.source_type for a in collectible)
    if by_source.most_common(1)[0][1] / n > 0.6:
        s, cnt = by_source.most_common(1)[0]
        warnings.append(f"{cnt}/{n} artifacts are '{s}' — one source class dominates the corpus")
    low_authority = sum(1 for a in collectible if artifact_authority(a) <= 0.35)
    if low_authority / n > 0.5:
        warnings.append(
            f"{low_authority}/{n} artifacts are low-authority (blog/docs/search/exa) — "
            "top-level marketing surfaces are under-represented"
        )
    # Warn specifically when the two most company-representative surfaces are
    # missing (R2): a corpus of product pages alone cannot certify company-level
    # positioning. Recognize the surface by URL path, not only page_category.
    surfaces = {_path_surface(_url_path(a.final_url or a.url)) for a in collectible}
    if "home" not in surfaces:
        warnings.append(
            "no homepage was captured as a first-party page — company-level positioning is "
            "inferred from secondary surfaces and should be treated as provisional"
        )
    elif "platform" not in surfaces:
        warnings.append(
            "no platform page was captured — the platform/consolidation narrative is inferred "
            "from product and secondary pages"
        )
    return warnings


def source_distribution(artifacts: list[RawArtifact]) -> dict[str, int]:
    """Counts by a readable source label for the source-distribution table
    (feedback #8)."""
    labels: dict[str, int] = {}
    for a in artifacts:
        cat = str(a.metadata.get("page_category", "")).lower()
        if a.source_type == "webpage" and cat:
            label = {"home": "homepage/platform", "platform": "homepage/platform"}.get(
                cat, f"{cat} pages"
            )
        elif a.source_type == "sitemap":
            label = "site map"
        elif a.source_type == "wayback":
            label = "wayback snapshots"
        elif a.source_type in ("news",):
            label = "news/launches"
        elif a.source_type == "comparison":
            label = "comparison pages"
        else:
            label = a.source_type
        labels[label] = labels.get(label, 0) + 1
    return labels


def proof_distribution(proof_type_lists: list[list[str]]) -> ProofDistribution:
    """Aggregate per-page proof-type lists into an honest distribution
    (feedback #16): counts per type + an overall assessment that a single
    strong page cannot inflate."""
    counts: Counter[str] = Counter()
    n_pages = len(proof_type_lists)
    for types in proof_type_lists:
        for t in set(types):
            counts[t] += 1
    strong_pages = sum(1 for types in proof_type_lists if set(types) & _STRONG_PROOF)
    moderate_pages = sum(
        1
        for types in proof_type_lists
        if not (set(types) & _STRONG_PROOF) and set(types) & _MODERATE_PROOF
    )
    # Overall assessment weights how MANY pages carry strong proof, not whether
    # any single one does.
    strong_share = strong_pages / n_pages if n_pages else 0.0
    if strong_share >= 0.6:
        overall = "strong"
    elif strong_share >= 0.3 or (strong_pages and moderate_pages):
        overall = "weak-to-moderate"
    elif strong_pages or moderate_pages:
        overall = "weak-to-moderate"
    elif counts:
        overall = "weak"
    else:
        overall = "none"
    return ProofDistribution(
        counts=dict(counts),
        n_pages=n_pages,
        quantified_outcomes=counts.get("quantified_customer_outcome", 0),
        independent_validations=counts.get("independent_validation", 0),
        product_demonstrations=counts.get("product_demonstration", 0),
        named_customer_stories=counts.get("named_customer_story", 0),
        feature_assertions=counts.get("feature_assertion", 0),
        logos_only=counts.get("customer_logo", 0),
        overall_assessment=overall,
    )


def commercial_motion(classifications: list[MarketingClassification]) -> dict[str, Any]:
    """Deterministic commercial-motion read from observed CTAs, pricing
    disclosure, and segment signals (feedback #20). No fabricated economics —
    this is a public-signal inference, never CAC/conversion/spend.
    """
    cta_counts: Counter[str] = Counter()
    pricing_levels: Counter[str] = Counter()
    segment_counts: Counter[str] = Counter()
    motion_signals: Counter[str] = Counter()
    for c in classifications:
        if c.cta:
            cta_counts[_normalize_cta(c.cta)] += 1
        if c.pricing_disclosure_level and c.pricing_disclosure_level != "unknown":
            pricing_levels[c.pricing_disclosure_level] += 1
        for s in c.segments or []:
            segment_counts[s] += 1
        for sig in c.commercial_motion_signals or []:
            motion_signals[sig.lower()] += 1

    total_cta = sum(cta_counts.values())
    dominant_ctas = {k: round(v / total_cta, 2) for k, v in cta_counts.most_common(5)} if total_cta else {}
    pricing = pricing_levels.most_common(1)[0][0] if pricing_levels else "unknown"

    # Infer primary motion from the CTA mix + pricing gating (public signals).
    demo = cta_counts.get("book_demo", 0) + cta_counts.get("contact_sales", 0)
    free = cta_counts.get("start_free", 0) + cta_counts.get("sign_up", 0)
    if total_cta == 0:
        motion = "unclear"
    elif demo > free and pricing in ("sales_gated", "hidden", "starting_price_only"):
        motion = "sales_led" if free == 0 else "hybrid_sales_led"
    elif free > demo:
        motion = "product_led" if pricing in ("fully_public", "calculator") else "hybrid_plg"
    else:
        motion = "hybrid"

    return {
        "primary_motion": motion,
        "pricing_disclosure": pricing,
        "dominant_ctas": dominant_ctas,
        "segment_focus": {k: v for k, v in segment_counts.most_common(4)},
        "signals": [s for s, _ in motion_signals.most_common(6)],
        "confidence": "medium" if total_cta >= 4 else "low",
        "basis": f"{total_cta} observed CTAs, {sum(pricing_levels.values())} pricing signals",
    }


_CTA_NORMAL = [
    ("book_demo", ("book a demo", "get a demo", "request a demo", "schedule a demo", "see a demo")),
    ("contact_sales", ("contact sales", "talk to sales", "talk to an expert", "get a quote", "request pricing")),
    ("start_free", ("start free", "get started free", "try free", "free trial", "start for free")),
    ("sign_up", ("sign up", "get started", "create account")),
    ("learn_more", ("learn more", "explore", "read more")),
]


def _normalize_cta(cta: str) -> str:
    low = cta.lower()
    for norm, variants in _CTA_NORMAL:
        if any(v in low for v in variants):
            return norm
    return "other"


def coverage_details(
    state: Any,
    artifacts: list[RawArtifact],
    classifications: list[MarketingClassification],
) -> list[CoverageDetail]:
    """Per-dimension coverage with the evidence behind each rating (feedback #7)."""
    from . import coverage as cov

    by_id = _artifacts_by_id(artifacts)
    # Map dimension -> source types that feed it.
    details: list[CoverageDetail] = []
    # Group artifacts by the source classes present.
    all_source_classes = sorted({a.source_type for a in artifacts if a.source_type != "sitemap"})
    windows_requested = len(state.time_windows)
    windows_present = len(
        {
            "current" if (a.archive_capture_at is None) else "historical"
            for a in artifacts
            if a.source_type != "sitemap"
        }
    )
    for dim in cov.COVERAGE_DIMENSIONS:
        level = state.coverage.get(dim, "not_attempted")
        if level == "not_attempted":
            continue
        feeders = cov.DIMENSION_SOURCES.get(dim, [])
        contributing = [a for a in artifacts if a.source_type in feeders] if feeders else []
        src_classes = sorted({a.source_type for a in contributing}) or all_source_classes
        missing = []
        for optional in ("paid_media", "public_social", "reviews"):
            if optional not in src_classes:
                missing.append(optional)
        reason = f"{len(contributing) or len(artifacts)} artifacts across {', '.join(src_classes) or 'mixed'} sources"
        details.append(
            CoverageDetail(
                dimension=dim,
                level=level,
                artifact_count=len(contributing) or 0,
                source_classes=src_classes,
                requested_periods=windows_requested,
                represented_periods=windows_present,
                missing_sources=missing
                if dim in ("paid_media", "competitive_stance", "product_positioning")
                else [],
                reason=reason,
            )
        )
    del by_id, classifications
    return details
