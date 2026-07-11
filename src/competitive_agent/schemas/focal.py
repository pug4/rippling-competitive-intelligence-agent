"""Focal-company proof inventory and vulnerability models.

Renamed from the blueprint's ``RipplingProof`` / ``RipplingVulnerability``
(§20.2–§20.3): the tool must support any focal company — the focal
company is configuration (``config/default.yaml``), not code — so these
models carry generic names. Field semantics are unchanged from the
blueprint. This rename is a logged project decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel

ProofStatus = Literal["active", "stale", "removed", "unverified"]
VulnerabilityStatus = Literal["emerging", "persistent", "declining", "unclear"]


class FocalProof(VersionedModel):
    """A verifiable proof asset the focal company can deploy (§20.2)."""

    proof_id: str
    claim_supported: str
    proof_type: str
    products: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    source_url: str
    source_date: datetime | None = None
    last_verified_at: datetime
    status: ProofStatus = "unverified"


class FocalVulnerability(VersionedModel):
    """A publicly observable weakness of the focal company (§20.3)."""

    vulnerability_id: str
    issue: str
    buyer_wording: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    prevalence_in_observed_corpus: str
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    status: VulnerabilityStatus = "unclear"
    confidence: ConfidenceLevel
