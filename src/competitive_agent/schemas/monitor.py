"""Monitor and product-intelligence-feed schemas (blueprint §38.13–§38.14).

Monitor output is a *discovery candidate*, never final evidence (§38.14):
items ingested from monitors enter the feed as ``new_unvalidated`` and must
pass validation before they can affect gaps, opportunities, or reports.

Secrets note: the Exa webhook secret lives in environment-backed secret
storage; ``webhook_secret_ref`` stores only a reference — the secret itself
must never be logged or placed in reports.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel


class MonitorDefinition(VersionedModel):
    local_monitor_id: str
    exa_monitor_id: str | None = None
    company_id: str
    monitor_type: str
    name: str
    query: str
    period: str
    status: Literal["active", "paused", "disabled", "local_only"]
    output_schema_version: str
    webhook_url: str | None = None
    webhook_secret_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    last_successful_run_at: datetime | None = None
    last_ingested_run_id: str | None = None
    failure_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MonitorRunRecord(VersionedModel):
    local_run_id: str
    exa_monitor_run_id: str
    exa_monitor_id: str
    company_id: str
    monitor_type: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    fail_reason: str | None = None
    raw_payload_path: str | None = None
    result_count: int = 0
    ingested_artifact_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class ProductIntelligenceFeedItem(VersionedModel):
    """One discovered product event. Starts ``new_unvalidated``; monitor
    discoveries are candidates only and never count as final evidence
    until validated (§38.14)."""

    feed_item_id: str
    company_id: str
    product_ids: list[str]
    event_type: str
    headline: str
    summary: str
    event_date: date | None = None
    discovered_at: datetime
    validated_at: datetime | None = None
    status: Literal[
        "new_unvalidated",
        "validating",
        "validated",
        "rejected",
        "superseded",
    ]
    strategic_relevance: Literal["high", "medium", "low"]
    affected_gap_ids: list[str]
    affected_opportunity_ids: list[str]
    source_urls: list[str]
    confidence: ConfidenceLevel


class ProductIntelligenceFeed(VersionedModel):
    portfolio_run_id: str
    generated_at: datetime
    companies: list[str]
    items: list[ProductIntelligenceFeedItem]
    monitor_coverage: dict[str, str]
    last_successful_refresh_at: datetime | None = None
    limitations: list[str]
