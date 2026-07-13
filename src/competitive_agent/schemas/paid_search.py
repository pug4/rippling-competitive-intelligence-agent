"""Paid-search targeting draft schema (on-demand, per-run).

Every cluster is a HYPOTHESIS grounded in observed evidence — search volume,
CPC, and commercial ad spend are not publicly knowable, so the schema carries
no field for them and ``validate_before_spend`` is forced true by the caller
regardless of model output.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import VersionedModel


class PaidSearchCluster(VersionedModel):
    cluster_label: str
    # Realistic buyer-language query phrases (never competitor brand slogans).
    seed_keywords: list[str] = Field(default_factory=list)
    search_intent: Literal[
        "informational",
        "commercial_investigation",
        "transactional",
        "navigational",
        "comparison",
    ] = "commercial_investigation"
    funnel_stage: Literal["awareness", "consideration", "decision"] = "consideration"
    # The mapped category entry point, when the cluster comes from one.
    category_entry_point: str | None = None
    evidence_basis: Literal[
        "observed_ad",
        "observed_page",
        "category_entry_point",
        "inferred",
    ] = "inferred"
    # Smallest exact verbatim excerpt from the supplied evidence; None only
    # when evidence_basis == "inferred". Containment-verified by the caller.
    supporting_quote: str | None = None
    focal_angle: str = ""
    focal_proof_status: Literal["available", "partial", "missing"] = "missing"
    competitor_defensibility: Literal["high", "medium", "low"] = "medium"
    cluster_type: Literal[
        "category_intent",
        "competitor_conquesting",
        "brand_defense",
        "whitespace",
    ] = "category_intent"
    priority_tier: Literal["high", "medium", "low"] = "medium"
    priority_reason: str = ""
    risk_note: str = ""
    # Forced true by the caller for competitor_conquesting / comparative angles.
    legal_review_required: bool = False
    # Forced true by the caller — economics must be validated in a keyword
    # planner / the live auction; they are never publicly observable.
    validate_before_spend: bool = True


class PaidSearchTargetingDraft(VersionedModel):
    clusters: list[PaidSearchCluster] = Field(default_factory=list)
    method_note: str = ""
