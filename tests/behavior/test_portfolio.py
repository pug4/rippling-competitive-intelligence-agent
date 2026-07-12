"""Phase 5 gate: a multi-competitor portfolio run completes with fully isolated
per-company pipelines and no cross-company evidence leakage (blueprint §38.16)."""

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


def test_three_competitor_portfolio_completes_without_leakage(isolated_env: Path):
    from competitive_agent.portfolio import run_portfolio

    result = run_portfolio(
        ["deel.com", "gusto.com", "workday.com"],
        mode="comparative",
        execution_mode="fixture",
        compare_to="rippling.com",
    )
    assert result["status"] in ("completed", "completed_with_limitations")
    assert result["isolation_verified"] is True
    assert result["isolation_report"]["violations"] == []
    # Every company ran in its own pipeline with a distinct run_id.
    run_ids = [p["company_run_id"] for p in result["packages"]]
    assert len(run_ids) == len(set(run_ids)) == 3
    # Each package's evidence carries only that company's company_id.
    for p in result["packages"]:
        foreign = [c for c in p["artifact_company_ids"] if c != p["company_id"]]
        assert foreign == [], f"{p['company_input']} leaked evidence from {foreign}"


def test_leakage_detector_flags_contamination(isolated_env: Path):
    """The detector must FAIL when a package references another company's id
    (guards the guard: a no-op checker would pass the happy path silently)."""
    from competitive_agent.portfolio import assert_no_cross_company_leakage

    packages = [
        {"company_id": "co-a", "company_input": "a.com", "artifact_company_ids": ["co-a"]},
        {"company_id": "co-b", "company_input": "b.com", "artifact_company_ids": ["co-b", "co-a"]},
    ]
    report = assert_no_cross_company_leakage(packages)
    assert report["ok"] is False
    assert any("b.com" in v for v in report["violations"])


def test_per_company_quality_gates_recorded(isolated_env: Path):
    from competitive_agent.portfolio import run_portfolio

    result = run_portfolio(
        ["deel.com", "gusto.com"],
        mode="comparative",
        execution_mode="fixture",
        compare_to="rippling.com",
    )
    for p in result["packages"]:
        assert "quality_gate_results" in p
        assert p["quality_gate_results"]["single_company_evidence"] is True
        assert p["counts"]["artifacts"] > 0


def test_over_cap_companies_are_declared_not_silently_dropped(isolated_env: Path):
    from competitive_agent.portfolio import run_portfolio

    # max_competitors_per_demo defaults to 3; a 4th must surface in skipped +
    # limitations, never vanish.
    result = run_portfolio(
        ["deel.com", "gusto.com", "workday.com", "bamboohr.com"],
        mode="comparative",
        execution_mode="fixture",
        compare_to="rippling.com",
    )
    assert result["skipped_companies"] == ["bamboohr.com"]
    assert any("bamboohr.com" in lim for lim in result["limitations"])
