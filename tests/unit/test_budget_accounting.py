"""Model spend must count toward the research budget, not just tool costs —
otherwise the dollar cap is meaningless (the dominant cost is model calls)."""

from __future__ import annotations


def _state(**kw):
    from competitive_agent.state import DirectorState

    return DirectorState(run_id="RUN-x", company_input="deel.com", **kw)


def test_budget_counts_model_cost_not_only_tool_cost():
    st = _state(budget_usd=1.0, spent_usd=0.10, model_cost_usd=0.0)
    assert not st.budget_exhausted()
    # Tool cost alone is under budget, but model spend pushes total over.
    st.model_cost_usd = 0.95
    assert st.total_spend_usd() == 1.05
    assert st.budget_exhausted()


def test_gateway_exposes_total_cost_uniformly():
    from competitive_agent.config import get_settings
    from competitive_agent.model_gateway import FixtureGateway

    g = FixtureGateway(get_settings())
    assert g.total_cost_usd == 0.0  # fixtures are free but expose the interface


def test_fixture_run_records_zero_model_cost_but_field_present():
    """A fixture run completes and the package carries the cost breakdown."""
    import json
    import tempfile
    from pathlib import Path

    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    tmp = Path(tempfile.mkdtemp())
    object.__setattr__(settings, "db_path", tmp / "a.db")
    object.__setattr__(settings, "outputs_dir", tmp / "out")
    try:
        from competitive_agent.runner import run_analysis

        st = run_analysis("deel.com", mode="comparative", execution_mode="fixture", compare_to=None)
        pkg = json.loads((settings.outputs_dir / "runs" / st.run_id / "data.json").read_text())
        run = pkg["run"]
        assert "total_spend_usd" in run
        assert "model_cost_usd" in run
        assert "tool_cost_usd" in run
    finally:
        config_mod.reset_config_cache()
