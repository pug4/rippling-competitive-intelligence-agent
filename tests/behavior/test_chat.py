"""Grounded per-run analysis chatbot (assignment: 'Be conversational')."""

from __future__ import annotations

import asyncio
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


def test_build_context_digests_the_findings():
    from competitive_agent.chat import build_context

    pkg = {
        "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
        "dominant_message": {"label": "All-in-one platform", "theme": "consolidation"},
        "eval_summary": {"n_artifacts": 50, "n_proof_gaps": 5, "n_opportunities": 3},
        "proof_gaps": [
            {"short_label": "compliance", "attackability": "medium", "proof_strength": "weak"}
        ],
        "opportunities": [{"title": "Workflow demo", "message_angle": "show the flow"}],
        "linkedin_posts": [{"author": "Dana", "theme": "consolidation", "excerpt": "one platform"}],
    }
    ctx = build_context(pkg)
    assert "Deel" in ctx and "Rippling" in ctx
    assert "consolidation" in ctx and "compliance" in ctx
    assert "Workflow demo" in ctx and "Dana" in ctx


def test_chat_answers_from_run_and_suggests_followups(isolated_env: Path):
    from competitive_agent.chat import chat_about_run
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    res = asyncio.run(
        chat_about_run(state.run_id, "What's their most attackable gap?", execution_mode="fixture")
    )
    assert res["answer"]
    assert isinstance(res["suggested_followups"], list) and len(res["suggested_followups"]) >= 1
    assert res["confidence"] in ("high", "medium", "low")


def test_chat_unknown_run_raises(isolated_env: Path):
    from competitive_agent.chat import chat_about_run

    with pytest.raises(KeyError):
        asyncio.run(chat_about_run("RUN-nope", "hi", execution_mode="fixture"))


def test_chat_api_endpoint(isolated_env: Path):
    from fastapi.testclient import TestClient

    from competitive_agent.api import app
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    client = TestClient(app)
    # missing question -> 400
    assert client.post(f"/api/runs/{state.run_id}/chat", json={"question": ""}).status_code == 400
    # unknown run -> 404
    assert client.post("/api/runs/RUN-x/chat", json={"question": "hi"}).status_code == 404
    # real question (fixture) -> grounded answer + followups
    r = client.post(
        f"/api/runs/{state.run_id}/chat",
        json={"question": "What changed recently?", "execution_mode": "fixture"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] and "suggested_followups" in body
