"""Shared schema conventions.

Every persisted analytical object follows the same contract:
- inherits from ``VersionedModel`` (strict, forbids unknown fields);
- declares a class-level ``SCHEMA_VERSION`` so the storage layer can
  validate payloads on read via the schema registry;
- uses ``new_id(prefix)`` identifiers so records stay human-traceable.

Absence semantics: taxonomy-style fields use ``not_observed`` /
``unknown`` members rather than omitting the field — silence in a public
corpus is never evidence of absence (blueprint §2, §38.8).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

ConfidenceLevel = Literal["high", "medium", "low"]
CoverageLevel = Literal["high", "medium", "low", "not_attempted", "unavailable"]
SourceQualityBand = Literal["high", "medium", "low"]
SourceQualityKind = Literal[
    "first_party", "platform_record", "estimated_provider", "third_party", "anecdotal"
]
PerformanceEvidence = Literal["direct", "self_reported", "indirect_proxy", "unavailable"]
FeasibilityBadge = Literal[
    "RELIABLE_PUBLIC",
    "ESTIMATED",
    "DISCOVERABLE_PARTIAL",
    "SELF_REPORTED",
    "NOT_PUBLICLY_KNOWABLE",
]
InterpretationStatus = Literal["observed", "supported_inference", "hypothesis"]
Lifecycle = Literal[
    "emerging",
    "expanding",
    "stable",
    "declining",
    "not_recently_observed",
    "possibly_abandoned",
    "reintroduced",
    "repositioned",
]
DatePrecision = Literal["exact", "day", "month", "quarter", "unknown"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class VersionedModel(BaseModel):
    """Base for every persisted analytical object."""

    SCHEMA_VERSION: ClassVar[str] = "1.0.0"

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @classmethod
    def schema_name(cls) -> str:
        return cls.__name__
