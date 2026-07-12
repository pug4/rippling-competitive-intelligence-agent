"""Independently judge each candidate claim against its cited evidence (§37.20).

The judge sees ONLY the exact excerpts the claim cites (plus any contradicting
ones) — never the whole corpus, never the builder's reasoning. Its verdict is
mapped to the blueprint acceptance policy:

    fully_supported     -> keep (observed / supported_inference)
    partially_supported -> narrow the wording, downgrade to hypothesis
    unsupported         -> reject
    contradicted        -> preserve for the contradiction view, never as true
    context_dependent   -> downgrade to hypothesis
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..prompt_registry import PromptRegistry
from ..schemas.claim import StrategicClaim
from ..schemas.evidence import EvidenceItem

JUDGE_TASK_NAME = "judge_claim"
JUDGE_PROMPT_NAME = "claim_judge"

JUDGE_SYSTEM = (
    "You are an independent claim judge. Decide only from the supplied exact "
    "evidence excerpts; use no outside knowledge. A claim is fully supported "
    "only when its wording does not exceed the evidence. Respond only via the "
    "structured tool."
)


class ClaimJudgeVerdict(BaseModel):
    verdict: str  # fully_supported|partially_supported|unsupported|contradicted|context_dependent
    narrowed_claim: str | None = None
    driving_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reason: str = ""


def _evidence_block(claim: StrategicClaim, evidence_by_id: dict[str, EvidenceItem]) -> str:
    ids = list(dict.fromkeys([*claim.evidence_ids, *claim.contradicting_evidence_ids]))
    lines = []
    for eid in ids:
        e = evidence_by_id.get(eid)
        if e is None:
            continue
        date = e.published_at.date().isoformat() if e.published_at else "undated"
        lines.append(
            f'- id={eid} | date={date} | source={e.source_quality} | excerpt="{e.exact_excerpt[:220]}"'
        )
    return "\n".join(lines) or "(no resolvable evidence)"


async def judge_claim(
    claim: StrategicClaim,
    evidence_by_id: dict[str, EvidenceItem],
    gateway: Any,
    prompts: PromptRegistry,
) -> StrategicClaim:
    prompt = prompts.get(JUDGE_PROMPT_NAME)
    rendered = prompt.render(
        claim_statement=claim.statement,
        evidence_block=_evidence_block(claim, evidence_by_id),
    )
    result = await gateway.generate_structured(
        JUDGE_TASK_NAME,
        system=JUDGE_SYSTEM,
        user_content=rendered,
        output_model=ClaimJudgeVerdict,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    verdict: ClaimJudgeVerdict = result.output
    v = (verdict.verdict or "").strip().lower()

    if v == "fully_supported":
        # Multi-period support may read as an inference; single-artifact stays observed.
        status = "supported_inference" if len(claim.evidence_ids) > 1 else "observed"
        return claim.model_copy(
            update={
                "status": status,
                "confidence_reason": verdict.reason or claim.confidence_reason,
            }
        )
    if v == "partially_supported":
        # The narrowed wording is LLM-generated — re-run the builder's banned
        # performance-term guard on it (red-team: the build-time ROAS/CAC/spend
        # guard could be bypassed via narrowed_claim). On a hit, keep the
        # original build-time-vetted statement.
        from .claim_builder import _has_banned_term

        narrowed = verdict.narrowed_claim or claim.statement
        reason = verdict.reason or "narrowed to what the evidence supports"
        if verdict.narrowed_claim and _has_banned_term(verdict.narrowed_claim):
            narrowed = claim.statement
            reason += (
                " (narrowed wording rejected: performance term; original vetted statement retained)"
            )
        return claim.model_copy(
            update={
                "statement": narrowed,
                "status": "hypothesis",
                "claim_confidence": "medium"
                if claim.claim_confidence == "high"
                else claim.claim_confidence,
                "confidence_reason": reason,
            }
        )
    if v == "contradicted":
        return claim.model_copy(
            update={
                "status": "contradicted",
                "confidence_reason": verdict.reason or "contradicted by cited evidence",
            }
        )
    if v == "context_dependent":
        return claim.model_copy(
            update={
                "status": "hypothesis",
                "confidence_reason": verdict.reason or "context dependent",
            }
        )
    # unsupported (or anything unrecognized) -> reject
    return claim.model_copy(
        update={
            "status": "rejected",
            "confidence_reason": verdict.reason or "unsupported by cited evidence",
        }
    )
