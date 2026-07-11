from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel


class Company(VersionedModel):
    company_id: str
    canonical_name: str
    primary_domain: str
    aliases: list[str] = Field(default_factory=list)
    linkedin_company_url: str | None = None
    resolved_at: datetime
    resolution_confidence: ConfidenceLevel
    resolution_evidence_ids: list[str] = Field(default_factory=list)


class TimeWindow(VersionedModel):
    window_id: str
    label: str
    start_at: datetime
    end_at: datetime
    purpose: str  # current | comparison | historical | custom
