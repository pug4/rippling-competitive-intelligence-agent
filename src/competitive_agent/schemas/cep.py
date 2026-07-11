from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import VersionedModel

CEPCoverage = Literal["strong", "medium", "weak", "not_observed"]
CEPStrategicStatus = Literal["owned", "contested", "whitespace", "competitor_advantage"]


class CategoryEntryPoint(VersionedModel):
    """A buying situation in which the category comes to mind (§11.2)."""

    cep_id: str
    company_id: str
    label: str
    buyer_trigger: str
    target_personas: list[str] = Field(default_factory=list)
    jobs: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    observed_channels: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    lifecycle: str = "unknown"
    observed_message_share: float | None = None
    rippling_coverage: CEPCoverage = "not_observed"
    strategic_status: CEPStrategicStatus
