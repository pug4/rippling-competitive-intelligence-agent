"""Golden / regression guard (blueprint §37.33, §40.10): the deterministic
fixture run must keep its output CONTRACT stable — every prescribed top-level
JSON key present and every prescribed brief section rendered. This is what
`competitive-agent eval --suite regression` runs; without it a green
`make quality` would overstate regression protection.

It asserts STRUCTURE (keys/sections), not free-text values, so it is stable
across model/prompt changes while still catching a dropped section or key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# The JSON package's prescribed top-level keys (blueprint §39.10 output contract).
REQUIRED_JSON_KEYS = {
    "schema_version",
    "run",
    "scope",
    "companies",
    "dominant_message",
    "source_distribution",
    "corpus_skew_warnings",
    "coverage",
    "coverage_detail",
    "commercial_motion",
    "product_positioning",
    "category_entry_points",
    "persona_channel_matrix",
    "sources",
    "artifacts",
    "unclassified_artifacts",
    "evidence",
    "focal_evidence",
    "classifications",
    "claims",
    "change_events",
    "proof_gaps",
    "opportunities",
    "limitations",
    "negative_observations",
}

# Brief sections that must always render (an empty one collapses to a one-liner
# but the heading must be present).
REQUIRED_BRIEF_SECTIONS = [
    "## Executive summary",
    "## Action Board",
    "## Current public positioning",
    "## Strategy over time",
    "## Research scope and coverage",
    "## Limitations and missing data",
    "## Evidence appendix",
]


@pytest.fixture()
def fixture_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    from competitive_agent.runner import run_analysis

    state = run_analysis("deel.com", mode="comparative", execution_mode="fixture", compare_to=None)
    out = settings.outputs_dir / "runs" / state.run_id
    yield out
    config_mod.reset_config_cache()


def test_json_package_has_all_contract_keys(fixture_run: Path):
    pkg = json.loads((fixture_run / "data.json").read_text())
    missing = REQUIRED_JSON_KEYS - set(pkg.keys())
    assert not missing, f"JSON package is missing contract keys: {sorted(missing)}"


def test_brief_renders_all_required_sections(fixture_run: Path):
    brief = (fixture_run / "brief.md").read_text()
    missing = [s for s in REQUIRED_BRIEF_SECTIONS if s not in brief]
    assert not missing, f"brief is missing required sections: {missing}"


def test_trace_and_outputs_exist(fixture_run: Path):
    for f in ("brief.md", "data.json", "trace.jsonl"):
        assert (fixture_run / f).exists()
    # Trace is JSONL with an agentic loop (coverage assessment + action selection).
    events = [
        json.loads(x) for x in (fixture_run / "trace.jsonl").read_text().splitlines() if x.strip()
    ]
    types = {e.get("event_type") for e in events}
    assert "coverage_assessed" in types
    assert "action_selected" in types
    assert "stop_selected" in types
