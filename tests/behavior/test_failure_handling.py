"""Graceful failure handling (blueprint §37.33, §39.6): a source with no data —
or a disabled/unavailable one — is a typed, non-blocking finding, never a crash,
and the run still completes and renders. (Prompt-injection resistance lives in
tests/security/test_prompt_injection.py.)"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.ads import MetaAdsTool
from competitive_agent.tools.base import ToolContext


class _FakeRepo:
    def record_tool_call(self, **kwargs: Any) -> None: ...
    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


def _ctx(sources: dict[str, bool]) -> ToolContext:
    return ToolContext(
        run_id="RUN-test",
        company_id="c",
        mode="live",  # type: ignore[arg-type]
        config=AppConfig(
            focal_company=FocalCompanyConfig(), sources=sources, execution={}, budgets={},
            portfolio={}, windows={}, taxonomy={}, model_routes={}, source_capabilities={},
        ),
        settings=Settings(exa_api_key=""),
        repository=_FakeRepo(),
    )


def test_disabled_source_returns_typed_nonblocking_status():
    """A disabled source yields skipped_disabled + a negative observation, never
    raises, and produces no artifacts."""
    tool = MetaAdsTool()
    action = ResearchAction(action_id="ACT-1", action_type="search_meta_ads", company_id="c")
    result = asyncio.run(tool.execute(action, _ctx({"meta_ads": False})))
    assert result.status == "skipped_disabled"
    assert result.artifacts == []
    assert result.negative_observations  # the absence is recorded as a finding
    assert "meta_ads" in " ".join(result.negative_observations)


def test_missing_flag_fails_closed():
    """A source flag that isn't set at all is treated as disabled (fail-closed)."""
    tool = MetaAdsTool()
    action = ResearchAction(action_id="ACT-2", action_type="search_meta_ads", company_id="c")
    result = asyncio.run(tool.execute(action, _ctx({})))  # no meta_ads key
    assert result.status == "skipped_disabled"


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


def test_low_value_source_is_skipped_with_reason(isolated_env: Path):
    """The agent decides what to pursue vs. skip (take-home core): each action is
    chosen over scored alternatives it declined, and stopping is a reasoned
    decision — both recorded in the trace, not implicit."""
    import json

    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis

    state = run_analysis("deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com")
    trace = get_settings().outputs_dir / "runs" / state.run_id / "trace.jsonl"
    events = [json.loads(x) for x in trace.read_text().splitlines() if x.strip()]

    selected = [e for e in events if e.get("event_type") == "action_selected"]
    # At least one selection evaluated alternatives it did NOT pursue (skipped).
    assert any(e.get("payload", {}).get("alternatives_considered") for e in selected)
    # The loop terminates with an explicit, human-readable reason.
    stops = [e for e in events if e.get("event_type") == "stop_selected"]
    assert stops and stops[-1]["payload"].get("reason")


def test_run_completes_despite_disabled_sources(isolated_env: Path):
    """meta_ads / linkedin_ads are off by default; a full comparative run still
    completes and renders all three outputs."""
    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis

    state = run_analysis("deel.com", mode="comparative", execution_mode="fixture", compare_to=None)
    assert state.stop_reason is not None  # terminated cleanly, not crashed
    out = get_settings().outputs_dir / "runs" / state.run_id
    for f in ("brief.md", "data.json", "trace.jsonl"):
        assert (out / f).exists(), f"missing {f}"
