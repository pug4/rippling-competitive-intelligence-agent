"""Node implementations for the Research Director graph.

Phase 0 ships the structural loop: initialization, resolution, windows,
coverage assessment, stop policy, and minimal rendering are real; research
nodes are explicit passthroughs that later phases replace (they are wired,
traced, and idempotent, so replacing one never changes the driver).
"""

from __future__ import annotations

from datetime import timedelta

from . import coverage as cov
from .graph import GraphContext
from .schemas.common import new_id, utcnow
from .schemas.company import Company, TimeWindow
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
        # Looks like a domain: accept with medium confidence, name from label.
        label = key.split(".")[0].capitalize()
        return Company(
            company_id=new_id("CO"),
            canonical_name=label,
            primary_domain=key,
            resolved_at=utcnow(),
            resolution_confidence="medium",
        )
    return None


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

    focal_cfg = ctx.config.focal_company if ctx.config else None
    focal_input = state.compare_to or (focal_cfg.domain if focal_cfg else "rippling.com")
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
    # Phase 5 loads/reuses the focal-company baseline package here.
    return state, "assess_coverage"


async def assess_coverage(state: DirectorState, ctx: GraphContext):
    ok, missing = cov.sufficient(state.coverage, state.mode, bool(state.compare_to))
    ctx.scratch["coverage_missing"] = missing
    if ctx.trace:
        ctx.trace.append("coverage_assessed", {"missing": missing, "coverage": state.coverage})
    return state, "identify_unresolved_questions"


async def identify_unresolved_questions(state: DirectorState, ctx: GraphContext):
    return state, "propose_actions"


async def propose_actions(state: DirectorState, ctx: GraphContext):
    ctx.scratch.setdefault("proposed_actions", [])
    return state, "score_actions"


async def score_actions(state: DirectorState, ctx: GraphContext):
    return state, "select_next_action"


async def select_next_action(state: DirectorState, ctx: GraphContext):
    proposed = ctx.scratch.get("proposed_actions") or []
    if not proposed:
        # Nothing actionable this cycle: fall through to the stop decision.
        return state, "decide_continue_or_stop"
    return state, "execute_action"


async def execute_action(state: DirectorState, ctx: GraphContext):
    return state, "normalize_and_deduplicate"


async def normalize_and_deduplicate(state: DirectorState, ctx: GraphContext):
    return state, "extract_and_classify"


async def extract_and_classify(state: DirectorState, ctx: GraphContext):
    return state, "validate_evidence"


async def validate_evidence(state: DirectorState, ctx: GraphContext):
    return state, "update_coverage"


async def update_coverage(state: DirectorState, ctx: GraphContext):
    return state, "refresh_claims"


async def refresh_claims(state: DirectorState, ctx: GraphContext):
    return state, "check_contradictions"


async def check_contradictions(state: DirectorState, ctx: GraphContext):
    return state, "verify_temporal_changes"


async def verify_temporal_changes(state: DirectorState, ctx: GraphContext):
    return state, "build_matrices"


async def build_matrices(state: DirectorState, ctx: GraphContext):
    return state, "run_focal_mirror_check"


async def run_focal_mirror_check(state: DirectorState, ctx: GraphContext):
    return state, "generate_opportunities"


async def generate_opportunities(state: DirectorState, ctx: GraphContext):
    return state, "critique_opportunities"


async def critique_opportunities(state: DirectorState, ctx: GraphContext):
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
        ok, missing = cov.sufficient(state.coverage, state.mode, bool(state.compare_to))
        proposed = ctx.scratch.get("proposed_actions") or []
        if ok:
            reason = "required_coverage_reached"
        elif not proposed:
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
    # The CLI/UI surfaces state.pending_user_question; the run resumes on answer.
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
