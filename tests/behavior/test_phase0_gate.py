"""Phase 0 gate: a clean fixture run initializes, persists state, writes trace
events, and renders a valid output package (blueprint Task group A)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


def test_fixture_run_completes_and_renders(isolated_env: Path):
    from competitive_agent.runner import run_analysis

    state = run_analysis("deel.com", mode="comparative", execution_mode="fixture")

    assert state.is_complete, "fixture run must reach a terminal state"
    assert state.stop_reason, "stop reason must be recorded (§37.17)"
    assert state.company is not None and state.company.canonical_name == "Deel"
    assert state.focal_company is not None and state.focal_company.canonical_name == "Rippling"
    assert len(state.time_windows) == 2

    out = isolated_env / "outputs" / "runs" / state.run_id
    brief = out / "brief.md"
    data = out / "data.json"
    trace = out / "trace.jsonl"
    assert brief.exists() and data.exists() and trace.exists()

    package = json.loads(data.read_text())
    for key in (
        "schema_version",
        "run",
        "scope",
        "companies",
        "artifacts",
        "claims",
        "opportunities",
        "coverage",
        "limitations",
        "tool_failures",
    ):
        assert key in package, f"JSON package missing required top-level key {key}"

    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    event_types = {e["event_type"] for e in events}
    for required in (
        "run_started",
        "company_resolved",
        "coverage_assessed",
        "stop_selected",
        "run_completed",
    ):
        assert required in event_types, f"trace missing {required}"


def test_run_is_resumable_from_checkpoint(isolated_env: Path):
    import asyncio

    from competitive_agent.graph import load_state
    from competitive_agent.runner import create_run, drive

    state, ctx = create_run("gusto.com", mode="snapshot", execution_mode="fixture")
    final = asyncio.run(drive(state, ctx))

    reloaded = load_state(ctx.repository, final.run_id)
    assert reloaded.run_id == final.run_id
    assert reloaded.is_complete
    assert reloaded.coverage == final.coverage


def test_unresolvable_company_asks_user(isolated_env: Path):
    from competitive_agent.runner import run_analysis

    state = run_analysis("zzz nonexistent corp", mode="snapshot", execution_mode="fixture")
    assert state.pending_user_question, (
        "ambiguous identity must surface a question (§37.33 scenario 3)"
    )
    assert not state.is_complete


def test_assignment_answers_ship_in_brief_and_json(isolated_env: Path):
    """The assignment's four questions are answered in BOTH deliverables: the
    brief carries the cited section between exec summary and Action Board, and
    data.json ships the structured mirror with per-row citations."""
    import re

    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    out = isolated_env / "outputs" / "runs" / state.run_id

    md = (out / "brief.md").read_text()
    heading = "## The assignment deliverable — four questions, answered from the evidence"
    assert md.index("## Executive summary") < md.index(heading) < md.index("\n## Action Board\n")
    section = md[md.index(heading) : md.index("\n## Action Board\n")]
    for sub in (
        "### 1. What messaging angles and themes are they running?",
        "### 2. How do they position their product(s)?",
        "### 3. What's changed recently (new campaigns, new ICPs targeted, messaging pivots)?",
        "### 4. What gaps does this surface for Rippling — and what we'd exploit",
    ):
        assert sub in section, f"missing subsection: {sub}"
    citation_links = re.findall(r"\[[^\]]+\]\(https?://[^)]+\)", section)
    assert len(citation_links) >= 10, f"only {len(citation_links)} citation links in the section"

    aa = json.loads((out / "data.json").read_text())["assignment_answers"]
    for key in (
        "q1_messaging_themes",
        "q2_product_positioning",
        "q3_recent_changes",
        "q4_gaps_and_opportunities",
    ):
        assert key in aa, f"assignment_answers missing {key}"
    assert any(t["citations"] for t in aa["q1_messaging_themes"]["themes"])
    assert aa["q2_product_positioning"]["dominant_message"]["citations"]
    q4 = aa["q4_gaps_and_opportunities"]
    assert any(r["citations"] for r in q4["message_proof_gaps"] + q4["campaign_plays"])
