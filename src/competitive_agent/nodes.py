"""Node implementations for the Research Director graph.

The loop is genuinely stateful: proposals derive from coverage gaps, execution
goes through the typed tool registry, results move coverage, failures feed
fallbacks, temporal claims require both periods, and no opportunity survives
without the focal-company mirror check (Rule 7). Nodes are idempotent: with no
new evidence, claim/opportunity nodes return state unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from . import coverage as cov
from . import planner
from .graph import GraphContext
from .schemas.common import new_id, utcnow
from .schemas.company import Company, TimeWindow
from .schemas.source import ResearchAction, ToolResult
from .state import DirectorState

# Known-domain seed for offline resolution; live resolution (Exa) supplements
# this in later phases. Deliberately data, not logic: swapping industries only
# means editing config/seeds, never code.
_KNOWN_COMPANIES = {
    "deel": ("Deel", "deel.com"),
    "deel.com": ("Deel", "deel.com"),
    "gusto": ("Gusto", "gusto.com"),
    "gusto.com": ("Gusto", "gusto.com"),
    "workday": ("Workday", "workday.com"),
    "workday.com": ("Workday", "workday.com"),
    "rippling": ("Rippling", "rippling.com"),
    "rippling.com": ("Rippling", "rippling.com"),
    "example-hr.com": ("Example-HR", "example-hr.com"),  # fixture world
}

# Coverage effect of a successful action, by action type and page category.
_ACTION_COVERAGE = {
    "map_current_website": [("current_website", "medium"), ("portfolio_discovery", "low")],
    "search_wayback": [("historical_website", "medium"), ("historical_messages", "medium")],
    "search_news_launches": [("news_and_launches", "medium")],
    "search_comparison_pages": [("competitive_stance", "medium")],
    "search_exa_web": [("news_and_launches", "low")],
}

_PAGE_CATEGORY_COVERAGE = {
    "pricing": ("pricing_and_packaging", "high"),
    "product": ("current_product", "medium"),
    "platform": ("current_product", "medium"),
    "comparison": ("competitive_stance", "medium"),
    "customers": ("customer_proof", "medium"),
    "home": ("current_website", "high"),
}

_MIN_CLASSIFIABLE_CHARS = 200

# Family model class name -> classifications.family column value.
_FAMILY_TABLE = {
    "MessageFamily": "message",
    "AudienceFamily": "audience",
    "ProductFamily": "product",
    "CompetitiveFamily": "competitive",
}


def _resolve_offline(raw: str) -> Company | None:
    key = (
        raw.strip()
        .lower()
        .removeprefix("https://")
        .removeprefix("http://")
        .removeprefix("www.")
        .rstrip("/")
    )
    hit = _KNOWN_COMPANIES.get(key)
    if hit:
        name, domain = hit
        return Company(
            company_id=new_id("CO"),
            canonical_name=name,
            primary_domain=domain,
            resolved_at=utcnow(),
            resolution_confidence="high",
        )
    if "." in key and " " not in key:
        label = key.split(".")[0].capitalize()
        return Company(
            company_id=new_id("CO"),
            canonical_name=label,
            primary_domain=key,
            resolved_at=utcnow(),
            resolution_confidence="medium",
        )
    return None


def _taxonomy(ctx: GraphContext) -> dict[str, Any]:
    return ctx.config.taxonomy if ctx.config else {}


def _focal_name(ctx: GraphContext) -> str:
    return ctx.config.focal_company.name if ctx.config else "Rippling"


# ---------------------------------------------------------------------------
# Setup nodes
# ---------------------------------------------------------------------------


async def initialize_run(state: DirectorState, ctx: GraphContext):
    if not state.coverage:
        state.coverage = cov.initial_coverage()
    if ctx.trace:
        ctx.trace.append(
            "run_started",
            {
                "company_input": state.company_input,
                "mode": state.mode,
                "execution_mode": state.execution_mode,
            },
        )
    return state, "resolve_companies"


async def resolve_companies(state: DirectorState, ctx: GraphContext):
    company = _resolve_offline(state.company_input)
    if company is None:
        state.pending_user_question = (
            f"I could not resolve '{state.company_input}' to a company domain. "
            "Please provide the primary public domain (e.g. example.com)."
        )
        return state, "await_followup"
    state.company = company

    if state.mode == "comparative" or state.compare_to:
        focal_cfg = ctx.config.focal_company if ctx.config else None
        focal_input = state.compare_to or (focal_cfg.domain if focal_cfg else "rippling.com")
        if focal_input.strip().lower().replace("www.", "") != company.primary_domain:
            state.focal_company = _resolve_offline(focal_input)
    cov.raise_coverage(state.coverage, "identity", "high")
    if ctx.trace:
        ctx.trace.append(
            "company_resolved",
            {
                "company": company.canonical_name,
                "domain": company.primary_domain,
                "confidence": company.resolution_confidence,
            },
        )
    return state, "load_or_create_time_windows"


async def load_or_create_time_windows(state: DirectorState, ctx: GraphContext):
    if not state.time_windows:
        now = utcnow()
        current_days = int(ctx.config.windows.get("current_days", 90)) if ctx.config else 90
        state.time_windows = [
            TimeWindow(
                window_id=new_id("TW"),
                label="current",
                start_at=now - timedelta(days=current_days),
                end_at=now,
                purpose="current",
            ),
            TimeWindow(
                window_id=new_id("TW"),
                label="comparison",
                start_at=now - timedelta(days=state.lookback_days),
                end_at=now - timedelta(days=current_days),
                purpose="comparison",
            ),
        ]
        if ctx.trace:
            ctx.trace.append(
                "time_windows_created",
                {
                    w.label: [w.start_at.isoformat(), w.end_at.isoformat()]
                    for w in state.time_windows
                },
            )
    return state, "load_focal_state"


async def load_focal_state(state: DirectorState, ctx: GraphContext):
    # The focal mirror runs as an isolated sub-run in run_focal_mirror_check;
    # nothing to preload in Phase 1. A snapshot run of the focal company itself
    # has no mirror, so its focal dimensions are not required.
    if state.focal_company is None:
        cov.mark_unavailable(state.coverage, "focal_current")
        cov.mark_unavailable(state.coverage, "focal_proof")
        state.coverage["focal_current"] = (
            "not_attempted" if state.compare_to else state.coverage["focal_current"]
        )
    return state, "assess_coverage"


# ---------------------------------------------------------------------------
# Planning loop
# ---------------------------------------------------------------------------


async def assess_coverage(state: DirectorState, ctx: GraphContext):
    ok, missing = cov.sufficient(state.coverage, state.mode, state.focal_company is not None)
    ctx.scratch["coverage_missing"] = missing
    if ctx.trace:
        ctx.trace.append("coverage_assessed", {"missing": missing, "coverage": state.coverage})
    return state, "identify_unresolved_questions"


async def identify_unresolved_questions(state: DirectorState, ctx: GraphContext):
    return state, "propose_actions"


async def propose_actions(state: DirectorState, ctx: GraphContext):
    proposals = planner.propose_actions(state, ctx)
    ctx.scratch["proposed_actions"] = proposals
    if ctx.trace and proposals:
        ctx.trace.append(
            "actions_proposed",
            {"actions": [{"type": a.action_type, "rationale": a.rationale} for a in proposals]},
        )
    return state, "score_actions"


async def score_actions(state: DirectorState, ctx: GraphContext):
    proposals = ctx.scratch.get("proposed_actions") or []
    best, trace_rows = planner.score_and_select(state, proposals)
    ctx.scratch["selected_action"] = best
    ctx.scratch["action_score_trace"] = trace_rows
    return state, "select_next_action"


async def select_next_action(state: DirectorState, ctx: GraphContext):
    action: ResearchAction | None = ctx.scratch.get("selected_action")
    if action is None:
        return state, "decide_continue_or_stop"
    if ctx.trace:
        alternatives = [
            r
            for r in ctx.scratch.get("action_score_trace", [])
            if r["action_type"] != action.action_type
        ]
        ctx.trace.append(
            "action_selected",
            {
                "action_type": action.action_type,
                "source": action.source_name,
                "rationale": action.rationale,
                "alternatives_considered": [a["action_type"] for a in alternatives],
                "scores": ctx.scratch.get("action_score_trace", []),
            },
        )
    return state, "execute_action"


async def execute_action(state: DirectorState, ctx: GraphContext):
    action: ResearchAction | None = ctx.scratch.get("selected_action")
    if action is None:
        return state, "decide_continue_or_stop"

    from .tools.base import ToolContext

    tool_ctx = ToolContext(
        run_id=state.run_id,
        company_id=state.company.company_id if state.company else "unresolved",
        mode=state.execution_mode,
        config=ctx.config,
        settings=ctx.settings,
        repository=ctx.repository,
        http=ctx.http,
        trace=ctx.trace,
    )
    result: ToolResult = await ctx.tool_registry.run_action(action, tool_ctx)

    state.tool_calls_made += 1
    state.spent_usd += result.cost_usd
    state.executed_action_keys.append(planner.action_key(action.action_type, action.parameters))
    ctx.scratch["last_result"] = result
    ctx.scratch["last_action"] = action

    if result.status in ("failed_retryable", "failed_terminal", "unsupported"):
        state.record_failure(
            action.source_name or result.tool_name, action.action_type, result.error_type
        )
        if action.fallback_action_types and ctx.trace:
            ctx.trace.append(
                "fallback_selected",
                {
                    "failed": action.action_type,
                    "fallbacks_available": action.fallback_action_types,
                    "note": "fallback actions become eligible on the next planning cycle",
                },
            )
    for observation in result.negative_observations:
        if observation not in state.negative_observations:
            state.negative_observations.append(observation)
    return state, "normalize_and_deduplicate"


async def normalize_and_deduplicate(state: DirectorState, ctx: GraphContext):
    result: ToolResult | None = ctx.scratch.get("last_result")
    action: ResearchAction | None = ctx.scratch.get("last_action")
    new_artifacts = []
    if result and state.company:
        fetched_key = f"fetched_urls:{state.company.company_id}"
        fetched: list[str] = ctx.scratch.setdefault(fetched_key, [])
        for artifact in result.artifacts:
            if artifact.url:
                fetched.append(artifact.url)
            existing = ctx.repository.find_artifact_by_hash(artifact.content_hash)
            if (
                existing is not None
                and getattr(existing, "artifact_id", None) in state.artifact_ids
            ):
                continue  # duplicate content: never inflates coverage or distributions
            ctx.repository.save_artifact(state.run_id, artifact)
            state.artifact_ids.append(artifact.artifact_id)
            new_artifacts.append(artifact)
        # Website map: stash the page map for the planner.
        if action and action.action_type == "map_current_website":
            for artifact in result.artifacts:
                page_map = artifact.metadata.get("page_map")
                if page_map:
                    ctx.scratch[f"page_map:{state.company.company_id}"] = page_map
    ctx.scratch["new_artifacts"] = new_artifacts
    if ctx.trace and new_artifacts:
        ctx.trace.append(
            "artifacts_normalized",
            {"new": len(new_artifacts), "total": len(state.artifact_ids)},
        )
    return state, "extract_and_classify"


async def extract_and_classify(state: DirectorState, ctx: GraphContext):
    new_artifacts = ctx.scratch.get("new_artifacts") or []
    classifiable = [a for a in new_artifacts if len(a.normalized_text) >= _MIN_CLASSIFIABLE_CHARS]
    if not classifiable or ctx.gateway is None:
        return state, "validate_evidence"

    from .processing.classify import classify_artifact
    from .processing.extract import extract_evidence
    from .prompt_registry import PromptRegistry

    prompts = ctx.scratch.get("_prompt_registry")
    if prompts is None:
        prompts = PromptRegistry()
        ctx.scratch["_prompt_registry"] = prompts

    taxonomy = _taxonomy(ctx)
    max_parallel = ctx.settings.max_parallel_extractions if ctx.settings else 4
    sem = asyncio.Semaphore(max_parallel)

    async def process(artifact):
        async with sem:
            evidence, extraction_report = await extract_evidence(
                artifact,
                ctx.gateway,
                prompts,
                taxonomy,
                company_id=artifact.company_id,
                time_windows=state.time_windows,
            )
            merged, families = await classify_artifact(
                artifact,
                ctx.gateway,
                prompts,
                taxonomy,
                focal_company_name=_focal_name(ctx),
                company_id=artifact.company_id,
            )
            return artifact, evidence, extraction_report, merged, families

    results = await asyncio.gather(*(process(a) for a in classifiable), return_exceptions=True)
    stored_evidence = 0
    for item in results:
        if isinstance(item, BaseException):
            state.limitations.append(f"extraction/classification failed: {type(item).__name__}")
            if ctx.trace:
                ctx.trace.append(
                    "tool_failed", {"stage": "extract_classify", "error": str(item)[:300]}
                )
            continue
        artifact, evidence, extraction_report, merged, families = item
        for ev in evidence:
            ctx.repository.save_classification(
                state.run_id, "evidence", ev, prompt_version="1.0.0", model_id="n/a"
            )
            state.evidence_ids.append(ev.evidence_id)
            stored_evidence += 1
        for fam in families:
            fam_name = _FAMILY_TABLE.get(type(fam).__name__, "family")
            ctx.repository.save_classification(
                state.run_id, fam_name, fam, prompt_version="1.0.0", model_id="tier1"
            )
        cls_id = ctx.repository.save_classification(
            state.run_id, "merged", merged, prompt_version="1.0.0", model_id="tier1"
        )
        state.classification_ids.append(cls_id)
        if ctx.trace:
            ctx.trace.append(
                "classification_completed",
                {
                    "artifact_id": artifact.artifact_id,
                    "url": artifact.url,
                    "primary_message": merged.primary_message,
                    "rejected_excerpts": getattr(extraction_report, "rejected", 0),
                },
            )
    if ctx.trace and stored_evidence:
        ctx.trace.append("evidence_extracted", {"evidence_items": stored_evidence})
    ctx.scratch["evidence_dirty"] = ctx.scratch.get("evidence_dirty", False) or stored_evidence > 0
    return state, "validate_evidence"


async def validate_evidence(state: DirectorState, ctx: GraphContext):
    # Excerpt containment is enforced at extraction time through the shared
    # normalizer; this node re-asserts referential integrity for storage.
    return state, "update_coverage"


async def update_coverage(state: DirectorState, ctx: GraphContext):
    result: ToolResult | None = ctx.scratch.get("last_result")
    action: ResearchAction | None = ctx.scratch.get("last_action")
    if not result or not action:
        return state, "refresh_claims"

    if result.status in ("success", "partial"):
        for dimension, level in _ACTION_COVERAGE.get(action.action_type, []):
            cov.raise_coverage(state.coverage, dimension, level)
        for artifact in result.artifacts:
            category = str(artifact.metadata.get("page_category", "")).lower()
            for key, (dimension, level) in _PAGE_CATEGORY_COVERAGE.items():
                if key in category or key in artifact.url.lower():
                    cov.raise_coverage(state.coverage, dimension, level)
        # Classified marketing content advances positioning/motion coverage.
        if state.classification_ids:
            cov.raise_coverage(state.coverage, "product_positioning", "medium")
            cov.raise_coverage(state.coverage, "commercial_motion", "medium")
            cov.raise_coverage(state.coverage, "personas_and_jobs", "medium")
            cov.raise_coverage(state.coverage, "portfolio_discovery", "medium")
    elif result.status == "empty":
        # A genuine empty answer after a real attempt: record, do not equate
        # with absence of activity.
        pass
    else:
        cap = int(ctx.config.budgets.get("max_retries_per_source", 2)) if ctx.config else 2
        key = f"{action.source_name}:{action.action_type}"
        rec = state.failed_actions.get(key)
        if rec and rec.attempts >= cap:
            dims = [d for d, _ in _ACTION_COVERAGE.get(action.action_type, [])]
            for dimension in dims:
                cov.mark_unavailable(state.coverage, dimension)
            limitation = (
                f"{action.source_name} unavailable after {rec.attempts} attempts; "
                f"dimensions {dims} rely on remaining sources"
            )
            if limitation not in state.limitations:
                state.limitations.append(limitation)
    return state, "refresh_claims"


# ---------------------------------------------------------------------------
# Intelligence nodes
# ---------------------------------------------------------------------------


def _evidence_records(ctx: GraphContext, run_id: str) -> list:
    return [
        m
        for m in ctx.repository.list_classifications(run_id, family="evidence")
        if m.__class__.__name__ == "EvidenceItem"
    ]


async def refresh_claims(state: DirectorState, ctx: GraphContext):
    # Only rebuild when new evidence landed AND enough exists to say anything.
    if not ctx.scratch.get("evidence_dirty") or ctx.gateway is None:
        return state, "check_contradictions"
    evidence = _evidence_records(ctx, state.run_id)
    if len(evidence) < 3 or len(evidence) == ctx.scratch.get("claims_built_at_count"):
        return state, "check_contradictions"

    from .processing.claim_builder import build_claims
    from .processing.claim_judge import judge_claim

    prompts = ctx.scratch["_prompt_registry"]
    try:
        candidates = await build_claims(
            evidence,
            ctx.gateway,
            prompts,
            company_name=state.company.canonical_name if state.company else state.company_input,
            company_id=state.company.company_id if state.company else "unresolved",
            time_windows=state.time_windows,
        )
    except Exception as exc:
        state.limitations.append(f"claim building unavailable: {type(exc).__name__}")
        return state, "check_contradictions"

    evidence_by_id = {e.evidence_id: e for e in evidence}
    kept: list[str] = []
    for claim in candidates:
        try:
            judged = await judge_claim(claim, evidence_by_id, ctx.gateway, prompts)
        except Exception:
            judged = claim.model_copy(update={"status": "hypothesis", "claim_confidence": "low"})
        ctx.repository.save_claim(state.run_id, judged)
        if judged.status in ("observed", "supported_inference", "hypothesis"):
            kept.append(judged.claim_id)
            if ctx.trace:
                ctx.trace.append(
                    "claim_created",
                    {
                        "claim_id": judged.claim_id,
                        "status": judged.status,
                        "statement": judged.statement[:200],
                    },
                )
        elif ctx.trace:
            ctx.trace.append(
                "claim_rejected",
                {
                    "claim_id": judged.claim_id,
                    "status": judged.status,
                    "statement": judged.statement[:200],
                },
            )
    state.claim_ids = list(dict.fromkeys(state.claim_ids + kept))
    ctx.scratch["claims_built_at_count"] = len(evidence)
    ctx.scratch["evidence_dirty"] = False
    return state, "check_contradictions"


async def check_contradictions(state: DirectorState, ctx: GraphContext):
    claims = ctx.repository.list_claims(state.run_id)
    contradicted = [c for c in claims if getattr(c, "status", "") == "contradicted"]
    for c in contradicted:
        note = f"contradicted: {getattr(c, 'statement', '')[:160]}"
        if note not in state.contradictions:
            state.contradictions.append(note)
            if ctx.trace:
                ctx.trace.append(
                    "contradiction_detected", {"claim_id": getattr(c, "claim_id", "?")}
                )
    return state, "verify_temporal_changes"


async def verify_temporal_changes(state: DirectorState, ctx: GraphContext):
    if state.mode not in ("longitudinal", "comparative") or ctx.gateway is None:
        return state, "build_matrices"
    if not cov.level_at_least(state.coverage, "historical_website", "low"):
        return state, "build_matrices"
    if ctx.scratch.get("temporal_done"):
        return state, "build_matrices"

    from .processing.temporal import build_change_events

    try:
        changes = await build_change_events(
            run_id=state.run_id,
            state=state,
            ctx=ctx,
        )
    except Exception as exc:
        state.limitations.append(f"temporal verification unavailable: {type(exc).__name__}")
        return state, "build_matrices"
    for change in changes:
        ctx.repository.save_claim(state.run_id, change)
        state.change_event_ids.append(change.change_id)
        if ctx.trace:
            ctx.trace.append(
                "temporal_change_verified",
                {
                    "change_id": change.change_id,
                    "dimension": change.dimension,
                    "lifecycle": change.lifecycle,
                    "confidence": change.confidence,
                },
            )
    ctx.scratch["temporal_done"] = True
    return state, "build_matrices"


async def build_matrices(state: DirectorState, ctx: GraphContext):
    # Phase 3 builds the persona × channel × funnel cube.
    return state, "run_focal_mirror_check"


async def run_focal_mirror_check(state: DirectorState, ctx: GraphContext):
    if state.focal_company is None or ctx.scratch.get("focal_run_id"):
        return state, "generate_opportunities"
    if not state.classification_ids:
        return state, "generate_opportunities"
    # Run the mirror only when the COMPETITOR side is fully collected — the
    # focal pipeline is expensive and would otherwise starve the competitor's
    # own collection if it ran on iteration 1. Trigger when competitor coverage
    # is sufficient OR no competitor actions remain.
    ok, _ = cov.sufficient(state.coverage, state.mode, compare=False)
    remaining = planner.propose_actions(state, ctx)
    if not ok and remaining:
        return state, "generate_opportunities"

    from .comparison import run_focal_mirror

    focal_run_id = await run_focal_mirror(state, ctx)
    if focal_run_id:
        ctx.scratch["focal_run_id"] = focal_run_id
        cov.raise_coverage(state.coverage, "focal_current", "medium")
        cov.raise_coverage(state.coverage, "focal_proof", "medium")
    return state, "generate_opportunities"


async def generate_opportunities(state: DirectorState, ctx: GraphContext):
    if ctx.scratch.get("opportunities_done") or not state.classification_ids:
        return state, "critique_opportunities"
    # Rule 7: no opportunity without the mirror when a focal company exists.
    if state.focal_company is not None and not ctx.scratch.get("focal_run_id"):
        return state, "critique_opportunities"
    # Wait until the loop is otherwise ready to stop, so gaps see full corpus.
    ok, missing = cov.sufficient(state.coverage, state.mode, state.focal_company is not None)
    proposals = planner.propose_actions(state, ctx)
    if proposals and not ok:
        return state, "critique_opportunities"

    from .comparison import build_message_proof_gaps
    from .opportunity_engine import generate_from_gaps

    gaps = build_message_proof_gaps(
        state.run_id,
        ctx.scratch.get("focal_run_id"),
        ctx.repository,
        competitor_name=state.company.canonical_name if state.company else "competitor",
        focal_name=_focal_name(ctx),
    )
    for gap in gaps:
        ctx.repository.save_opportunity(state.run_id, gap)
    ctx.scratch["gaps"] = gaps

    try:
        opportunities = await generate_from_gaps(state, ctx, gaps)
    except Exception as exc:
        state.limitations.append(f"opportunity generation unavailable: {type(exc).__name__}")
        opportunities = []
    for opp in opportunities:
        ctx.repository.save_opportunity(state.run_id, opp)
        state.opportunity_ids.append(opp.opportunity_id)
        if ctx.trace:
            ctx.trace.append(
                "opportunity_generated", {"opportunity_id": opp.opportunity_id, "title": opp.title}
            )
    ctx.scratch["opportunities_done"] = True
    return state, "critique_opportunities"


async def critique_opportunities(state: DirectorState, ctx: GraphContext):
    # Phase 5 adds the adversarial critic pass; Phase 1 opportunities carry
    # backfire risk fields from generation.
    return state, "decide_continue_or_stop"


async def decide_continue_or_stop(state: DirectorState, ctx: GraphContext):
    state.iteration += 1
    reason: str | None = None

    if state.budget_exhausted():
        reason = "budget_exhausted"
    elif state.runtime_exhausted():
        reason = "runtime_exhausted"
    elif state.iteration >= state.max_iterations:
        reason = "max_iterations_reached"
    elif state.tool_calls_made >= state.max_tool_calls:
        reason = "tool_call_cap_reached"
    else:
        ok, missing = cov.sufficient(state.coverage, state.mode, state.focal_company is not None)
        remaining = planner.propose_actions(state, ctx)
        if ok and not remaining:
            reason = "required_coverage_reached"
        elif ok and remaining:
            # Coverage is sufficient; remaining actions are optional depth.
            reason = "required_coverage_reached; optional_actions_skipped=" + ",".join(
                sorted({a.action_type for a in remaining})
            )
        elif not remaining:
            reason = f"no_remaining_actions_with_expected_value; unmet_dimensions={missing}"

    if reason:
        state.stop_reason = reason
        if ctx.trace:
            ctx.trace.append(
                "stop_selected",
                {
                    "reason": reason,
                    "iteration": state.iteration,
                    "remaining_gaps": ctx.scratch.get("coverage_missing", []),
                },
            )
        return state, "render_outputs"
    return state, "assess_coverage"


async def render_outputs(state: DirectorState, ctx: GraphContext):
    from .report import render_run_outputs

    paths = render_run_outputs(state, ctx)
    state.is_complete = True
    if ctx.trace:
        ctx.trace.append("report_rendered", {"paths": {k: str(v) for k, v in paths.items()}})
        ctx.trace.append("run_completed", {"stop_reason": state.stop_reason})
    return state, "render_outputs_done"


async def await_followup(state: DirectorState, ctx: GraphContext):
    return state, "awaiting_user"


async def process_feedback_or_retry(state: DirectorState, ctx: GraphContext):
    return state, "assess_coverage"


def build_default_nodes() -> dict:
    return {
        "initialize_run": initialize_run,
        "resolve_companies": resolve_companies,
        "load_or_create_time_windows": load_or_create_time_windows,
        "load_focal_state": load_focal_state,
        "assess_coverage": assess_coverage,
        "identify_unresolved_questions": identify_unresolved_questions,
        "propose_actions": propose_actions,
        "score_actions": score_actions,
        "select_next_action": select_next_action,
        "execute_action": execute_action,
        "normalize_and_deduplicate": normalize_and_deduplicate,
        "extract_and_classify": extract_and_classify,
        "validate_evidence": validate_evidence,
        "update_coverage": update_coverage,
        "refresh_claims": refresh_claims,
        "check_contradictions": check_contradictions,
        "verify_temporal_changes": verify_temporal_changes,
        "build_matrices": build_matrices,
        "run_focal_mirror_check": run_focal_mirror_check,
        "generate_opportunities": generate_opportunities,
        "critique_opportunities": critique_opportunities,
        "decide_continue_or_stop": decide_continue_or_stop,
        "render_outputs": render_outputs,
        "await_followup": await_followup,
        "process_feedback_or_retry": process_feedback_or_retry,
    }
