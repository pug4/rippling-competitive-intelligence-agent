"""Wiring guards for batch 2b: NewsTool (adversarial-context news), industry
adaptivity, and the focal-claims (false-premise) gate.

Two levels:
1. A fixture COMPARATIVE run end to end proves the modules are actually wired
   into the pipeline — the package carries market_context (>=1 dated item from
   the fixture news sweep) and industry_context (a non-null industry from the
   fixture), and the brief renders the Market-context + Industry-lens sections.
2. A crafted, repository-backed unit test drives the nodes focal-gate step
   directly (no full run): an opportunity resting on a FALSE premise ("Rippling
   has no goals product") is DROPPED because the focal corpus shows Rippling HAS
   Goals, a clean opportunity SURVIVES, the drop is recorded on
   state.focal_gate_findings, and the withdrawal is disclosed in the run's
   limitations.

Hermetic: fixture execution mode (zero keys, deterministic), an isolated tmp
db/outputs, and no network anywhere. Env keys are emptied via setenv (never
delenv) so the process still sees the variable, just blank.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from competitive_agent.graph import GraphContext
from competitive_agent.nodes import critique_opportunities
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.company import Company
from competitive_agent.schemas.opportunity import MarketingOpportunity
from competitive_agent.state import DirectorState

# ---------------------------------------------------------------------------
# 1. Fixture comparative run -> market_context + industry_context are wired
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    # Empty (never removed) so fixture mode runs keyless and deterministic.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


def test_fixture_run_carries_market_context_and_industry_lens(isolated_env: Path) -> None:
    from competitive_agent.config import get_settings
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    run_dir = get_settings().outputs_dir / "runs" / state.run_id
    pkg = json.loads((run_dir / "data.json").read_text())
    brief = (run_dir / "brief.md").read_text()

    # market_context: the news sweep landed (the fixture serves 3 dated items).
    market = pkg["market_context"]
    assert market["total"] >= 1
    assert sum(market["counts"].values()) == market["total"]
    for item in market["items"]:
        assert set(item) == {"category", "title", "url", "published_at"}
    # The news_market artifacts actually made it into the corpus + coverage.
    assert pkg["source_distribution"].get("news_market", 0) >= 1
    assert pkg["coverage"].get("market_context") in ("low", "medium", "high")

    # industry_context: a non-null inferred industry from the fixture.
    industry = pkg["industry_context"]
    assert industry is not None
    assert industry.get("industry")  # non-empty, non-null
    assert state.industry_context is not None
    assert industry["industry"] == state.industry_context["industry"]

    # focal_gate_findings is exposed (the gate ran; empty is honest here).
    assert "focal_gate_findings" in pkg

    # Both brief sections render.
    assert state.company is not None
    assert "## Industry lens" in brief
    assert f"## Market context — what is happening TO {state.company.canonical_name}" in brief
    # The industry terminology adapts the lens (not a generic HR default).
    assert industry["industry"] in brief


# ---------------------------------------------------------------------------
# 2. Crafted focal-gate step: a false-premise opportunity is DROPPED
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _opportunity(opp_id: str, title: str, backfire: str) -> MarketingOpportunity:
    """A fully-valid MarketingOpportunity; only the fields the focal-claims gate
    scans (title + why_this_could_backfire) carry the crafted assertion."""
    return MarketingOpportunity(
        opportunity_id=opp_id,
        title=title,
        opportunity_type="comparative",
        competitor_pattern="competitor leads with a category story",
        change_over_time="stable",
        category_entry_point=None,
        commercial_motion_context="product-led",
        target_segment="mid-market",
        focal_product_focus=["platform"],
        focal_segment="mid-market",
        funnel_insertion_point="consideration",
        message_angle="lead with named customer proof",
        villain_or_status_quo="tool sprawl",
        promised_transformation="one platform",
        competitor_proof_strength="moderate",
        competitor_proof_gap="thin named-customer proof",
        focal_proof_ids=[],
        focal_proof_status="observed",
        focal_current_usage="in-market",
        structural_defensibility="medium",
        copyability="medium",
        why_competitor_cannot_easily_copy="platform breadth",
        why_this_could_backfire=backfire,
        comparative_claim_risk="low",
        legal_review_required=False,
        deliverable_type="comparison_page",
        experiment_hypothesis="a comparison page lifts consideration",
        primary_metric="qualified pipeline",
        minimum_sample_rule="n>=100",
        scale_rule="scale at CPL<target",
        iterate_rule="iterate weekly",
        kill_rule="kill at CPL>2x target",
    )


def _rippling() -> Company:
    return Company(
        company_id="rippling",
        canonical_name="Rippling",
        primary_domain="rippling.com",
        resolved_at=_NOW,
        resolution_confidence="high",
    )


def _goals_artifact() -> RawArtifact:
    """A focal-corpus page proving Rippling HAS a Goals product."""
    return RawArtifact(
        artifact_id="ART-focal-goals",
        company_id="rippling",
        source_type="webpage",
        source_name="webpage_fetch",
        url="https://www.rippling.com/goals",
        final_url="https://www.rippling.com/goals",
        title="Rippling Goals",
        retrieved_at=_NOW,
        content_hash="goalshash",
        collection_method="webpage",
    )


async def test_focal_gate_drops_false_premise_opportunity(tmp_path: Path) -> None:
    from competitive_agent.storage.repository import Repository

    repo = Repository.open(tmp_path / "gate.db")
    comp_run = "RUN-competitor"
    focal_run = "RUN-focal"

    # A false-premise play (Rippling "has no goals product") + a clean play.
    false_opp = _opportunity(
        "OPP-false",
        "Attack the missing goals story",
        "Rippling has no goals product, so this angle is safe.",
    )
    clean_opp = _opportunity(
        "OPP-clean",
        "Own the named-customer comparison",
        "Risk is low if we lead with verifiable named-customer proof.",
    )
    repo.save_opportunity(comp_run, false_opp)
    repo.save_opportunity(comp_run, clean_opp)
    # Focal corpus: a Rippling Goals page contradicts the false premise.
    repo.save_artifact(focal_run, _goals_artifact())

    state = DirectorState(
        run_id=comp_run,
        company_input="deel.com",
        mode="comparative",
        company=Company(
            company_id="deel",
            canonical_name="Deel",
            primary_domain="deel.com",
            resolved_at=_NOW,
            resolution_confidence="high",
        ),
        focal_company=_rippling(),
        focal_run_id=focal_run,
        opportunity_ids=["OPP-false", "OPP-clean"],
    )
    ctx = GraphContext(repository=repo, trace=None, config=None, settings=None)

    _state, nxt = await critique_opportunities(state, ctx)
    assert nxt == "decide_continue_or_stop"

    # The gate recorded a contradicted verdict for the false-premise opportunity.
    contradicted = [f for f in state.focal_gate_findings if f["verdict"] == "contradicted"]
    assert contradicted, "the focal gate should have contradicted the false premise"
    verdict = next(f for f in contradicted if f["id"] == "OPP-false")
    assert verdict["source"] == "opportunity"
    assert "goals" in verdict["x_phrase"].lower()
    assert any("rippling.com/goals" in ev["url"] for ev in verdict["focal_evidence"])

    # The false-premise opportunity is KEPT but CAVEATED in place — a fuzzy
    # x-phrase match must never empty the Action Board; the PMM sees a "verify
    # this premise" note instead of losing the whole play.
    assert "OPP-false" in state.opportunity_ids
    persisted = {
        o.opportunity_id: o
        for o in repo.list_opportunities(comp_run)
        if isinstance(o, MarketingOpportunity)
    }
    assert "OPP-false" in persisted
    assert str(persisted["OPP-false"].why_this_could_backfire).startswith("[focal-claims gate:")
    # The clean play SURVIVES untouched (no caveat).
    assert "OPP-clean" in state.opportunity_ids
    assert "OPP-clean" in persisted
    assert not str(persisted["OPP-clean"].why_this_could_backfire or "").startswith(
        "[focal-claims gate:"
    )

    # The caveat is disclosed honestly in the run's limitations.
    assert any(
        "caveated by the focal-claims gate" in lim.lower()
        and "Attack the missing goals story" in lim
        for lim in state.limitations
    )


async def test_focal_gate_no_false_premise_keeps_everything(tmp_path: Path) -> None:
    """A run with no 'focal lacks X' assertion drops nothing and stays a no-op."""
    from competitive_agent.storage.repository import Repository

    repo = Repository.open(tmp_path / "gate2.db")
    comp_run = "RUN-competitor2"
    focal_run = "RUN-focal2"
    clean = _opportunity(
        "OPP-ok",
        "Own the named-customer comparison",
        "Risk is low if we lead with verifiable named-customer proof.",
    )
    repo.save_opportunity(comp_run, clean)
    repo.save_artifact(focal_run, _goals_artifact())

    state = DirectorState(
        run_id=comp_run,
        company_input="deel.com",
        mode="comparative",
        company=_rippling(),  # any resolved company; unused by the gate
        focal_company=_rippling(),
        focal_run_id=focal_run,
        opportunity_ids=["OPP-ok"],
    )
    ctx = GraphContext(repository=repo, trace=None, config=None, settings=None)

    await critique_opportunities(state, ctx)

    assert [f for f in state.focal_gate_findings if f["verdict"] == "contradicted"] == []
    assert state.opportunity_ids == ["OPP-ok"]
    assert not any("withdrawn by the focal-claims gate" in lim.lower() for lim in state.limitations)
