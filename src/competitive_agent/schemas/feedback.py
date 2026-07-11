from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import VersionedModel, utcnow

FeedbackTargetType = Literal["report", "claim", "change", "opportunity"]
FeedbackType = Literal["thumbs_up", "thumbs_down", "retry"]
RetryMode = Literal[
    "reanalyze_same_evidence",
    "collect_deeper_evidence",
    "retry_failed_sources",
    "use_alternative_provider",
    "challenge_conclusion",
    "narrow_scope",
    "expand_time_horizon",
    "compare_another_company",
]


class FeedbackEvent(VersionedModel):
    """User feedback on a run output (§37.9)."""

    feedback_id: str
    run_id: str
    target_type: FeedbackTargetType
    target_id: str | None = None
    feedback_type: FeedbackType
    reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class RetryRequest(VersionedModel):
    """A targeted rerun derived from feedback (§37.9)."""

    parent_run_id: str
    target_id: str | None = None
    retry_mode: RetryMode
    user_reason: str | None = None
