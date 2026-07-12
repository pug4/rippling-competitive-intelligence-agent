"""Conversational behavior: feedback, retry lineage, challenge, deep-dive, and a
follow-up router (blueprint §25, §37.27, §39.11).

Retries never overwrite the parent: a child run is created with a
parent→child relationship, driven under its retry mode, and a difference
report is produced so the user can compare. Follow-ups answer from stored
state first and only collect new evidence when the corpus is insufficient.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .graph import load_state
from .runner import _build_context, create_run, drive
from .schemas.common import utcnow

RETRY_MODES = {
    "reanalyze_same_evidence",
    "collect_deeper_evidence",
    "retry_failed_sources",
    "use_alternative_provider",
    "challenge_conclusion",
    "narrow_scope",
    "expand_time_horizon",
    "compare_another_company",
}


def record_feedback(
    run_id: str,
    *,
    target_type: str = "report",
    target_id: str | None = None,
    feedback_type: str,
    reason: str | None = None,
) -> str:
    """Persist a thumbs-up/down/retry signal. Does not retrain (§37.27)."""
    ctx = _build_context(run_id)
    return ctx.repository.save_feedback(
        run_id,
        target_type=target_type,
        target_id=target_id or run_id,
        feedback_type=feedback_type,
        reason=reason or "",
    )


def _load_parent(run_id: str):
    from .config import get_settings
    from .storage.repository import Repository

    row = Repository.open(get_settings().db_path).get_run(run_id)
    if row is None:
        raise KeyError(f"run not found: {run_id}")
    return row


def create_retry(
    parent_run_id: str,
    *,
    retry_mode: str,
    target_id: str | None = None,
    user_reason: str | None = None,
    focus: list[str] | None = None,
) -> dict[str, Any]:
    """Create and drive a child run under ``retry_mode``, preserving lineage,
    then return a difference report (§25 retry output)."""
    if retry_mode not in RETRY_MODES:
        raise ValueError(f"unknown retry_mode {retry_mode!r}; expected one of {sorted(RETRY_MODES)}")
    parent_row = _load_parent(parent_run_id)
    parent_ctx = _build_context(parent_run_id, execution_mode=parent_row["execution_mode"] or "fixture")
    parent_state = load_state(parent_ctx.repository, parent_run_id)

    # Map retry mode -> child research mode / scope adjustments.
    mode = parent_state.mode
    lookback = parent_state.lookback_days
    child_focus = list(focus or parent_state.user_focus)
    # reanalyze/challenge reuse the parent's evidence with no new collection;
    # the graph pulls the parent's artifacts via the reuse_evidence action and
    # links (never reassigns) them, so the parent's analysis is preserved.
    reuse_evidence_only = retry_mode in ("reanalyze_same_evidence", "challenge_conclusion")
    if retry_mode == "challenge_conclusion":
        mode = "challenge"
    elif retry_mode == "collect_deeper_evidence":
        mode = "focused_deep_dive"
    elif retry_mode == "expand_time_horizon":
        mode = "longitudinal"
        lookback = min(lookback * 2, 1095)
    elif retry_mode == "narrow_scope" and child_focus:
        mode = "focused_deep_dive"

    child_state, child_ctx = create_run(
        parent_state.company_input,
        mode=mode,
        execution_mode=parent_state.execution_mode,
        compare_to=parent_state.compare_to,
        lookback_days=lookback,
        user_focus=child_focus,
        parent_run_id=parent_run_id,
        retry_mode=retry_mode,
        reuse_evidence_only=reuse_evidence_only,
    )
    # Parent→child lineage is recorded on the child run row (parent_run_id +
    # retry_mode) by create_run above.
    child_state = asyncio.run(drive(child_state, child_ctx))
    diff = _difference_report(parent_ctx, child_ctx, parent_run_id, child_state.run_id, retry_mode, user_reason)
    return diff


def _counts(ctx, run_id: str) -> dict[str, Any]:
    # Fresh connection so counts reflect committed writes from other connections
    # (SQLite WAL gives each connection a consistent snapshot from its last txn).
    from .config import get_settings
    from .storage.repository import Repository

    repo = Repository.open(get_settings().db_path)
    claims = repo.list_claims(run_id=run_id)
    opps = repo.list_opportunities(run_id=run_id)
    return {
        "artifacts": len(repo.list_artifacts(run_id=run_id)),
        "claims": [getattr(c, "statement", "")[:80] for c in claims if c.__class__.__name__ == "StrategicClaim"],
        "changes": len([c for c in claims if c.__class__.__name__ == "ChangeEvent"]),
        "opportunities": [getattr(o, "title", "") for o in opps if o.__class__.__name__ == "MarketingOpportunity"],
    }


def _difference_report(
    parent_ctx, child_ctx, parent_run_id: str, child_run_id: str, retry_mode: str, user_reason: str | None
) -> dict[str, Any]:
    p = _counts(parent_ctx, parent_run_id)
    c = _counts(child_ctx, child_run_id)
    p_opps, c_opps = set(p["opportunities"]), set(c["opportunities"])
    p_claims, c_claims = set(p["claims"]), set(c["claims"])
    child_state = load_state(child_ctx.repository, child_run_id)
    return {
        "parent_run_id": parent_run_id,
        "child_run_id": child_run_id,
        "retry_mode": retry_mode,
        "reason": user_reason,
        "created_at": utcnow().isoformat(),
        "artifacts": {"parent": p["artifacts"], "child": c["artifacts"]},
        "new_opportunities": sorted(c_opps - p_opps),
        "removed_opportunities": sorted(p_opps - c_opps),
        "unchanged_opportunities": sorted(p_opps & c_opps),
        "new_claims": sorted(c_claims - p_claims),
        "changed_claims_count": len(c_claims ^ p_claims),
        "child_stop_reason": child_state.stop_reason,
        "final_difference_summary": _summary(p, c, retry_mode),
    }


def _summary(p: dict, c: dict, retry_mode: str) -> str:
    da = c["artifacts"] - p["artifacts"]
    do = len(set(c["opportunities"]) - set(p["opportunities"]))
    return (
        f"Retry ({retry_mode}): {da:+d} artifacts, {do} new opportunity(ies), "
        f"{c['changes']} change events (parent {p['changes']})."
    )


def answer_followup(run_id: str, question: str) -> dict[str, Any]:
    """Follow-up router (§39.11): answer from stored state first; only collect
    new evidence when the corpus is insufficient. Phase 4 returns the routing
    decision + a from-state answer; a focused child run handles collection."""
    ctx = _build_context(run_id)
    load_state(ctx.repository, run_id)  # validates the run exists / is loadable
    q = question.lower()
    # Cheap intent routing.
    if any(k in q for k in ("why", "evidence", "show")):
        claims = [c for c in ctx.repository.list_claims(run_id=run_id) if c.__class__.__name__ == "StrategicClaim"]
        return {
            "route": "answer_from_state",
            "answer": "See the grounded claims and their evidence IDs.",
            "claims": [
                {"statement": getattr(c, "statement", ""), "status": getattr(c, "status", ""),
                 "evidence_ids": getattr(c, "evidence_ids", [])}
                for c in claims[:10]
            ],
        }
    if "challenge" in q or "disprove" in q:
        return {"route": "challenge", "action": "run: competitive-agent challenge " + run_id}
    if any(k in q for k in ("deeper", "pricing", "enterprise", "go deeper")):
        return {"route": "focused_deep_dive", "action": "run: competitive-agent deepen " + run_id + " --focus <dimension>"}
    if "compare" in q or "run this again" in q:
        return {"route": "compare_another_company", "action": "run: competitive-agent analyze <company>"}
    return {"route": "clarify", "question": "Could you specify the dimension or company to focus on?"}


def write_diff_report(diff: dict[str, Any], out_dir: Any) -> Any:
    from pathlib import Path

    path = Path(out_dir) / f"retry_diff_{diff['child_run_id']}.json"
    path.write_text(json.dumps(diff, indent=2, default=str), encoding="utf-8")
    return path
