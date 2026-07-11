"""Persisted Research Director state.

The state is the single source of truth for the loop: it is checkpointed to
``runs.state_json`` after every node so any run can resume at ``current_node``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .schemas.common import VersionedModel, new_id, utcnow
from .schemas.company import Company, TimeWindow

ResearchMode = Literal[
    "snapshot", "longitudinal", "comparative", "focused_deep_dive", "challenge", "retry"
]


class FailureRecord(VersionedModel):
    source_name: str
    action_type: str
    error_type: str | None = None
    attempts: int = 1
    last_failed_at: datetime = Field(default_factory=utcnow)


class OpenQuestion(VersionedModel):
    question_id: str = Field(default_factory=lambda: new_id("Q"))
    question: str
    dimension: str
    priority: Literal["high", "medium", "low"] = "medium"
    resolved: bool = False
    resolution_note: str | None = None


class DirectorState(VersionedModel):
    run_id: str
    thread_id: str | None = None
    parent_run_id: str | None = None

    # Inputs
    company_input: str
    compare_to: str | None = None
    mode: ResearchMode = "snapshot"
    execution_mode: Literal["live", "cached", "fixture"] = "fixture"
    lookback_days: int = 365
    user_focus: list[str] = Field(default_factory=list)
    retry_mode: str | None = None

    # Resolution
    company: Company | None = None
    focal_company: Company | None = None
    time_windows: list[TimeWindow] = Field(default_factory=list)

    # Loop state
    current_node: str = "initialize_run"
    iteration: int = 0
    coverage: dict[str, str] = Field(default_factory=dict)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    failed_actions: dict[str, FailureRecord] = Field(default_factory=dict)
    executed_action_keys: list[str] = Field(default_factory=list)
    pending_fallbacks: list[dict[str, Any]] = Field(default_factory=list)

    # Accumulated result references (full records live in the repository)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    classification_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    change_event_ids: list[str] = Field(default_factory=list)
    opportunity_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    negative_observations: list[str] = Field(default_factory=list)

    # Budgets
    budget_usd: float = 5.0
    spent_usd: float = 0.0
    max_runtime_seconds: int = 600
    started_at: datetime = Field(default_factory=utcnow)
    max_iterations: int = 40
    tool_calls_made: int = 0
    max_tool_calls: int = 120

    # Completion
    is_complete: bool = False
    stop_reason: str | None = None
    pending_user_question: str | None = None

    def budget_exhausted(self) -> bool:
        return self.spent_usd >= self.budget_usd

    def runtime_exhausted(self) -> bool:
        return (utcnow() - self.started_at).total_seconds() >= self.max_runtime_seconds

    def record_failure(self, source_name: str, action_type: str, error_type: str | None) -> None:
        key = f"{source_name}:{action_type}"
        existing = self.failed_actions.get(key)
        if existing:
            existing.attempts += 1
            existing.last_failed_at = utcnow()
            existing.error_type = error_type
        else:
            self.failed_actions[key] = FailureRecord(
                source_name=source_name, action_type=action_type, error_type=error_type
            )
