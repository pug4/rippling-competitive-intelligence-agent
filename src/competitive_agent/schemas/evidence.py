from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import ConfidenceLevel, SourceQualityBand, VersionedModel


class EvidenceItem(VersionedModel):
    """A single grounded observation tied to an exact excerpt (§37.9)."""

    evidence_id: str
    artifact_id: str
    company_id: str
    exact_excerpt: str
    normalized_statement: str
    evidence_type: str
    classification_id: str | None = None
    source_quality: SourceQualityBand
    extraction_confidence: ConfidenceLevel
    time_window_ids: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
