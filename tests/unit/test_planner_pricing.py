"""Feedback #6/#34: a required dimension must exhaust public fallbacks before
the loop may stop for low expected value."""

from __future__ import annotations

from types import SimpleNamespace

from competitive_agent import planner
from competitive_agent.config import get_config
from competitive_agent.coverage import initial_coverage
from competitive_agent.schemas.common import new_id, utcnow
from competitive_agent.schemas.company import Company
from competitive_agent.state import DirectorState


def _state():
    s = DirectorState(
        run_id="R", company_input="deel.com", mode="snapshot", execution_mode="fixture"
    )
    s.company = Company(
        company_id=new_id("CO"),
        canonical_name="Deel",
        primary_domain="deel.com",
        resolved_at=utcnow(),
        resolution_confidence="high",
    )
    s.coverage = initial_coverage()
    # Everything covered except pricing.
    for d in (
        "identity",
        "current_website",
        "current_product",
        "news_and_launches",
        "portfolio_discovery",
        "product_positioning",
        "commercial_motion",
    ):
        s.coverage[d] = "high"
    s.coverage["pricing_and_packaging"] = "low"
    return s


def _ctx():
    return SimpleNamespace(config=get_config(), scratch={})


def test_pricing_fallbacks_are_proposed_when_pricing_incomplete():
    state = _state()
    actions = planner.propose_actions(state, _ctx())
    types = {(a.source_name, a.action_type) for a in actions}
    # An Exa pricing search and a Wayback pricing fallback must be offered.
    assert ("exa_search", "search_exa_web") in types
    assert ("wayback", "search_wayback") in types
    assert "pricing_and_packaging" in planner.required_dims_needing_exhaustion(state)


def test_pricing_not_needing_exhaustion_once_unavailable():
    state = _state()
    state.coverage["pricing_and_packaging"] = "unavailable"
    assert "pricing_and_packaging" not in planner.required_dims_needing_exhaustion(state)
