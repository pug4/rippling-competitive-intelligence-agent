"""Phase 4 behavior: feedback persists, retry creates an isolated child run with
lineage + a difference report, and the follow-up router answers from state."""

from __future__ import annotations

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


def _run(company="example-hr.com"):
    from competitive_agent.runner import run_analysis

    return run_analysis(company, mode="comparative", execution_mode="fixture", compare_to="rippling.com")


def test_feedback_persists(isolated_env: Path):
    from competitive_agent.conversation import record_feedback

    state = _run()
    fid = record_feedback(state.run_id, feedback_type="thumbs_down", reason="too generic")
    assert fid.startswith("fb-")


def test_retry_creates_child_run_with_lineage_and_diff(isolated_env: Path):
    from competitive_agent import config as config_mod
    from competitive_agent.conversation import create_retry
    from competitive_agent.graph import load_state
    from competitive_agent.runner import _build_context
    from competitive_agent.storage.repository import Repository

    parent = _run()
    parent_artifacts_before = len(
        Repository.open(config_mod.get_settings().db_path).list_artifacts(run_id=parent.run_id)
    )
    assert parent_artifacts_before > 0
    diff = create_retry(parent.run_id, retry_mode="reanalyze_same_evidence")
    assert diff["child_run_id"] != parent.run_id
    assert diff["parent_run_id"] == parent.run_id
    # Retry must NEVER destroy the parent's evidence (regression guard: earlier
    # the child re-saved artifacts, reassigning the parent's rows to itself).
    parent_artifacts_after = len(
        Repository.open(config_mod.get_settings().db_path).list_artifacts(run_id=parent.run_id)
    )
    assert parent_artifacts_after == parent_artifacts_before
    # Child preserved the parent (parent still exists and is unchanged).
    ctx = _build_context(parent.run_id)
    assert load_state(ctx.repository, parent.run_id).run_id == parent.run_id
    # reanalyze reuses evidence: parent and child have the same artifact count.
    assert diff["artifacts"]["child"] == diff["artifacts"]["parent"]
    assert diff["artifacts"]["parent"] == parent_artifacts_before
    assert "final_difference_summary" in diff


def test_challenge_creates_challenge_mode_child(isolated_env: Path):
    from competitive_agent.conversation import create_retry

    parent = _run()
    diff = create_retry(parent.run_id, retry_mode="challenge_conclusion", target_id="CLM-x")
    assert diff["retry_mode"] == "challenge_conclusion"
    assert diff["child_run_id"]


def test_followup_router_answers_from_state(isolated_env: Path):
    from competitive_agent.conversation import answer_followup

    state = _run()
    assert answer_followup(state.run_id, "why do you believe this? show evidence")["route"] == "answer_from_state"
    assert answer_followup(state.run_id, "go deeper on pricing")["route"] == "focused_deep_dive"
    assert answer_followup(state.run_id, "challenge this conclusion")["route"] == "challenge"


def test_unknown_retry_mode_rejected(isolated_env: Path):
    from competitive_agent.conversation import create_retry

    parent = _run()
    with pytest.raises(ValueError):
        create_retry(parent.run_id, retry_mode="not_a_mode")
