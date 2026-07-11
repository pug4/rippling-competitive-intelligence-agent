"""Paid media, out-of-home, and event presence models (§12–§14).

Longevity is never treated as performance: ``performance_evidence`` on a
creative cluster may only be ``direct`` or ``self_reported`` when a public
result exists (§12.2).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from .common import ConfidenceLevel, PerformanceEvidence, VersionedModel

OOHFormat = Literal[
    "billboard",
    "digital_billboard",
    "transit",
    "airport",
    "street_furniture",
    "place_based",
    "projection",
    "experiential",
    "unknown",
]

# §14.1 presence taxonomy — kept as a reference constant; presence_type stays a
# plain str so new presence kinds do not break parsing.
EVENT_PRESENCE_TYPES: frozenset[str] = frozenset(
    {
        "sponsor",
        "exhibitor",
        "speaker",
        "host",
        "partner_event",
        "attendee_only_mention",
        "customer_speaker",
        "private_dinner_or_side_event",
        "webinar_or_virtual_event",
    }
)


class CreativeCluster(VersionedModel):
    """A persistent message/creative grouping observed across ads (§12.3)."""

    cluster_id: str
    company_id: str
    message: str
    channels: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    first_observed_at: datetime
    last_observed_at: datetime
    days_observed: int = 0
    active_status: str | None = None
    variant_count: int = 0
    landing_page_count: int = 0
    reactivation_count: int = 0
    observed_share_by_period: dict[str, float] = Field(default_factory=dict)
    personas: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    lifecycle: str = "unknown"
    performance_evidence: PerformanceEvidence = "unavailable"
    performance_note: str = ""


class OOHEvidence(VersionedModel):
    """Discovered out-of-home evidence — never a complete inventory (§13.3)."""

    evidence_id: str
    company_id: str
    format: OOHFormat = "unknown"
    geography: str | None = None
    venue: str | None = None
    message_text: str | None = None
    campaign_name: str | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    source_type: str
    image_url: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel


class EventPresence(VersionedModel):
    """A company's observed presence at one event (§14.3)."""

    # presence_id / company_id are convention additions (new_id traceability,
    # multi-company scoping); the blueprint schema omits them.
    presence_id: str
    company_id: str
    event_name: str
    event_date_start: date | None = None
    event_date_end: date | None = None
    geography: str | None = None
    presence_type: str = "unknown"
    sponsorship_tier: str | None = None
    speakers: list[str] = Field(default_factory=list)
    personas_targeted: list[str] = Field(default_factory=list)
    products_promoted: list[str] = Field(default_factory=list)
    message_themes: list[str] = Field(default_factory=list)
    booth_or_session_evidence: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
