"""Exa Agent tool — query construction, capabilities, and the no-key path.
Live polling is exercised against the real API in an integration/live run."""

from __future__ import annotations

from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.exa_agent import ExaAgentTool, _query_for


def _action(action_type, **params):
    return ResearchAction(action_id="ACT-x", action_type=action_type, company_id="c", parameters=params)


def test_linkedin_query_includes_company_and_url():
    q = _query_for(_action("research_linkedin", company="Deel", domain="deel.com",
                           linkedin_url="https://www.linkedin.com/company/deel"))
    assert "Deel" in q and "LinkedIn" in q
    assert "linkedin.com/company/deel" in q
    assert "cited" in q.lower()


def test_company_query_mentions_focal_when_given():
    q = _query_for(_action("research_company", company="Deel", focal="Rippling"))
    assert "Deel" in q and "Rippling" in q


def test_supported_actions_and_capabilities():
    tool = ExaAgentTool()
    assert tool.supports(_action("research_linkedin"))
    assert not tool.supports(_action("search_exa_web"))
    caps = tool.capabilities()
    assert "research_linkedin" in caps.supported_action_types
    # Honesty: the synthesis is labeled as not-first-party in the limitations.
    assert any("synthesis" in lim.lower() for lim in caps.known_limitations)
