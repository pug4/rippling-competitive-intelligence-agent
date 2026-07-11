from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .artifact import RawArtifact
from .common import VersionedModel

ToolStatus = Literal[
    "success",
    "partial",
    "empty",
    "failed_retryable",
    "failed_terminal",
    "skipped_disabled",
    "unsupported",
]


class ResearchAction(VersionedModel):
    action_id: str
    action_type: str
    company_id: str
    time_window_ids: list[str] = Field(default_factory=list)
    source_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    expected_information_gain: float = 0.5
    strategic_importance: float = 0.5
    expected_reliability: float = 0.5
    estimated_cost_usd: float = 0.0
    estimated_latency_seconds: float = 5.0
    fallback_action_types: list[str] = Field(default_factory=list)
    parent_action_id: str | None = None


class ToolCapabilities(VersionedModel):
    live_available: bool
    fixture_available: bool
    supported_action_types: list[str]
    supports_date_filters: bool = False
    supports_historical_data: bool = False
    supports_exact_content: bool = False
    returns_estimates: bool = False
    known_limitations: list[str] = Field(default_factory=list)


class ToolResult(VersionedModel):
    action_id: str
    tool_name: str
    status: ToolStatus
    artifacts: list[RawArtifact] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    retryable: bool = False
    fallback_actions: list[ResearchAction] = Field(default_factory=list)
    capability_snapshot: dict[str, bool] = Field(default_factory=dict)
    latency_ms: int = 0
    cost_usd: float = 0.0
    coverage_delta: dict[str, float] = Field(default_factory=dict)
    # Negative observations ("searched X, found nothing") are findings, not
    # failures; they are recorded here for the limitations panel.
    negative_observations: list[str] = Field(default_factory=list)
