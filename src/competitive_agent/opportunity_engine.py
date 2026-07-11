"""Turn verified message–proof gaps into defensible marketing actions (§23, §37.26).

Phase 1 generates grounded opportunities from the top gaps; each carries the
mandatory backfire / counterattack / proof-status fields so nothing reaches the
report as a naive "say all-in-one" idea. The adversarial critic pass (§37.26
rejection rules) is added in Phase 5.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from pydantic import BaseModel, Field

from .schemas.common import new_id
from .schemas.opportunity import DeliverableType, MarketingOpportunity, MessageProofGap

logger = logging.getLogger(__name__)

GEN_TASK_NAME = "generate_opportunities"
GEN_PROMPT_NAME = "opportunity"

GEN_SYSTEM = (
    "You are a product-marketing strategist. Produce one grounded, defensible, "
    "testable action from the supplied gap. Never invent competitor capabilities "
    "or focal-company proof. Respond only via the structured tool."
)

MAX_OPPORTUNITIES = 3


class OpportunityDraft(BaseModel):
    title: str
    opportunity_type: str = "positioning"
    message_angle: str
    villain_or_status_quo: str = ""
    promised_transformation: str = ""
    target_segment: str = "mid_market"
    target_personas: list[str] = Field(default_factory=list)
    target_jobs: list[str] = Field(default_factory=list)
    category_entry_point: str | None = None
    channels: list[str] = Field(default_factory=list)
    funnel_insertion_point: str = "consideration"
    deliverable_type: str = "product_marketing"
    focal_product_focus: list[str] = Field(default_factory=list)
    focal_proof_status: str = "missing"
    structural_defensibility: str = "medium"
    why_competitor_cannot_easily_copy: str = ""
    why_this_could_backfire: str
    comparative_claim_risk: str = "medium"
    legal_review_required: bool = True
    experiment_hypothesis: str = ""
    primary_metric: str = ""
    guardrail_metrics: list[str] = Field(default_factory=list)
    minimum_sample_rule: str = ""
    scale_rule: str = ""
    iterate_rule: str = ""
    kill_rule: str = ""


_VALID_DELIVERABLES = {
    "paid_campaign",
    "landing_page",
    "comparison_page",
    "sales_collateral",
    "customer_story",
    "event_strategy",
    "content_series",
    "brand_campaign",
    "product_marketing",
    "research_asset",
}


def _already_saying_it(gap: MessageProofGap) -> str:
    return "underweighted" if gap.rippling_equivalent_claim else "net_new"


async def generate_from_gaps(
    state: Any, ctx: Any, gaps: list[MessageProofGap]
) -> list[MarketingOpportunity]:
    if not gaps or ctx.gateway is None:
        return []
    prompts = ctx.scratch.get("_prompt_registry")
    if prompts is None:
        from .prompt_registry import PromptRegistry

        prompts = PromptRegistry()
    focal_name = ctx.config.focal_company.name if ctx.config else "Rippling"
    competitor = state.company.canonical_name if state.company else "the competitor"

    opportunities: list[MarketingOpportunity] = []
    seen_titles: set[str] = set()
    for gap in gaps[:MAX_OPPORTUNITIES]:
        prompt = prompts.get(GEN_PROMPT_NAME)
        rendered = prompt.render(
            focal_company=focal_name,
            competitor=competitor,
            claim_text=gap.claim_text,
            proof_strength=gap.proof_strength,
            missing_proof=", ".join(gap.missing_proof) or "none identified",
            focal_equivalent=gap.rippling_equivalent_claim or "not observed",
            already_saying_it=_already_saying_it(gap),
        )
        try:
            result = await ctx.gateway.generate_structured(
                GEN_TASK_NAME,
                system=GEN_SYSTEM,
                user_content=rendered,
                output_model=OpportunityDraft,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
            )
            draft: OpportunityDraft = result.output
        except Exception as exc:  # noqa: BLE001 - one gap failing must not sink the rest
            logger.warning("opportunity generation failed for gap %s: %s", gap.claim_id, exc)
            continue

        title_key = draft.title.strip().lower()
        if title_key in seen_titles:
            # Distinct gaps that yield the same action collapse to one (common
            # in fixture mode; live drafts differ per gap).
            continue
        seen_titles.add(title_key)
        deliverable = (
            draft.deliverable_type
            if draft.deliverable_type in _VALID_DELIVERABLES
            else "product_marketing"
        )
        proof_status = (
            draft.focal_proof_status
            if draft.focal_proof_status in ("available", "partial", "missing")
            else "missing"
        )
        opportunities.append(
            MarketingOpportunity(
                opportunity_id=new_id("OPP"),
                title=draft.title,
                opportunity_type=draft.opportunity_type,
                competitor_pattern=f"{competitor} repeats “{gap.claim_text}” with {gap.proof_strength} public proof.",
                change_over_time="see change events; message–proof gap is current-state unless a change event is linked",
                supporting_claim_ids=[gap.claim_id],
                category_entry_point=draft.category_entry_point,
                commercial_motion_context="derived from observed pricing/CTA signals",
                target_segment=draft.target_segment,
                target_personas=draft.target_personas,
                target_jobs=draft.target_jobs,
                rippling_product_focus=draft.focal_product_focus,
                rippling_segment=draft.target_segment,
                channels=draft.channels or ["website", "paid_linkedin"],
                funnel_insertion_point=draft.funnel_insertion_point,
                message_angle=draft.message_angle,
                villain_or_status_quo=draft.villain_or_status_quo or gap.claim_text,
                promised_transformation=draft.promised_transformation,
                competitor_proof_strength=gap.proof_strength,
                competitor_proof_gap=", ".join(gap.missing_proof) or "proof concentration unclear",
                rippling_proof_ids=gap.rippling_proof_ids,
                rippling_proof_status=proof_status,
                rippling_current_usage=_already_saying_it(gap),
                structural_defensibility=draft.structural_defensibility
                if draft.structural_defensibility in ("high", "medium", "low")
                else "medium",
                copyability="low" if draft.structural_defensibility == "high" else "medium",
                why_competitor_cannot_easily_copy=draft.why_competitor_cannot_easily_copy
                or "depends on the focal company's actual product breadth, not copy",
                why_this_could_backfire=draft.why_this_could_backfire,
                comparative_claim_risk=draft.comparative_claim_risk
                if draft.comparative_claim_risk in ("low", "medium", "high")
                else "medium",
                legal_review_required=bool(draft.legal_review_required),
                deliverable_type=cast(DeliverableType, deliverable),
                experiment_hypothesis=draft.experiment_hypothesis
                or f"Positioning against “{gap.claim_text}” lifts qualified engagement.",
                primary_metric=draft.primary_metric or "qualified demo requests",
                guardrail_metrics=draft.guardrail_metrics
                or ["brand sentiment", "legal review sign-off"],
                minimum_sample_rule=draft.minimum_sample_rule
                or "run until 2 weeks or 300 sessions per variant",
                scale_rule=draft.scale_rule
                or "scale if primary metric beats control by >15% with no guardrail regression",
                iterate_rule=draft.iterate_rule or "iterate creative if flat after minimum sample",
                kill_rule=draft.kill_rule
                or "kill if guardrail regresses or legal substantiation fails",
            )
        )
    return opportunities
