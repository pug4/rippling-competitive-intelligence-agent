"""Opportunity outputs: message-proof gaps and marketing opportunities.

``MarketingOpportunity`` merges the §23 schema with the §37.9 extra
required fields (which add ``rippling_segment``; all thirteen §37.9
fields are declared without defaults so pydantic enforces them). The
``rippling_*`` field names are kept verbatim from the blueprint contract;
the focal-generic rename applies only to ``focal.py`` (logged decision).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel

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


class MessageProofGap(VersionedModel):
    """A competitor claim vs. the proof observed behind it (§19.3)."""

    claim_id: str
    claim_text: str
    claim_type: str
    repetition: str
    lifecycle: str
    proof_types_observed: list[str] = Field(default_factory=list)
    strongest_proof_id: str | None = None
    proof_strength: ProofStrength
    missing_proof: list[str] = Field(default_factory=list)
    rippling_equivalent_claim: str | None = None
    rippling_proof_ids: list[str] = Field(default_factory=list)
    actionable_interpretation: str
    attackability: ConfidenceLevel
    why_attack_might_backfire: str


class MarketingOpportunity(VersionedModel):
    """An actionable, evidence-grounded marketing recommendation (§23 + §37.9)."""

    opportunity_id: str
    title: str
    opportunity_type: str

    competitor_pattern: str
    change_over_time: str
    supporting_claim_ids: list[str] = Field(default_factory=list)

    # §37.9 required: no default, but nullable when no CEP applies.
    category_entry_point: str | None
    commercial_motion_context: str
    target_segment: str
    target_personas: list[str] = Field(default_factory=list)
    target_jobs: list[str] = Field(default_factory=list)
    rippling_product_focus: list[str]
    rippling_segment: str

    channels: list[str] = Field(default_factory=list)
    funnel_insertion_point: str
    message_angle: str
    villain_or_status_quo: str
    promised_transformation: str

    competitor_proof_strength: str
    competitor_proof_gap: str
    rippling_proof_ids: list[str]
    rippling_proof_status: str

    rippling_current_usage: str
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
