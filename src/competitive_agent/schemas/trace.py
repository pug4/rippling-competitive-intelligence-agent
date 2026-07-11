"""Run trace events.

``event_type`` is a plain ``str`` so new pipeline stages never break
deserialization of old traces; ``TRACE_EVENT_TYPES`` is the reference
vocabulary (§37.30 core list plus §38.39 portfolio/product/monitor
additions) for validation and UI grouping.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import VersionedModel, utcnow

TRACE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        # graph-driver node lifecycle (implementation-level, not §37.30)
        "node_started",
        "node_completed",
        "node_failed",
        # §37.30 core run events
        "run_started",
        "company_resolved",
        "time_windows_created",
        "coverage_assessed",
        "question_identified",
        "actions_proposed",
        "action_selected",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "fallback_selected",
        "artifacts_normalized",
        "evidence_extracted",
        "classification_completed",
        "claim_created",
        "claim_rejected",
        "contradiction_detected",
        "temporal_change_candidate",
        "temporal_change_verified",
        "temporal_change_rejected",
        "rippling_mirror_completed",
        "opportunity_generated",
        "opportunity_rejected",
        "stop_selected",
        "report_rendered",
        "feedback_received",
        "retry_created",
        "run_completed",
        # §38.39 portfolio events
        "portfolio_run_created",
        "company_pipeline_created",
        "company_pipeline_started",
        "company_pipeline_completed",
        "company_pipeline_partial",
        "company_pipeline_failed",
        "company_package_validated",
        "portfolio_synthesis_started",
        "portfolio_synthesis_completed",
        # §38.39 product-intelligence events
        "product_discovery_started",
        "product_discovered",
        "product_alias_merged",
        "product_relationship_created",
        "product_portfolio_snapshot_created",
        "product_positioning_classified",
        "product_motion_inferred",
        "product_gap_candidate_created",
        "product_gap_counterevidence_searched",
        "product_gap_validated",
        "product_gap_downgraded",
        "product_gap_rejected",
        "product_marketing_strategy_created",
        # §38.39 monitor events
        "monitor_created",
        "monitor_triggered",
        "monitor_run_polled",
        "monitor_webhook_received",
        "monitor_webhook_rejected",
        "monitor_result_ingested",
        "monitor_result_deduplicated",
        "monitor_result_validation_started",
        "monitor_result_rejected",
        "monitor_result_validated",
        "monitor_triggered_company_rerun",
    }
)


class TraceEvent(VersionedModel):
    event_id: str
    run_id: str
    event_type: str
    timestamp: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)
