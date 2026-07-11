"""Build candidate strategic claims from grounded evidence (§37.20).

The model may only GROUP the supplied evidence; deterministic guards run
before and after it: evidence IDs outside the supplied set are stripped, a
claim left with no support is dropped, and any claim wording that reaches for
publicly-unknowable performance (ROAS, CAC, conversion, spend, pipeline) is
rejected outright. Claims leave here provisional — status is decided by the
independent judge.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from ..prompt_registry import PromptRegistry
from ..schemas.claim import StrategicClaim
from ..schemas.common import new_id
from ..schemas.evidence import EvidenceItem

logger = logging.getLogger(__name__)

BUILD_TASK_NAME = "build_claims"
BUILD_PROMPT_NAME = "claim_builder"

BUILDER_SYSTEM = (
    "You are the claim builder in a competitive marketing research system. "
    "Group only the supplied evidence into candidate claims. Do not invent "
    "evidence, cite yourself, or use outside knowledge. Respond only via the "
    "structured tool."
)

# Performance/economics are not publicly knowable — a claim reaching for them
# is rejected before it can ever reach the report (§2.1, §37.6 anti-pattern 8/9).
_BANNED_PERFORMANCE_TERMS = (
    "roas",
    "cac",
    "cpa",
    "conversion rate",
    "conversion-rate",
    "revenue",
    "pipeline",
    "ad spend",
    "media spend",
    "budget of",
    "return on ad spend",
)

_MAX_EVIDENCE_IN_PROMPT = 40


class CandidateClaim(BaseModel):
    statement: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    proposed_status: str = "hypothesis"
    alternative_explanations: list[str] = Field(default_factory=list)
    suggested_confidence: str = "low"
    reason: str = ""


class ClaimBuilderOutput(BaseModel):
    claims: list[CandidateClaim] = Field(default_factory=list)


def _evidence_block(evidence: list[EvidenceItem]) -> str:
    lines = []
    for e in evidence[:_MAX_EVIDENCE_IN_PROMPT]:
        window = ",".join(e.time_window_ids) or "n/a"
        lines.append(
            f"- id={e.evidence_id} | window={window} | type={e.evidence_type} | "
            f'excerpt="{e.exact_excerpt[:200]}" | statement="{e.normalized_statement[:200]}"'
        )
    return "\n".join(lines)


def _has_banned_term(statement: str) -> bool:
    lowered = statement.lower()
    return any(term in lowered for term in _BANNED_PERFORMANCE_TERMS)


def _norm_status(value: str) -> Literal["observed", "supported_inference", "hypothesis"]:
    value = (value or "").strip().lower()
    return cast(
        'Literal["observed", "supported_inference", "hypothesis"]',
        value if value in ("observed", "supported_inference", "hypothesis") else "hypothesis",
    )


def _norm_conf(value: str) -> Literal["high", "medium", "low"]:
    value = (value or "").strip().lower()
    return cast(
        'Literal["high", "medium", "low"]', value if value in ("high", "medium", "low") else "low"
    )


async def build_claims(
    evidence: list[EvidenceItem],
    gateway: Any,
    prompts: PromptRegistry,
    company_name: str,
    company_id: str,
    time_windows: Any,
) -> list[StrategicClaim]:
    if not evidence:
        return []
    valid_ids = {e.evidence_id for e in evidence}
    quality_by_id = {e.evidence_id: e.source_quality for e in evidence}

    prompt = prompts.get(BUILD_PROMPT_NAME)
    windows_desc = (
        ", ".join(getattr(w, "label", str(w)) for w in time_windows)
        if not isinstance(time_windows, str)
        else time_windows
    )
    rendered = prompt.render(
        company_name=company_name,
        time_windows=windows_desc or "current",
        evidence_block=_evidence_block(evidence),
    )
    result = await gateway.generate_structured(
        BUILD_TASK_NAME,
        system=BUILDER_SYSTEM,
        user_content=rendered,
        output_model=ClaimBuilderOutput,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    output: ClaimBuilderOutput = result.output

    claims: list[StrategicClaim] = []
    for candidate in output.claims:
        support = [eid for eid in candidate.supporting_evidence_ids if eid in valid_ids]
        contra = [eid for eid in candidate.contradicting_evidence_ids if eid in valid_ids]
        if not support:
            logger.info("claim dropped (no valid supporting evidence): %.80s", candidate.statement)
            continue
        if _has_banned_term(candidate.statement):
            logger.info(
                "claim dropped (performance term not publicly knowable): %.80s", candidate.statement
            )
            continue
        # Source quality = weakest supporting evidence band.
        bands = [quality_by_id.get(eid, "low") for eid in support]
        order = {"high": 3, "medium": 2, "low": 1}
        source_quality = min(bands, key=lambda b: order.get(b, 1))
        claims.append(
            StrategicClaim(
                claim_id=new_id("CLM"),
                company_id=company_id,
                statement=candidate.statement.strip(),
                status=_norm_status(candidate.proposed_status),
                evidence_ids=support,
                contradicting_evidence_ids=contra,
                claim_confidence=_norm_conf(candidate.suggested_confidence),
                research_coverage="low",  # set by judge/coverage later
                source_quality=source_quality,
                confidence_reason=candidate.reason or "builder proposal (pending judge)",
                alternative_explanations=candidate.alternative_explanations,
            )
        )
    return claims
