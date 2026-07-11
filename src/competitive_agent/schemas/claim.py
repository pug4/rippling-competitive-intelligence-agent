from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ConfidenceLevel, SourceQualityBand, VersionedModel

ClaimStatus = Literal[
    "observed",
    "supported_inference",
    "hypothesis",
    "contradicted",
    "rejected",
]


class StrategicClaim(VersionedModel):
    """An evidence-grounded strategic claim about a company (§37.9)."""

    claim_id: str
    company_id: str
    statement: str
    status: ClaimStatus
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    claim_confidence: ConfidenceLevel
    research_coverage: ConfidenceLevel
    source_quality: SourceQualityBand
    confidence_reason: str
    alternative_explanations: list[str] = Field(default_factory=list)
