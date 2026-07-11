"""Portfolio-level run orchestration schemas (blueprint §38.16).

A ``PortfolioRun`` fans out per-company pipelines; each completed company
pipeline produces a ``CompanyIntelligencePackage`` whose hashes and
version pins make cross-company synthesis reproducible and auditable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import VersionedModel
from .company import TimeWindow


class PortfolioRun(VersionedModel):
    portfolio_run_id: str
    requested_companies: list[str]
    focal_company: str = "Rippling"
    mode: Literal["snapshot", "longitudinal", "comparative", "full"]
    time_windows: list[TimeWindow]

    company_run_ids: dict[str, str]
    status: Literal[
        "created",
        "running_company_pipelines",
        "validating_company_outputs",
        "synthesizing",
        "completed",
        "completed_with_limitations",
        "failed",
    ]

    max_concurrent_company_runs: int
    total_budget_usd: float
    per_company_budget_usd: float
    total_runtime_limit_seconds: int

    completed_company_ids: list[str] = Field(default_factory=list)
    failed_company_ids: list[str] = Field(default_factory=list)
    skipped_company_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CompanyIntelligencePackage(VersionedModel):
    company_run_id: str
    company_id: str
    scope_hash: str
    time_contract_hash: str
    taxonomy_version: str
    prompt_versions: dict[str, str]

    portfolio_snapshot_ids: list[str]
    product_positioning_record_ids: list[str]
    commercial_motion_profile_ids: list[str]
    claim_ids: list[str]
    change_event_ids: list[str]
    launch_ids: list[str]
    product_gap_candidate_ids: list[str]

    coverage: dict[str, str]
    quality_gate_results: dict[str, bool]
    limitations: list[str]
    report_path: str
    json_path: str
    trace_path: str
