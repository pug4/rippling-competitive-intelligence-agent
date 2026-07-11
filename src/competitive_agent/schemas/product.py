"""Product intelligence schemas (blueprint §38).

Product portfolio, positioning, commercial motion, gap, strategy, and
launch models. URL fields are plain ``str`` (logged decision: real public
URLs are messy). Taxonomy-style fields carry ``unknown`` / ``unclear`` /
``not_observed`` members rather than being omitted — silence in a public
corpus is never evidence of absence (§38.8).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .common import ConfidenceLevel, CoverageLevel, VersionedModel


class ArtifactType(StrEnum):
    """Product-artifact roles an artifact can carry (§38.3).

    An artifact retains its original channel type while carrying one or
    more of these product-artifact roles.
    """

    PRODUCT_OVERVIEW = "product_overview"
    PRODUCT_DETAIL = "product_detail"
    PLATFORM_OVERVIEW = "platform_overview"
    FEATURE_PAGE = "feature_page"
    SOLUTION_PAGE = "solution_page"
    SEGMENT_PAGE = "segment_page"
    USE_CASE_PAGE = "use_case_page"
    PRICING_PAGE = "pricing_page"
    PACKAGING_PAGE = "packaging_page"
    PRODUCT_DOC = "product_doc"
    API_DOC = "api_doc"
    CHANGELOG = "changelog"
    RELEASE_NOTE = "release_note"
    PRODUCT_LAUNCH = "product_launch"
    BETA_ANNOUNCEMENT = "beta_announcement"
    GENERAL_AVAILABILITY = "general_availability"
    INTEGRATION_ANNOUNCEMENT = "integration_announcement"
    ACQUISITION_ANNOUNCEMENT = "acquisition_announcement"
    PRODUCT_COMPARISON = "product_comparison"
    PRODUCT_CUSTOMER_STORY = "product_customer_story"
    PRODUCT_WEBINAR = "product_webinar"
    PRODUCT_DEMO = "product_demo"
    PRODUCT_AD = "product_ad"
    PRODUCT_SOCIAL_POST = "product_social_post"
    PRODUCT_JOB_POSTING = "product_job_posting"
    MARKETPLACE_LISTING = "marketplace_listing"


class ProductEntity(VersionedModel):
    product_id: str
    company_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    public_url: str | None = None

    product_type: Literal[
        "platform",
        "suite",
        "module",
        "application",
        "feature",
        "service",
        "add_on",
        "integration",
        "marketplace",
        "unknown",
    ]

    parent_product_id: str | None = None
    related_product_ids: list[str] = Field(default_factory=list)

    availability_status: Literal[
        "announced",
        "private_beta",
        "public_beta",
        "early_access",
        "generally_available",
        "region_limited",
        "segment_limited",
        "partner_delivered",
        "deprecated",
        "discontinued",
        "unknown",
    ]

    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    announcement_date: date | None = None
    general_availability_date: date | None = None

    source_evidence_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
    coverage: CoverageLevel


class ProductPortfolioSnapshot(VersionedModel):
    snapshot_id: str
    company_run_id: str
    company_id: str
    time_window_id: str
    observed_at: datetime

    products: list[ProductEntity]
    top_navigation_product_ids: list[str]
    wedge_product_ids: list[str]
    platform_product_ids: list[str]
    expansion_product_ids: list[str]

    architecture_type: Literal[
        "single_product",
        "suite",
        "unified_platform",
        "federated_platform",
        "bundle",
        "marketplace_led",
        "services_led",
        "unclear",
    ]

    architecture_reason: str
    evidence_ids: list[str]
    confidence: ConfidenceLevel
    coverage: CoverageLevel


class ProductRelationship(VersionedModel):
    relationship_id: str
    company_run_id: str
    source_product_id: str
    target_product_id: str
    relationship_type: Literal[
        "parent_child",
        "bundle_member",
        "cross_sell",
        "prerequisite",
        "integration",
        "replacement",
        "migration_path",
        "shared_data_claim",
        "shared_workflow_claim",
        "unknown",
    ]
    evidence_ids: list[str]
    confidence: ConfidenceLevel


class ProductPositioningRecord(VersionedModel):
    """How a company publicly positions a product (§38.5).

    Multi-label extraction with a dominant product message; exact wording
    is preserved in the ``exact_*`` and ``villain_exact_wording`` fields.
    """

    record_id: str
    company_run_id: str
    artifact_id: str
    product_ids: list[str]

    category_claim: str | None = None
    primary_message: str
    supporting_messages: list[str] = Field(default_factory=list)
    exact_headline: str | None = None
    exact_subheadline: str | None = None

    target_segments: list[str] = Field(default_factory=list)
    target_personas: list[str] = Field(default_factory=list)
    buyer_jobs: list[str] = Field(default_factory=list)
    buyer_pains: list[str] = Field(default_factory=list)
    category_entry_points: list[str] = Field(default_factory=list)
    funnel_stages: list[str] = Field(default_factory=list)

    claim_types: list[str] = Field(default_factory=list)
    proof_types: list[str] = Field(default_factory=list)
    villain_exact_wording: list[str] = Field(default_factory=list)
    villain_normalized: list[str] = Field(default_factory=list)
    named_competitors: list[str] = Field(default_factory=list)
    implied_competitor_classes: list[str] = Field(default_factory=list)
    competitive_stance: str

    promised_transformation_from: str | None = None
    promised_transformation_to: str | None = None

    primary_cta: str | None = None
    secondary_ctas: list[str] = Field(default_factory=list)
    pricing_disclosure: str | None = None
    packaging_signals: list[str] = Field(default_factory=list)
    implementation_signals: list[str] = Field(default_factory=list)

    evidence_ids: list[str]
    confidence: ConfidenceLevel


class ProductMotionProfile(VersionedModel):
    """Public commercial-motion inference (§38.7).

    This is an inference from public signals only — not an estimate of
    CAC, conversion, sales efficiency, or product adoption.
    """

    company_run_id: str
    time_window_id: str

    wedge_products: list[str]
    platform_anchors: list[str]
    expansion_products: list[str]
    attach_paths: list[dict[str, Any]]

    primary_entry_ctas: list[str]
    self_service_signals: list[str]
    sales_led_signals: list[str]
    partner_signals: list[str]

    apparent_motion: Literal[
        "product_led",
        "sales_led",
        "partner_led",
        "hybrid",
        "unclear",
    ]

    interpretation: str
    evidence_ids: list[str]
    alternative_explanations: list[str]
    confidence: ConfidenceLevel
    coverage: CoverageLevel


class ProductGapType(StrEnum):
    """Product-gap taxonomy (§38.8). A gap must be classified before it
    becomes a recommendation."""

    POSITIONING_GAP = "positioning_gap"
    PUBLIC_CAPABILITY_GAP = "public_capability_gap"
    WORKFLOW_GAP = "workflow_gap"
    INTEGRATION_GAP = "integration_gap"
    PACKAGING_GAP = "packaging_gap"
    PRICING_DISCLOSURE_GAP = "pricing_disclosure_gap"
    PROOF_GAP = "proof_gap"
    SEGMENT_GAP = "segment_gap"
    PERSONA_GAP = "persona_gap"
    CATEGORY_ENTRY_POINT_GAP = "category_entry_point_gap"
    MIGRATION_GAP = "migration_gap"
    IMPLEMENTATION_GAP = "implementation_gap"
    AVAILABILITY_GAP = "availability_gap"
    GEOGRAPHIC_GAP = "geographic_gap"
    PRODUCT_NARRATIVE_GAP = "product_narrative_gap"
    PORTFOLIO_COHERENCE_GAP = "portfolio_coherence_gap"
    LAUNCH_ACTIVATION_GAP = "launch_activation_gap"
    UNKNOWN_OR_UNPROVEN = "unknown_or_unproven"


class ProductGapRecord(VersionedModel):
    """A classified product gap between a competitor and Rippling (§38.9).

    Critical truthfulness rule (§38.8): use ``PUBLIC_CAPABILITY_GAP`` only
    when public first-party documentation, product comparison material,
    pricing or packaging, public product behavior, or multiple credible
    sources affirmatively establish the difference. When the only evidence
    is that a competitor does not *mention* a capability, classify as
    ``POSITIONING_GAP``, ``PROOF_GAP``, or ``UNKNOWN_OR_UNPROVEN`` instead.
    Never claim a product cannot do something based only on marketing
    silence.
    """

    gap_id: str
    portfolio_run_id: str | None = None
    competitor_company_run_id: str
    rippling_company_run_id: str

    gap_type: ProductGapType
    title: str
    description: str

    competitor_product_ids: list[str]
    rippling_product_ids: list[str]

    target_segments: list[str]
    target_personas: list[str]
    buyer_jobs: list[str]
    category_entry_points: list[str]

    competitor_position: str
    competitor_public_capability: str | None = None
    competitor_proof_strength: Literal["high", "medium", "low", "unknown"]

    rippling_position: str
    rippling_public_capability: str | None = None
    rippling_proof_ids: list[str]
    rippling_proof_status: Literal["available", "partial", "missing"]

    observed_over_time: bool
    prior_state: str | None = None
    current_state: str
    change_event_ids: list[str] = Field(default_factory=list)

    alternative_explanations: list[str]
    counterattack_risk: Literal["high", "medium", "low"]
    why_this_could_backfire: str
    legal_review_required: bool

    competitor_evidence_ids: list[str]
    rippling_evidence_ids: list[str]

    claim_confidence: ConfidenceLevel
    research_coverage: CoverageLevel


RecommendedAsset = Literal[
    "landing_page",
    "comparison_page",
    "product_demo",
    "interactive_workflow",
    "tco_calculator",
    "migration_guide",
    "battlecard",
    "customer_story",
    "webinar",
    "analyst_brief",
    "sales_deck",
    "paid_ad",
    "organic_social",
    "email_nurture",
    "event_session",
    "out_of_home",
    "other",
]


class ProductMarketingStrategy(VersionedModel):
    """A product-marketing strategy derived from a classified gap (§38.11).

    Strategies are prioritized on evidence, business relevance,
    defensibility, proof readiness, and current Rippling message
    saturation.
    """

    strategy_id: str
    gap_id: str

    strategic_objective: str
    category_entry_point: str
    target_segment: str
    target_personas: list[str]
    buyer_problem: str

    rippling_product_focus: list[str]
    competitor_product_focus: list[str]

    positioning_statement: str
    message_hierarchy: list[str]
    villain_or_status_quo: str
    promised_transformation: str

    proof_plan: list[str]
    required_proof_ids: list[str]
    proof_missing: list[str]

    recommended_assets: list[RecommendedAsset]

    launch_or_campaign_sequence: list[str]
    primary_channel: str
    supporting_channels: list[str]
    funnel_insertion_point: str

    why_it_can_win: str
    why_competitor_cannot_easily_copy: str
    why_it_could_backfire: str
    competitor_likely_response: str

    primary_metric: str
    guardrail_metrics: list[str]
    scale_rule: str
    iterate_rule: str
    kill_rule: str

    evidence_ids: list[str]
    confidence: ConfidenceLevel


class ProductLaunchEvent(VersionedModel):
    """A product launch as a structured change event (§38.12), able to
    alter portfolio, segment, persona, category claim, motion, pricing,
    geography, proof, posture, and channel allocation."""

    launch_id: str
    company_run_id: str
    company_id: str

    product_ids: list[str]
    launch_name: str
    launch_type: Literal[
        "new_product",
        "new_module",
        "major_feature",
        "integration",
        "platform_expansion",
        "bundle",
        "pricing_or_packaging",
        "geographic_expansion",
        "beta",
        "general_availability",
        "acquisition_integration",
        "rebrand",
        "other",
    ]

    announcement_date: date | None = None
    availability_date: date | None = None
    first_observed_at: datetime
    last_observed_at: datetime

    availability_status: str
    regions: list[str]
    segments: list[str]
    personas: list[str]
    buyer_jobs: list[str]
    category_entry_points: list[str]

    launch_message: str
    villain_or_status_quo: str | None = None
    promised_transformation: str | None = None
    named_competitors: list[str]

    pricing_or_packaging_change: str | None = None
    documentation_url: str | None = None
    pricing_url: str | None = None

    initial_channels: list[str]
    follow_on_channels: list[str]
    employee_amplification_count_observed: int | None = None
    executive_amplification_count_observed: int | None = None

    proof_types: list[str]
    customer_proof_ids: list[str]

    lifecycle: Literal[
        "announced",
        "launching",
        "expanding",
        "stable",
        "declining",
        "not_recently_observed",
        "possibly_abandoned",
        "reintroduced",
        "superseded",
    ]

    source_evidence_ids: list[str]
    confidence: ConfidenceLevel
    coverage: CoverageLevel
