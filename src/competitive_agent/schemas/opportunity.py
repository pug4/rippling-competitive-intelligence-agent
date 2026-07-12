"""Opportunity outputs: message-proof gaps and marketing opportunities.

``MarketingOpportunity`` merges the §23 schema with the §37.9 extra
required fields (which add ``focal_segment``; all thirteen §37.9
fields are declared without defaults so pydantic enforces them). The
``focal_*`` field names are kept verbatim from the blueprint contract;
the focal-generic rename applies only to ``focal.py`` (logged decision).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .common import ConfidenceLevel, CoverageLevel, VersionedModel

DeliverableType = Literal[
    "paid_campaign",
    "landing_page",
    "comparison_page",
    "sales_collateral",
    "customer_story",
    "event_strategy",
    "content_series",
    "brand_campaign",
    "product_marketing",
    "research_asset",
]

ProofStrength = Literal["strong", "moderate", "weak", "none"]
Comparability = Literal["strong", "partial", "weak"]
OverallStance = Literal["attack", "reframe", "concede", "investigate"]


class ProofDistribution(VersionedModel):
    """Honest proof profile: a count per proof type, not one collapsed label
    (feedback #16). One strong page must not mask weak overall coverage."""

    counts: dict[str, int] = Field(default_factory=dict)
    n_pages: int = 0
    quantified_outcomes: int = 0
    independent_validations: int = 0
    product_demonstrations: int = 0
    named_customer_stories: int = 0
    feature_assertions: int = 0
    logos_only: int = 0
    overall_assessment: str = "none"  # e.g. "weak-to-moderate"

    def summary_line(self) -> str:
        parts = [f"{n}x {t}" for t, n in sorted(self.counts.items(), key=lambda kv: -kv[1])]
        return "; ".join(parts) or "no proof observed"


class ProductComparability(VersionedModel):
    """Whether a competitor product and a focal product are apples-to-apples
    before a comparative recommendation is made (feedback #3)."""

    competitor_product: str
    focal_product: str
    shared_buyer_job: str | None = None
    shared_workflow: str | None = None
    overlapping_capabilities: list[str] = Field(default_factory=list)
    materially_different_capabilities: list[str] = Field(default_factory=list)
    comparability: Comparability = "weak"
    rationale: str = ""


class AttackabilityAssessment(VersionedModel):
    """Ordinal attackability rubric (feedback #17) — not a single opaque label."""

    proof_gap: ConfidenceLevel
    focal_proof: ConfidenceLevel
    product_comparability: Comparability
    structural_defensibility: ConfidenceLevel
    counterattack_risk: ConfidenceLevel
    overall: OverallStance
    rationale: str = ""


class MessageProofGap(VersionedModel):
    """A competitor claim vs. the proof observed behind it (§19.3)."""

    claim_id: str
    claim_text: str
    short_label: str = ""  # concise theme label for the gap table (feedback #29)
    claim_type: str
    claim_specificity: str = "unknown"  # SEPARATE from proof_strength (feedback #15)
    repetition: str
    lifecycle: str
    proof_types_observed: list[str] = Field(default_factory=list)
    proof_distribution: ProofDistribution | None = None  # feedback #16
    strongest_proof_id: str | None = None
    proof_strength: ProofStrength
    missing_proof: list[str] = Field(default_factory=list)
    focal_equivalent_claim: str | None = None
    focal_proof_ids: list[str] = Field(default_factory=list)
    focal_proof_strength: ProofStrength = "none"
    product_comparability: ProductComparability | None = None  # feedback #3
    actionable_interpretation: str
    # Per-product-vertical proof strengths (vertical -> {n_pages, strength}) and
    # the divergent weakest vertical — a corpus-wide verdict must not hide
    # 'weak in EOR, strong in payroll' (audit HIGH fix).
    vertical_strengths: dict[str, Any] = Field(default_factory=dict)
    weakest_vertical: str | None = None
    attackability: ConfidenceLevel
    attackability_detail: AttackabilityAssessment | None = None  # feedback #17
    why_attack_might_backfire: str


class CoverageDetail(VersionedModel):
    """Per-dimension coverage with the evidence behind the rating (feedback #7).
    A single source class should rarely earn 'high' for a broad dimension."""

    dimension: str
    level: CoverageLevel
    artifact_count: int = 0
    source_classes: list[str] = Field(default_factory=list)
    requested_periods: int = 0
    represented_periods: int = 0
    failed_sources: list[str] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    reason: str = ""


ActionCategory = Literal[
    "product_marketing_proof",
    "demand_gen_campaign",
    "competitive_enablement",
    "content_research",
    "brand_event",
]


class CampaignPlan(VersionedModel):
    """Concrete campaign attached to an opportunity (feedback #12): the proof
    prerequisite is a stage, not the whole recommendation."""

    audience: list[str] = Field(default_factory=list)
    category_entry_point: str | None = None
    message: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    creative_or_asset: str | None = None
    landing_experience: str | None = None
    primary_metric: str | None = None


class StagedPlan(VersionedModel):
    """Stage-specific decision rules (feedback #13): proof → validate → launch,
    each with its own proceed/stop criteria rather than one arbitrary date."""

    stage: Literal["build_proof", "validate_proof", "launch_campaign"]
    objective: str
    proceed_if: list[str] = Field(default_factory=list)
    stop_or_reframe_if: list[str] = Field(default_factory=list)


class MarketingOpportunity(VersionedModel):
    """An actionable, evidence-grounded marketing recommendation (§23 + §37.9)."""

    opportunity_id: str
    title: str
    opportunity_type: str
    # Coarse category used to enforce recommendation DIVERSITY (feedback #11/#38).
    action_category: ActionCategory = "product_marketing_proof"

    competitor_pattern: str
    change_over_time: str
    supporting_claim_ids: list[str] = Field(default_factory=list)
    # Concrete campaign + staged decision rules (feedback #12/#13).
    campaign_plan: CampaignPlan | None = None
    staged_plan: list[StagedPlan] = Field(default_factory=list)
    product_comparability: ProductComparability | None = None  # feedback #3

    # §37.9 required: no default, but nullable when no CEP applies.
    category_entry_point: str | None
    commercial_motion_context: str
    target_segment: str
    target_personas: list[str] = Field(default_factory=list)
    target_jobs: list[str] = Field(default_factory=list)
    focal_product_focus: list[str]
    focal_segment: str

    channels: list[str] = Field(default_factory=list)
    funnel_insertion_point: str
    message_angle: str
    villain_or_status_quo: str
    promised_transformation: str

    competitor_proof_strength: str
    competitor_proof_gap: str
    focal_proof_ids: list[str]
    focal_proof_status: str

    focal_current_usage: str
    structural_defensibility: str
    copyability: str
    why_competitor_cannot_easily_copy: str

    why_this_could_backfire: str
    comparative_claim_risk: str
    legal_review_required: bool

    deliverable_type: DeliverableType

    experiment_hypothesis: str
    primary_metric: str
    guardrail_metrics: list[str] = Field(default_factory=list)
    minimum_sample_rule: str
    scale_rule: str
    iterate_rule: str
    kill_rule: str
