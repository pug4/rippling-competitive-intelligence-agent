"""Deterministic coverage model (§37.15).

Coverage is tracked separately from claim confidence and rises only for the
reasons the blueprint allows: new relevant artifacts, better date coverage,
better source diversity, or an answered question. Duplicate artifacts never
inflate coverage.
"""

from __future__ import annotations

ORDINAL = {"not_attempted": 0, "unavailable": 0, "low": 1, "medium": 2, "high": 3}

COVERAGE_DIMENSIONS = [
    "identity",
    "current_website",
    "current_product",
    "pricing_and_packaging",
    "customer_proof",
    "paid_media",
    "public_linkedin",
    "news_and_launches",
    "historical_website",
    "historical_product",
    "historical_pricing",
    "historical_messages",
    "commercial_motion",
    "category_entry_points",
    "personas_and_jobs",
    "funnel",
    "proof_strategy",
    "competitive_stance",
    "events",
    "out_of_home",
    "portfolio_discovery",
    "product_positioning",
    "launches_current",
    "focal_current",
    "focal_proof",
    "focal_vulnerabilities",
]

# Source types that feed each dimension. Used by deterministic assessment: a
# dimension's level derives from how many DISTINCT relevant artifacts and
# source types have been observed for it.
DIMENSION_SOURCES: dict[str, list[str]] = {
    "identity": ["company_resolution"],
    "current_website": ["webpage", "sitemap"],
    "current_product": ["webpage"],
    "pricing_and_packaging": ["webpage"],
    "customer_proof": ["webpage", "exa_web", "reviews"],
    "paid_media": ["google_ads", "meta_ads", "linkedin_ads"],
    "public_linkedin": ["linkedin", "linkedin_post"],
    "news_and_launches": ["exa_web", "news"],
    "historical_website": ["wayback"],
    "historical_product": ["wayback"],
    "historical_pricing": ["wayback"],
    "historical_messages": ["wayback", "exa_web"],
    "events": ["events", "exa_web"],
    "out_of_home": ["ooh", "exa_web"],
    "launches_current": ["news"],
}

# Dimensions whose evidence is CLASSIFICATIONS of already-collected content,
# not a dedicated source type — their coverage derives from classification
# fields at render time (verifier: these were permanently stuck at
# not_attempted while the same brief displayed their analysis).
CLASSIFICATION_DERIVED_DIMENSIONS = (
    "funnel",
    "proof_strategy",
    "category_entry_points",
    "commercial_motion",
    "personas_and_jobs",
    "product_positioning",
    "portfolio_discovery",
    "competitive_stance",
    "identity",
    "focal_current",
    "focal_proof",
    "focal_vulnerabilities",
)


def initial_coverage() -> dict[str, str]:
    return dict.fromkeys(COVERAGE_DIMENSIONS, "not_attempted")


def level_at_least(coverage: dict[str, str], dimension: str, level: str) -> bool:
    return ORDINAL.get(coverage.get(dimension, "not_attempted"), 0) >= ORDINAL[level]


def raise_coverage(coverage: dict[str, str], dimension: str, new_level: str) -> bool:
    """Raise a dimension's coverage; never lowers. Returns True when changed."""
    current = ORDINAL.get(coverage.get(dimension, "not_attempted"), 0)
    proposed = ORDINAL.get(new_level, 0)
    if proposed > current:
        coverage[dimension] = new_level
        return True
    return False


def mark_unavailable(coverage: dict[str, str], dimension: str) -> None:
    """Publicly unobtainable after genuine attempts — a finding, not a failure."""
    if coverage.get(dimension) in ("not_attempted", None):
        coverage[dimension] = "unavailable"


def required_dimensions(mode: str, compare: bool) -> list[str]:
    base = [
        "identity",
        "current_website",
        "current_product",
        "pricing_and_packaging",
        "news_and_launches",
        "portfolio_discovery",
        "product_positioning",
        "commercial_motion",
    ]
    if mode in ("longitudinal", "comparative"):
        base += ["historical_website", "historical_messages"]
    if compare or mode == "comparative":
        base += ["focal_current", "focal_proof"]
    return base


def sufficient(
    coverage: dict[str, str], mode: str, compare: bool, minimum: str = "medium"
) -> tuple[bool, list[str]]:
    """(is_sufficient, missing_dimensions) for the mode's required set."""
    missing = [
        d
        for d in required_dimensions(mode, compare)
        if not level_at_least(coverage, d, minimum) and coverage.get(d) != "unavailable"
    ]
    return (not missing, missing)
