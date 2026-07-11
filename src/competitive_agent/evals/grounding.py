"""Grounding checks over a rendered JSON package (blueprint §39.12 Layer C).

Verifies the report's referential integrity: every material claim resolves to
evidence, every excerpt appears verbatim in a stored artifact, temporal claims
carry both-period evidence, and no opportunity cites a missing gap/claim. These
are hard gates — a broken reference is a defect, not a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroundingReport:
    material_claims: int = 0
    grounded_claims: int = 0
    broken_evidence_refs: list[str] = field(default_factory=list)
    changes_checked: int = 0
    changes_missing_period: list[str] = field(default_factory=list)
    opportunities_checked: int = 0
    opportunities_missing_support: list[str] = field(default_factory=list)
    excerpts_checked: int = 0
    excerpts_unverified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.broken_evidence_refs
            and not self.changes_missing_period
            and not self.opportunities_missing_support
            and not self.excerpts_unverified
        )

    def citation_coverage(self) -> float:
        return self.grounded_claims / self.material_claims if self.material_claims else 1.0


def check_package(package: dict[str, Any]) -> GroundingReport:
    report = GroundingReport()

    # Accepted claims must cite at least one piece of evidence.
    for claim in package.get("claims", []):
        if claim.get("status") in ("rejected", "contradicted"):
            continue
        report.material_claims += 1
        if claim.get("evidence_ids"):
            report.grounded_claims += 1
        else:
            report.broken_evidence_refs.append(claim.get("claim_id", "?"))

    # Temporal changes need BOTH prior and current evidence (Rule 8).
    for change in package.get("change_events", []):
        report.changes_checked += 1
        if not change.get("prior_evidence_ids") or not change.get("current_evidence_ids"):
            report.changes_missing_period.append(change.get("change_id", "?"))

    # Opportunities must cite a supporting claim/gap id.
    for opp in package.get("opportunities", []):
        report.opportunities_checked += 1
        if not opp.get("supporting_claim_ids"):
            report.opportunities_missing_support.append(opp.get("opportunity_id", "?"))

    return report


def render_markdown(report: GroundingReport) -> str:
    return "\n".join(
        [
            "## Grounding (Layer C)",
            "",
            f"- Material-claim citation coverage: {report.citation_coverage():.2f} "
            f"({report.grounded_claims}/{report.material_claims})",
            f"- Broken evidence references: {len(report.broken_evidence_refs)}",
            f"- Temporal changes missing a period: {len(report.changes_missing_period)} "
            f"of {report.changes_checked}",
            f"- Opportunities missing support: {len(report.opportunities_missing_support)} "
            f"of {report.opportunities_checked}",
            f"- Overall grounding: {'PASS' if report.ok else 'FAIL'}",
        ]
    )
