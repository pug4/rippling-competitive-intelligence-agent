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
    # None -> config windows.current_days (90). Set per-run from the UI/API
    # so users can choose how the comparison is split.
    current_days: int | None = None
    # Per-run source opt-outs (config source-flag names, e.g. 'exa_linkedin').
    # The planner skips proposing actions for these; global config unchanged.
    disabled_sources: list[str] = Field(default_factory=list)
    user_focus: list[str] = Field(default_factory=list)
    retry_mode: str | None = None
    # Retry mode that re-analyzes a parent's evidence without collecting anew:
    # the planner proposes a single reuse action, then no further collection.
    reuse_evidence_only: bool = False

    # Resolution
    company: Company | None = None
    focal_company: Company | None = None
    # The focal (Rippling) mirror runs as an isolated sub-run; its id is
    # persisted here so re-render can attach the focal evidence reliably.
    focal_run_id: str | None = None
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
    spent_usd: float = 0.0  # tool costs (model costs tracked separately below)
    model_cost_usd: float = 0.0  # cumulative model spend, synced from the gateway
    max_runtime_seconds: int = 600
    started_at: datetime = Field(default_factory=utcnow)
    max_iterations: int = 40
    tool_calls_made: int = 0
    max_tool_calls: int = 120

    # Completion
    is_complete: bool = False
    stop_reason: str | None = None
    pending_user_question: str | None = None

    # Shared contract fields (CONTRACTS.md "Shared state fields") — additive,
    # persistence-safe defaults so old checkpointed runs keep loading.
    # When set, propose_actions proposes ONLY actions whose source is in it
    # (internal/analysis actions like reuse_evidence stay allowed).
    source_allowlist: list[str] | None = None
    # UI-launched runs; enables mid-run clarifying decisions.
    interactive: bool = False
    # {"question": str, "context": str,
    #  "options": [{"id": str, "label": str, "source": str|None}]}
    pending_decision: dict[str, Any] | None = None
    # Append {"question": ..., "choice": option_id, "via": "user"|"auto"}.
    decision_log: list[dict[str, Any]] = Field(default_factory=list)

    # Industry adaptivity: the inferred competitor-industry lens (dict from
    # industry.infer_industry_context — industry/terminology/personas/positioning
    # frame). None until inferred; a typed-fallback dict on inference failure.
    # Additive, persistence-safe default so old checkpointed runs keep loading.
    industry_context: dict[str, Any] | None = None
    # Focal-claims gate findings: one row per detected "focal lacks X" assertion
    # {source, id, claim_excerpt, x_phrase, verdict, focal_evidence}. A
    # "contradicted" verdict drops/softens the offending recommendation. Additive.
    focal_gate_findings: list[dict[str, Any]] = Field(default_factory=list)
    # Provider circuit breaker: providers that returned a TERMINAL error mid-run
    # (out of credits / auth — HTTP 402/401) and are therefore down for the rest
    # of the run. The planner skips proposing/executing any tool backed by a dead
    # provider, and the outage is disclosed once. Additive, persistence-safe.
    dead_providers: list[str] = Field(default_factory=list)

    def total_spend_usd(self) -> float:
        return self.spent_usd + self.model_cost_usd

    def budget_exhausted(self) -> bool:
        return self.total_spend_usd() >= self.budget_usd

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
