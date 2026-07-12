"""Turn verified message–proof gaps into defensible marketing actions (§23, §37.26).

Every opportunity carries mandatory backfire / counterattack / proof-status
fields, a staged decision plan (proof → validate → launch) with stage-specific
rules, and a product-comparability judgement. Two critics run before an
opportunity is accepted: a genericness critic (reject "could be any SaaS")
and an unsupported-superiority critic (reject "Rippling is truly unified" /
"competitor merely stitches modules" without evidence). The final set is forced
to be diverse in action category (feedback #11/#38).
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from pydantic import BaseModel, Field

from .schemas.common import new_id
from .schemas.opportunity import (
    CampaignPlan,
    DeliverableType,
    MarketingOpportunity,
    MessageProofGap,
    ProductComparability,
    StagedPlan,
)

logger = logging.getLogger(__name__)

GEN_TASK_NAME = "generate_opportunities"
GEN_PROMPT_NAME = "opportunity"

GEN_SYSTEM = (
    "You are a product-marketing strategist. Produce one grounded, defensible, "
    "testable action from the supplied gap in the REQUIRED action category. Never "
    "invent competitor capabilities or focal-company proof, and never assert "
    "focal-company superiority the evidence does not support. Respond only via the "
    "structured tool."
)

MAX_OPPORTUNITIES = 3

# Preferred category order for the top-3 (feedback #11/#38): at most two may
# share a category. Assigned by attackability so the sharpest gap leads.
_CATEGORY_BY_RANK = [
    "product_marketing_proof",
    "demand_gen_campaign",
    "competitive_enablement",
]

# Unsupported-superiority phrasing to reject (feedback #40).
_SUPERIORITY_PATTERNS = [
    re.compile(r"\btruly unified\b", re.I),
    re.compile(r"\bmerely stitch", re.I),
    re.compile(r"cannot copy", re.I),
    re.compile(r"\brippling'?s claim is stronger\b", re.I),
    re.compile(r"\bis a true (unified|single) system\b", re.I),
]

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
_VALID_CATEGORIES = {
    "product_marketing_proof",
    "demand_gen_campaign",
    "competitive_enablement",
    "content_research",
    "brand_event",
}


class ComparabilityDraft(BaseModel):
    competitor_product: str = ""
    rippling_product: str = ""
    shared_buyer_job: str | None = None
    shared_workflow: str | None = None
    overlapping_capabilities: list[str] = Field(default_factory=list)
    materially_different_capabilities: list[str] = Field(default_factory=list)
    comparability: str = "weak"
    rationale: str = ""


class StageDraft(BaseModel):
    stage: str = "build_proof"
    objective: str = ""
    proceed_if: list[str] = Field(default_factory=list)
    stop_or_reframe_if: list[str] = Field(default_factory=list)


class CampaignDraft(BaseModel):
    audience: list[str] = Field(default_factory=list)
    category_entry_point: str | None = None
    message: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    creative_or_asset: str | None = None
    landing_experience: str | None = None
    primary_metric: str | None = None


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
    product_comparability: ComparabilityDraft = Field(default_factory=ComparabilityDraft)
    structural_defensibility: str = "medium"
    why_competitor_cannot_easily_copy: str = ""
    why_this_could_backfire: str
    comparative_claim_risk: str = "medium"
    legal_review_required: bool = True
    staged_plan: list[StageDraft] = Field(default_factory=list)
    campaign_plan: CampaignDraft | None = None
    experiment_hypothesis: str = ""
    primary_metric: str = ""
    guardrail_metrics: list[str] = Field(default_factory=list)
    minimum_sample_rule: str = ""
    scale_rule: str = ""
    iterate_rule: str = ""
    kill_rule: str = ""


def _already_saying_it(gap: MessageProofGap) -> str:
    return "underweighted" if gap.rippling_equivalent_claim else "net_new"


def _is_generic(draft: OpportunityDraft) -> bool:
    """Genericness critic (feedback #39): reject actions that name no specific
    buyer/product/workflow or that just say 'create a customer story'."""
    text = f"{draft.title} {draft.message_angle}".lower()
    names_specifics = bool(draft.focal_product_focus) and bool(draft.target_personas)
    only_customer_story = "customer story" in text and len(draft.message_angle) < 120
    too_generic = not names_specifics or only_customer_story
    return too_generic


def _superiority_violations(draft: OpportunityDraft) -> list[str]:
    """Unsupported-superiority critic (feedback #40)."""
    blob = " ".join(
        [
            draft.title,
            draft.message_angle,
            draft.why_competitor_cannot_easily_copy,
            draft.promised_transformation,
        ]
    )
    return [p.pattern for p in _SUPERIORITY_PATTERNS if p.search(blob)]


def _default_stages(focal: str) -> list[StagedPlan]:
    return [
        StagedPlan(
            stage="build_proof",
            objective=f"Produce a demonstrable {focal} workflow/proof for the theme.",
            proceed_if=[
                "a demonstrable workflow exists",
                "product and legal validate the claim",
                "at least one credible proof format can be produced",
            ],
            stop_or_reframe_if=[
                "the capability cannot be demonstrated",
                "the comparison cannot be substantiated",
                "the message depends on an unavailable customer quote",
            ],
        ),
        StagedPlan(
            stage="validate_proof",
            objective="Verify the proof holds up to buyer/analyst scrutiny.",
            proceed_if=[
                "proof is repeatable across >=2 environments",
                "no material edge-case gaps",
            ],
            stop_or_reframe_if=["proof is anecdotal or has sync/edge-case gaps"],
        ),
        StagedPlan(
            stage="launch_campaign",
            objective="Run the campaign against the competitor keyword/audience.",
            proceed_if=[
                "qualified conversion exceeds the agreed competitor-campaign baseline",
                "sales feedback confirms message relevance",
            ],
            stop_or_reframe_if=[
                "both engagement and qualified conversion trail the baseline",
            ],
        ),
    ]


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
    category_counts: dict[str, int] = {}
    rejected: list[str] = []

    for rank, gap in enumerate(gaps):
        if len(opportunities) >= MAX_OPPORTUNITIES:
            break
        required_category = _CATEGORY_BY_RANK[min(rank, len(_CATEGORY_BY_RANK) - 1)]
        # Diversity rule: no more than two of the same category.
        if category_counts.get(required_category, 0) >= 2:
            required_category = next(
                (c for c in _CATEGORY_BY_RANK if category_counts.get(c, 0) < 2), required_category
            )
        prompt = prompts.get(GEN_PROMPT_NAME)
        dist = gap.proof_distribution.summary_line() if gap.proof_distribution else "not profiled"
        rendered = prompt.render(
            focal_company=focal_name,
            competitor=competitor,
            required_category=required_category,
            claim_text=gap.claim_text,
            theme=gap.short_label or gap.claim_type,
            proof_distribution=dist,
            proof_strength=gap.proof_strength,
            focal_equivalent=gap.rippling_equivalent_claim or "not observed",
            focal_strength=gap.rippling_proof_strength,
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("opportunity generation failed for gap %s: %s", gap.claim_id, exc)
            continue

        # Critics (feedback #39/#40).
        violations = _superiority_violations(draft)
        if violations:
            rejected.append(f"{gap.claim_id}: unsupported superiority ({violations})")
            if ctx.trace:
                ctx.trace.append(
                    "opportunity_rejected",
                    {"gap": gap.claim_id, "reason": "unsupported_superiority"},
                )
            continue
        if _is_generic(draft):
            rejected.append(f"{gap.claim_id}: too generic")
            if ctx.trace:
                ctx.trace.append("opportunity_rejected", {"gap": gap.claim_id, "reason": "generic"})
            continue

        title_key = draft.title.strip().lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        category_counts[required_category] = category_counts.get(required_category, 0) + 1

        opportunities.append(_to_opportunity(draft, gap, required_category, competitor, focal_name))

    if rejected and ctx.trace:
        ctx.trace.append("opportunities_critiqued", {"rejected": rejected})
    ctx.scratch["opportunities_rejected"] = rejected
    return opportunities


def _to_opportunity(
    draft: OpportunityDraft, gap: MessageProofGap, category: str, competitor: str, focal: str
) -> MarketingOpportunity:
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
    action_category = category if category in _VALID_CATEGORIES else "product_marketing_proof"

    comparability = ProductComparability(
        competitor_product=draft.product_comparability.competitor_product
        or f"{competitor} product",
        rippling_product=draft.product_comparability.rippling_product
        or (draft.focal_product_focus[0] if draft.focal_product_focus else f"{focal} product"),
        shared_buyer_job=draft.product_comparability.shared_buyer_job,
        shared_workflow=draft.product_comparability.shared_workflow,
        overlapping_capabilities=draft.product_comparability.overlapping_capabilities,
        materially_different_capabilities=draft.product_comparability.materially_different_capabilities,
        comparability=cast(
            Any,
            draft.product_comparability.comparability
            if draft.product_comparability.comparability in ("strong", "partial", "weak")
            else "weak",
        ),
        rationale=draft.product_comparability.rationale,
    )
    staged = [
        StagedPlan(
            stage=cast(
                Any,
                s.stage
                if s.stage in ("build_proof", "validate_proof", "launch_campaign")
                else "build_proof",
            ),
            objective=s.objective,
            proceed_if=s.proceed_if,
            stop_or_reframe_if=s.stop_or_reframe_if,
        )
        for s in draft.staged_plan
    ] or _default_stages(focal)
    campaign = None
    if draft.campaign_plan and (
        action_category == "demand_gen_campaign" or draft.campaign_plan.audience
    ):
        cp = draft.campaign_plan
        campaign = CampaignPlan(
            audience=cp.audience,
            category_entry_point=cp.category_entry_point,
            message=cp.message,
            channels=cp.channels,
            creative_or_asset=cp.creative_or_asset,
            landing_experience=cp.landing_experience,
            primary_metric=cp.primary_metric,
        )

    return MarketingOpportunity(
        opportunity_id=new_id("OPP"),
        title=draft.title,
        opportunity_type=draft.opportunity_type,
        action_category=cast(Any, action_category),
        competitor_pattern=f"{competitor} repeats “{gap.claim_text}” with {gap.proof_strength} observed public proof.",
        change_over_time="see change events; message–proof gap is current-state unless a change event is linked",
        supporting_claim_ids=[gap.claim_id],
        campaign_plan=campaign,
        staged_plan=staged,
        product_comparability=comparability,
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
        guardrail_metrics=draft.guardrail_metrics or ["brand sentiment", "legal review sign-off"],
        minimum_sample_rule=draft.minimum_sample_rule
        or "run until 2 weeks or 300 sessions per variant",
        scale_rule=draft.scale_rule
        or "scale if primary metric beats the competitor-campaign baseline with no guardrail regression",
        iterate_rule=draft.iterate_rule
        or "iterate creative if engagement is strong but qualified conversion is weak",
        kill_rule=draft.kill_rule
        or "kill if both engagement and qualified conversion trail the baseline",
    )
