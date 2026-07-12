"""Regression guards for two audit findings:
1. proof_gaps/opportunities were persisted append-only across breadth cycles,
   inflating the headline deliverables ~4x.
2. retry reuse-mode re-proposed reuse_evidence every iteration (dedup-key
   mismatch), re-classifying the same artifacts to the iteration cap.
"""

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


def test_gaps_and_opportunities_are_not_duplicated(isolated_env: Path):
    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    pkg = json.loads((get_settings().outputs_dir / "runs" / state.run_id / "data.json").read_text())
    gaps = pkg["proof_gaps"]
    opps = pkg["opportunities"]
    # Every persisted gap/opportunity is distinct (replace-semantics, not append).
    assert len(gaps) == len({g["claim_id"] for g in gaps})
    assert len(gaps) == len({g["short_label"] for g in gaps}), "duplicate proof gaps"
    assert len(opps) == len({o["opportunity_id"] for o in opps})
    assert len(opps) == len({o["title"] for o in opps}), "duplicate opportunities"
    # eval_summary counts must match the deduped reality.
    assert pkg["eval_summary"]["n_proof_gaps"] == len(gaps)


def test_retry_reuse_does_not_run_away(isolated_env: Path):
    import sqlite3

    from competitive_agent.config import get_settings
    from competitive_agent.conversation import create_retry
    from competitive_agent.runner import run_analysis

    parent = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    diff = create_retry(parent.run_id, retry_mode="reanalyze_same_evidence")
    child = diff["child_run_id"]

    db = sqlite3.connect(str(get_settings().db_path))
    sj = json.loads(
        db.execute("SELECT state_json FROM runs WHERE run_id=?", (child,)).fetchone()[0]
    )
    n_cls = db.execute("SELECT count(*) FROM classifications WHERE run_id=?", (child,)).fetchone()[
        0
    ]
    parent_cls = db.execute(
        "SELECT count(*) FROM classifications WHERE run_id=?", (parent.run_id,)
    ).fetchone()[0]

    # Reuse re-classifies the SAME evidence roughly ONCE, not dozens of times, and
    # stops cleanly (not by hitting the iteration cap).
    assert sj["stop_reason"] != "max_iterations_reached"
    assert sj["iteration"] <= 5, f"retry looped {sj['iteration']} iterations"
    assert n_cls <= parent_cls * 2, f"child re-classified {n_cls} vs parent {parent_cls}"
