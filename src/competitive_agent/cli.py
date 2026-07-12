"""Typer CLI — every command exits non-zero on terminal failure (§37.32)."""

from __future__ import annotations

import json
import sys

import typer

app = typer.Typer(name="competitive-agent", no_args_is_help=True, add_completion=False)

MODE_OPT = typer.Option(
    "snapshot", "--mode", help="snapshot|longitudinal|comparative|focused_deep_dive|challenge|retry"
)
EXEC_OPT = typer.Option(None, "--execution-mode", help="live|cached|fixture (default from .env)")


@app.command()
def analyze(
    company: str = typer.Argument(..., help="Competitor name or public domain"),
    mode: str = MODE_OPT,
    execution_mode: str = EXEC_OPT,
    compare: str = typer.Option(
        None, "--compare", help="Focal company domain (default: config focal_company)"
    ),
    include_longitudinal: bool = typer.Option(False, "--include-longitudinal"),
    lookback_days: int = typer.Option(None, "--lookback-days"),
    focus: list[str] = typer.Option(None, "--focus", help="Repeatable focus dimension"),
) -> None:
    """Analyze one company's public marketing strategy."""
    from .runner import run_analysis

    if include_longitudinal and mode == "snapshot":
        mode = "longitudinal"
    try:
        state = run_analysis(
            company,
            mode=mode,
            execution_mode=execution_mode,
            compare_to=compare,
            lookback_days=lookback_days,
            user_focus=list(focus or []),
        )
    except Exception as exc:
        typer.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"run_id: {state.run_id}")
    typer.echo(f"stop_reason: {state.stop_reason}")
    if state.pending_user_question:
        typer.echo(f"question: {state.pending_user_question}")
    typer.echo(f"outputs: outputs/runs/{state.run_id}/")


@app.command()
def resume(run_id: str = typer.Argument(...)) -> None:
    """Resume an interrupted or awaiting run from its last checkpoint."""
    from .runner import resume_run

    try:
        state = resume_run(run_id)
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"run_id: {state.run_id} stop_reason: {state.stop_reason}")


@app.command()
def render(run_id: str = typer.Argument(...)) -> None:
    """Re-render Markdown/JSON for a stored run from persisted state."""
    from .graph import load_state
    from .report import render_run_outputs
    from .runner import _build_context

    ctx = _build_context(run_id)
    try:
        state = load_state(ctx.repository, run_id)
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    paths = render_run_outputs(state, ctx)
    typer.echo(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))


@app.command("demo-check")
def demo_check(mode: str = typer.Option("fixture", "--mode", help="fixture|cached")) -> None:
    """Deterministic end-to-end check: initialize → persist → trace → render."""
    from .runner import run_analysis

    if mode not in ("fixture", "cached"):
        typer.echo("error: demo-check supports fixture|cached", err=True)
        raise typer.Exit(code=2)
    state = run_analysis("deel.com", mode="comparative", execution_mode=mode, compare_to=None)
    problems = []
    if not state.is_complete and not state.pending_user_question:
        problems.append("run did not complete")
    from .config import get_settings

    out = get_settings().outputs_dir / "runs" / state.run_id
    for required in ("brief.md", "data.json", "trace.jsonl"):
        if not (out / required).exists():
            problems.append(f"missing output: {required}")
    if problems:
        typer.echo("DEMO CHECK FAILED: " + "; ".join(problems), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"DEMO CHECK OK ({mode}) run_id={state.run_id} stop_reason={state.stop_reason}")


@app.command()
def eval(
    suite: str = typer.Option("contracts", "--suite", help="contracts|behavior|regression|all"),
) -> None:
    """Run evaluation suites."""
    import subprocess

    suites = {
        "contracts": ["tests/contract", "tests/unit"],
        "behavior": ["tests/behavior"],
        "regression": ["tests/golden"],
    }
    targets = suites.get(suite) if suite != "all" else [p for ps in suites.values() for p in ps]
    if targets is None:
        typer.echo(f"error: unknown suite '{suite}'", err=True)
        raise typer.Exit(code=2)
    existing = [t for t in targets if __import__("pathlib").Path(t).exists()]
    if not existing:
        typer.echo("no eval targets exist yet", err=True)
        raise typer.Exit(code=1)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *existing], check=False)
    raise typer.Exit(code=result.returncode)


def _run_retry(run_id: str, *, mode: str, target: str | None, reason: str | None, focus: list[str] | None) -> None:
    from .config import get_settings
    from .conversation import create_retry, write_diff_report

    try:
        diff = create_retry(run_id, retry_mode=mode, target_id=target, user_reason=reason, focus=focus)
    except (KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = write_diff_report(diff, get_settings().outputs_dir / "runs" / diff["child_run_id"])
    typer.echo(f"child_run_id: {diff['child_run_id']} (parent {run_id})")
    typer.echo(diff["final_difference_summary"])
    if diff["new_opportunities"]:
        typer.echo("new opportunities: " + "; ".join(diff["new_opportunities"]))
    typer.echo(f"diff report: {path}")


@app.command()
def deepen(run_id: str = typer.Argument(...), focus: list[str] = typer.Option(..., "--focus")) -> None:
    """Focused deep dive on an existing run, reusing prior evidence."""
    _run_retry(run_id, mode="collect_deeper_evidence", target=None, reason=None, focus=list(focus))


@app.command()
def challenge(run_id: str = typer.Argument(...), claim: str = typer.Option(None, "--claim")) -> None:
    """Challenge a conclusion with counterevidence (child run preserves the parent)."""
    _run_retry(run_id, mode="challenge_conclusion", target=claim, reason="challenge the conclusion", focus=None)


@app.command()
def feedback(
    run_id: str = typer.Argument(...),
    target: str = typer.Option(None, "--target"),
    thumbs_up: bool = typer.Option(False, "--thumbs-up"),
    thumbs_down: bool = typer.Option(False, "--thumbs-down"),
    reason: str = typer.Option(None, "--reason"),
) -> None:
    """Record feedback on a run or insight (persists; does not retrain)."""
    from .conversation import record_feedback

    ftype = "thumbs_up" if thumbs_up else "thumbs_down" if thumbs_down else "retry"
    target_type = "opportunity" if target and target.startswith("OPP") else "report"
    try:
        fid = record_feedback(
            run_id, target_type=target_type, target_id=target, feedback_type=ftype, reason=reason
        )
    except Exception as exc:
        typer.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"feedback recorded: {fid} ({ftype})")
    if thumbs_down:
        typer.echo("Tip: `competitive-agent retry " + run_id + " --mode <reanalyze_same_evidence|"
                   "collect_deeper_evidence|challenge_conclusion>` to create a child run.")


@app.command()
def retry(
    run_id: str = typer.Argument(...),
    target: str = typer.Option(None, "--target"),
    mode: str = typer.Option("collect_deeper_evidence", "--mode"),
    reason: str = typer.Option(None, "--reason"),
) -> None:
    """Create a child retry run preserving lineage + a difference report."""
    _run_retry(run_id, mode=mode, target=target, reason=reason, focus=None)


@app.command()
def ask(run_id: str = typer.Argument(...), question: str = typer.Argument(...)) -> None:
    """Answer a follow-up from stored state, or route to a focused action."""
    import json as _json

    from .conversation import answer_followup

    try:
        typer.echo(_json.dumps(answer_followup(run_id, question), indent=2, default=str))
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
