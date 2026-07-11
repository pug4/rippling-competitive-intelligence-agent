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
    opportunities: list[dict] = []
    if repo is not None:
        artifacts = [
            json.loads(m.model_dump_json()) for m in repo.list_artifacts(run_id=state.run_id)
        ]
        claims = [json.loads(m.model_dump_json()) for m in repo.list_claims(run_id=state.run_id)]
        opportunities = [
            json.loads(m.model_dump_json()) for m in repo.list_opportunities(run_id=state.run_id)
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
        "classifications": [],
        "claims": claims,
        "product_portfolios": [],
        "launches": [],
        "change_events": [],
        "matrices": [],
        "proof_gaps": [],
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
    lines = [
        f"# Competitive Marketing Intelligence Brief — {company}",
        "",
        f"*Run `{state.run_id}` · mode `{state.mode}` · execution `{state.execution_mode}` · "
        f"generated {package['run']['generated_at']}*",
        "",
        "## Action Board",
        "",
        "_No verified findings yet — this run exercised the structural pipeline only._"
        if not package["opportunities"]
        else "",
        "",
        "## Research scope and coverage",
        "",
        f"- Competitor: **{company}**",
        f"- Focal comparison: **{focal}**",
        f"- Lookback: {state.lookback_days} days across {len(state.time_windows)} windows",
        "",
        "| Dimension | Coverage |",
        "|---|---|",
    ]
    for dim, level in sorted(state.coverage.items()):
        lines.append(f"| {dim} | {level} |")
    lines += [
        "",
        "## Limitations and missing data",
        "",
    ]
    if state.limitations:
        lines += [f"- {item}" for item in state.limitations]
    else:
        lines.append("- Structural run: no research collection was attempted yet.")
    if state.negative_observations:
        lines += ["", "### Negative observations", ""]
        lines += [f"- {item}" for item in state.negative_observations]
    lines += [
        "",
        "## Run trace",
        "",
        f"- Stop reason: `{state.stop_reason}`",
        f"- Iterations: {state.iteration}",
        f"- Tool calls: {state.tool_calls_made}",
        "",
    ]
    return "\n".join(lines)


def render_run_outputs(state: DirectorState, ctx: GraphContext) -> dict[str, Path]:
    out = run_output_dir(state, ctx)
    package = build_json_package(state, ctx)
    json_path = out / "data.json"
    json_path.write_text(json.dumps(package, indent=2, default=str), encoding="utf-8")
    md_path = out / "brief.md"
    md_path.write_text(render_markdown(state, package), encoding="utf-8")
    return {"json": json_path, "markdown": md_path, "dir": out}
