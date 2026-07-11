"""Deterministic report rendering: Markdown brief + JSON package + trace.

Both renderers consume ONLY validated stored records (state + repository) —
the UI later consumes the same JSON package, never separate logic (§40.6).
Phase 0 renders the structural skeleton; later phases fill sections in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import GraphContext
from .schemas.common import utcnow
from .state import DirectorState

JSON_SCHEMA_VERSION = "1.0.0"


def run_output_dir(state: DirectorState, ctx: GraphContext) -> Path:
    base = ctx.settings.outputs_dir if ctx.settings else Path("outputs")
    d = Path(base) / "runs" / state.run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_json_package(state: DirectorState, ctx: GraphContext) -> dict[str, Any]:
    repo = ctx.repository
    artifacts: list[dict] = []
    claims: list[dict] = []
    change_events: list[dict] = []
    opportunities: list[dict] = []
    proof_gaps: list[dict] = []
    classifications: list[dict] = []
    if repo is not None:
        artifacts = [
            json.loads(m.model_dump_json()) for m in repo.list_artifacts(run_id=state.run_id)
        ]
        # The claims table holds both strategic claims and temporal change events.
        for m in repo.list_claims(run_id=state.run_id):
            payload = json.loads(m.model_dump_json())
            if m.__class__.__name__ == "ChangeEvent":
                change_events.append(payload)
            else:
                claims.append(payload)
        # The opportunities table holds both message–proof gaps and opportunities.
        for m in repo.list_opportunities(run_id=state.run_id):
            payload = json.loads(m.model_dump_json())
            if m.__class__.__name__ == "MessageProofGap":
                proof_gaps.append(payload)
            else:
                opportunities.append(payload)
        classifications = [
            json.loads(m.model_dump_json())
            for m in repo.list_classifications(state.run_id, family="merged")
        ]
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "run": {
            "run_id": state.run_id,
            "parent_run_id": state.parent_run_id,
            "mode": state.mode,
            "execution_mode": state.execution_mode,
            "generated_at": utcnow().isoformat(),
            "stop_reason": state.stop_reason,
            "iterations": state.iteration,
            "spent_usd": round(state.spent_usd, 4),
        },
        "scope": {
            "company_input": state.company_input,
            "compare_to": state.compare_to,
            "lookback_days": state.lookback_days,
            "user_focus": state.user_focus,
            "time_windows": [json.loads(w.model_dump_json()) for w in state.time_windows],
        },
        "companies": [
            json.loads(c.model_dump_json())
            for c in (state.company, state.focal_company)
            if c is not None
        ],
        "sources": [],
        "artifacts": artifacts,
        "evidence": [],
        "classifications": classifications,
        "claims": claims,
        "product_portfolios": [],
        "launches": [],
        "change_events": change_events,
        "matrices": [],
        "proof_gaps": proof_gaps,
        "opportunities": opportunities,
        "coverage": state.coverage,
        "limitations": state.limitations,
        "negative_observations": state.negative_observations,
        "tool_failures": [json.loads(f.model_dump_json()) for f in state.failed_actions.values()],
        "trace_summary": {"tool_calls": state.tool_calls_made},
        "eval_summary": {},
    }


def render_markdown(state: DirectorState, package: dict[str, Any]) -> str:
    company = state.company.canonical_name if state.company else state.company_input
    focal = state.focal_company.canonical_name if state.focal_company else "Rippling"
    exec_mode = state.execution_mode
    lines = [
        f"# Competitive Marketing Intelligence Brief — {company}",
        "",
        f"*Run `{state.run_id}` · mode `{state.mode}` · execution `{exec_mode}` · "
        f"generated {package['run']['generated_at']}*",
    ]
    if exec_mode == "fixture":
        lines += [
            "",
            "> **Fixture mode — synthetic, deterministic data.** This run exercises the "
            "full pipeline on labeled fixtures, not live public evidence. Run with "
            "`--execution-mode live` for a truthful competitor-vs-focal brief.",
        ]

    # --- Action Board ------------------------------------------------------
    lines += ["", "## Action Board", ""]
    changes = package["change_events"]
    gaps = package["proof_gaps"]
    opps = package["opportunities"]

    lines.append("### Largest strategy changes")
    if changes:
        for ch in changes[:3]:
            lines.append(
                f"- **{ch['dimension']}** ({ch['lifecycle']}, confidence {ch['confidence']}): "
                f"“{ch['prior_state']}” → “{ch['current_state']}”"
            )
    else:
        lines.append("- No temporal change met the both-periods evidence bar in this run.")

    lines += ["", "### Strongest message–proof gaps"]
    if gaps:
        for g in gaps[:3]:
            lines.append(
                f"- **{g['claim_text']}** — competitor proof: {g['proof_strength']}; "
                f"attackability: {g['attackability']}. {g['actionable_interpretation']}"
            )
    else:
        lines.append("- No repeated competitor claim with a proof gap was observed.")

    lines += ["", f"### {focal}-relative recommended actions"]
    if opps:
        for o in opps[:3]:
            lines += [
                f"- **{o['title']}** ({o['deliverable_type']}, defensibility {o['structural_defensibility']})",
                f"  - Angle: {o['message_angle']}",
                f"  - Focal proof status: {o['rippling_proof_status']}; already-saying-it: {o['rippling_current_usage']}",
                f"  - Why it could backfire: {o['why_this_could_backfire']}",
                f"  - Experiment: {o['experiment_hypothesis']} — kill rule: {o['kill_rule']}",
            ]
    else:
        lines.append("- No opportunity survived generation this run.")

    # --- Scope & coverage --------------------------------------------------
    lines += [
        "",
        "## Research scope and coverage",
        "",
        f"- Competitor: **{company}** · Focal comparison: **{focal}**",
        f"- Lookback: {state.lookback_days} days across {len(state.time_windows)} windows",
        f"- Artifacts collected: {len(package['artifacts'])} · classifications: "
        f"{len(package['classifications'])} · claims: {len(package['claims'])}",
        "",
        "| Dimension | Coverage |",
        "|---|---|",
    ]
    for dim, level in sorted(state.coverage.items()):
        if level != "not_attempted":
            lines.append(f"| {dim} | {level} |")

    # --- Current positioning (from classifications) ------------------------
    if package["classifications"]:
        lines += ["", "## Current public positioning", ""]
        primary = _dominant_message(package["classifications"])
        if primary:
            lines.append(f"- Dominant message: **{primary}**")
        villains = _collect(package["classifications"], "villain_normalized")
        if villains:
            lines.append(f"- Villains / status quo attacked: {', '.join(sorted(villains)[:5])}")
        stances = _collect_scalar(package["classifications"], "competitive_stance")
        if stances:
            lines.append(f"- Competitive stance observed: {', '.join(sorted(stances))}")

    # --- Limitations -------------------------------------------------------
    lines += ["", "## Limitations and missing data", ""]
    if state.limitations:
        lines += [f"- {item}" for item in state.limitations]
    else:
        lines.append("- No blocking limitations recorded.")
    lines += [
        "- Not publicly knowable and therefore excluded: actual ad performance/ROAS, "
        "exact spend, negotiated pricing, complete OOH/employee-post coverage, internal intent.",
    ]
    if state.negative_observations:
        lines += ["", "### Negative observations (searched, not found)", ""]
        lines += [f"- {item}" for item in state.negative_observations[:12]]

    lines += [
        "",
        "## Run trace",
        "",
        f"- Stop reason: `{state.stop_reason}` · iterations: {state.iteration} · "
        f"tool calls: {state.tool_calls_made}",
        "",
    ]
    return "\n".join(lines)


def _dominant_message(classifications: list[dict]) -> str | None:
    counts: dict[str, int] = {}
    for c in classifications:
        msg = c.get("primary_message")
        if msg:
            counts[msg] = counts.get(msg, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def _collect(classifications: list[dict], field: str) -> set[str]:
    out: set[str] = set()
    for c in classifications:
        for v in c.get(field, []) or []:
            out.add(str(v))
    return out


def _collect_scalar(classifications: list[dict], field: str) -> set[str]:
    return {str(c[field]) for c in classifications if c.get(field)}


def render_run_outputs(state: DirectorState, ctx: GraphContext) -> dict[str, Path]:
    out = run_output_dir(state, ctx)
    package = build_json_package(state, ctx)
    json_path = out / "data.json"
    json_path.write_text(json.dumps(package, indent=2, default=str), encoding="utf-8")
    md_path = out / "brief.md"
    md_path.write_text(render_markdown(state, package), encoding="utf-8")
    return {"json": json_path, "markdown": md_path, "dir": out}
