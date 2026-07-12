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


def artifact_authority(artifact: RawArtifact) -> float:
    """0..1 weight for how company-representative a surface is (feedback #1)."""
    category = str(artifact.metadata.get("page_category", "")).lower()
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
        theme_surfaces.setdefault(theme, set()).add(
            str(art.metadata.get("page_category", art.source_type)) if art else "unknown"
        )
        theme_source_classes.setdefault(theme, set()).add(art.source_type if art else "unknown")
        # The human-readable LABEL comes from the highest-AUTHORITY page for the
        # theme (homepage > platform > product), so a high-salience niche page
        # can't hijack the label away from the company-level surface.
        label_score = authority * 10 + sal
        if label_score > theme_best.get(theme, -1):
            theme_best[theme] = label_score
            theme_label[theme] = c.primary_message or theme

    if not theme_weight:
        return {"theme": None, "is_company_level": False, "label": None, "reason": "no themes"}

    top = max(theme_weight, key=lambda t: theme_weight[t])
    top_level_surfaces = {"home", "platform", "product"}
    on_top_surface = bool(theme_surfaces[top] & top_level_surfaces)
    multi_source = len(theme_source_classes[top]) >= 2
    is_company_level = on_top_surface and multi_source
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
    low_authority = sum(1 for a in collectible if artifact_authority(a) < 0.35)
    if low_authority / n > 0.5:
        warnings.append(
            f"{low_authority}/{n} artifacts are low-authority (blog/docs/search) — "
            "top-level marketing surfaces are under-represented"
        )
    top_surface = sum(
        1
        for a in collectible
        if str(a.metadata.get("page_category", "")).lower() in ("home", "platform", "product")
    )
    if top_surface == 0:
        warnings.append(
            "no homepage/platform/product page was classified — positioning is inferred from secondary surfaces"
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
