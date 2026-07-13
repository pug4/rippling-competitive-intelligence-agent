"""Crashed runs must be reported truthfully — everywhere, surviving restarts.

Regression guard for the morning crash (P0): ``run_focal_mirror_check`` /
``render_outputs`` died mid-run, yet the DB row read ``status='complete'`` — a
crashed run masqueraded as a finished one on /live and could vanish from the run
list. The contract enforced here:

* a node that raises mid-run marks the DB row ``status='failed'`` with the crash
  ``current_node`` preserved, and the honest ``node_error:...`` stop_reason is
  checkpointed into ``state_json`` (so a server restart / reload cannot lose it);
* ``status='complete'`` is reserved for a run that genuinely reached the terminal
  ``render_outputs_done`` node;
* /live and GET /api/runs report a crashed run as failed — a failed run WITHOUT a
  report shows in the in-flight section with ``live_status='failed'``, and one
  WITH a salvaged report is listed like a completed run but flagged ``failed``;
* the deterministic error-path render still produces a brief carrying the honest
  stop_reason, and rendering that brief never launders the failure back to
  ``complete``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    # Env keys PRESENT-BUT-EMPTY (never delenv): hermetic, no live providers.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    monkeypatch.setenv("SEMRUSH_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


def _run_row(run_id: str) -> sqlite3.Row:
    """The run's DB row via a FRESH Repository handle — the durable record a
    server restart would see (unlike the in-memory _JOBS 'error' status)."""
    from competitive_agent.config import get_settings
    from competitive_agent.storage.repository import Repository

    row = Repository.open(get_settings().db_path).get_run(run_id)
    assert row is not None, f"run row missing: {run_id}"
    return row


def _drive_to_crash(crash_node: str = "run_focal_mirror_check"):
    """Drive a real fixture run whose ``crash_node`` is monkeypatched to raise,
    reproducing a mid-run node failure. Returns the crashed (state, ctx)."""
    from competitive_agent import nodes as nodes_mod
    from competitive_agent.graph import Graph
    from competitive_agent.runner import create_run

    state, ctx = create_run(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to=None
    )

    async def _boom(_state, _ctx):
        raise KeyError("simulated mid-run failure")

    nodes = nodes_mod.build_default_nodes()
    nodes[crash_node] = _boom
    graph = Graph(nodes)
    with pytest.raises(KeyError):
        asyncio.run(graph.run(state, ctx))
    return state, ctx


def test_node_crash_is_recorded_failed_and_reported_everywhere(isolated_env: Path):
    from competitive_agent.api import get_live, list_runs

    state, _ = _drive_to_crash()
    run_id = state.run_id

    # 1) The DB row is TRUTHFUL: failed, not complete; the crash node preserved.
    row = _run_row(run_id)
    assert row["status"] == "failed"
    assert row["current_node"] == "run_focal_mirror_check"
    # The honest stop_reason is checkpointed into state_json (survives reload).
    reloaded_reason = row["state_json"] and json.loads(row["state_json"]).get("stop_reason")
    assert reloaded_reason == "node_error:run_focal_mirror_check:KeyError"

    # 2) /live reports the crashed run as failed (not a clean completion).
    snap = get_live(run_id)
    assert snap["status"] == "failed"
    assert snap["current_node"] == "run_focal_mirror_check"

    # 3) GET /api/runs still surfaces it — a crashed run without a report must
    #    never vanish; it lands in the in-flight section as live_status='failed'.
    listed = {r["run_id"]: r for r in list_runs()}
    assert run_id in listed, "a crashed run must never disappear from the run list"
    entry = listed[run_id]
    assert entry["in_progress"] is True
    assert entry["live_status"] == "failed"
    assert entry["stop_reason"] == "node_error:run_focal_mirror_check:KeyError"


def test_error_path_render_brief_is_honest_and_stays_failed(isolated_env: Path):
    from competitive_agent.api import list_runs
    from competitive_agent.config import get_settings
    from competitive_agent.graph import load_state
    from competitive_agent.report import render_run_outputs

    state, ctx = _drive_to_crash()
    run_id = state.run_id

    # The error-path render (the same render_run_outputs the `render` CLI runs)
    # produces an HONEST brief for a crashed run. A brief with the node_error
    # stop_reason is GOOD — and rendering it must NOT flip the DB back to
    # 'complete': a failed run may legitimately carry a salvaged report.
    render_run_outputs(load_state(ctx.repository, run_id), ctx)

    run_dir = get_settings().outputs_dir / "runs" / run_id
    assert (run_dir / "brief.md").exists()
    pkg = json.loads((run_dir / "data.json").read_text())
    assert pkg["run"]["stop_reason"] == "node_error:run_focal_mirror_check:KeyError"
    assert _run_row(run_id)["status"] == "failed"

    # GET /api/runs now lists it with the salvaged report AND the failed flag,
    # so the UI never presents a crashed run as a clean completion.
    entry = {r["run_id"]: r for r in list_runs()}[run_id]
    assert entry.get("failed") is True
    assert entry["stop_reason"] == "node_error:run_focal_mirror_check:KeyError"


def test_genuine_completion_still_marks_complete(isolated_env: Path):
    """Guard the happy path: a run that genuinely reaches render_outputs_done is
    still recorded 'complete'. The fix narrows 'complete' to that terminal node;
    it does not withhold it from a clean run."""
    from competitive_agent.runner import run_analysis

    state = run_analysis("deel.com", mode="comparative", execution_mode="fixture", compare_to=None)
    row = _run_row(state.run_id)
    assert row["status"] == "complete"
    assert row["current_node"] == "render_outputs_done"


def test_persist_run_failure_records_and_never_clobbers_terminal(isolated_env: Path):
    """The job-thread failure hook records a run 'failed' in the DB (durable,
    survives restart) for failures OUTSIDE the graph's own checkpoint, but never
    launders a genuinely terminal run."""
    from competitive_agent.api import _persist_run_failure
    from competitive_agent.config import get_settings
    from competitive_agent.storage.repository import Repository

    repo = Repository.open(get_settings().db_path)

    # A run that died outside the graph loop (still 'running', no checkpoint).
    repo.create_run(run_id="RUN-outofgraph", company="X", mode="comparative", status="running")
    _persist_run_failure("RUN-outofgraph")
    assert _run_row("RUN-outofgraph")["status"] == "failed"

    # A genuinely complete run is never rewritten to failed.
    repo.create_run(run_id="RUN-donecheck", company="X", mode="comparative", status="complete")
    _persist_run_failure("RUN-donecheck")
    assert _run_row("RUN-donecheck")["status"] == "complete"

    # No run id / unknown run id is a silent, non-raising no-op.
    _persist_run_failure(None)
    _persist_run_failure("RUN-does-not-exist")
