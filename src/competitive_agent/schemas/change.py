from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import ConfidenceLevel, DatePrecision, Lifecycle, VersionedModel


class ArtifactTime(VersionedModel):
    """Temporal grounding for an artifact (§8.1)."""

    published_at: datetime | None = None
    first_observed_at: datetime
    last_observed_at: datetime
    effective_start_at: datetime | None = None
    effective_end_at: datetime | None = None
    archive_capture_at: datetime | None = None
    date_precision: DatePrecision = "unknown"
    date_source: str


class ChangeEvent(VersionedModel):
    """A verified change on a strategic dimension (§37.9 version)."""

    change_id: str
    company_id: str
    dimension: str
    prior_state: str
    current_state: str
    lifecycle: Lifecycle
    prior_evidence_ids: list[str] = Field(default_factory=list)
    current_evidence_ids: list[str] = Field(default_factory=list)
    first_detected_at: datetime | None = None
    apparent_change_at: datetime | None = None
    confidence: ConfidenceLevel
    coverage: ConfidenceLevel
    alternative_explanations: list[str] = Field(default_factory=list)
