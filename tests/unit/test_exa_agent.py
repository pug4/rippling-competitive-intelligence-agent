"""Exa Agent tool — query construction, capabilities, and the no-key path.
Live polling is exercised against the real API in an integration/live run."""

from __future__ import annotations

from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.exa_agent import ExaAgentTool, _query_for


def _action(action_type, **params):
    return ResearchAction(
        action_id="ACT-x", action_type=action_type, company_id="c", parameters=params
    )


def test_linkedin_query_asks_for_per_post_data():
    q = _query_for(
        _action(
            "research_linkedin",
            company="Deel",
            domain="deel.com",
            linkedin_url="https://www.linkedin.com/company/deel",
            num_posts=15,
        )
    )
    assert "Deel" in q and "LinkedIn" in q
    assert "linkedin.com/company/deel" in q
    # Must request individual posts with URL + author + text (per-post, not just a synthesis).
    assert "post_url" in q and "author" in q
    assert "cite" in q.lower()


def test_company_query_mentions_focal_when_given():
    q = _query_for(_action("research_company", company="Deel", focal="Rippling"))
    assert "Deel" in q and "Rippling" in q


def test_map_fans_out_one_artifact_per_post():
    tool = ExaAgentTool()
    run = {
        "id": "agent_run_1",
        "status": "completed",
        "request": {"query": "..."},
        "costDollars": {"total": 0.03},
        "output": {
            "text": "",
            "structured": {
                "synthesis": "Two themes dominate.",
                "posts": [
                    {
                        "post_url": "https://linkedin.com/posts/a",
                        "author": "Dana",
                        "author_role": "VP",
                        "posted_at": "2026-06-20",
                        "theme": "consolidation",
                        "text": "Post one text about consolidation.",
                    },
                    {
                        "post_url": "https://linkedin.com/posts/b",
                        "author": "Priya",
                        "theme": "compliance",
                        "text": "Post two text about compliance.",
                    },
                    {"post_url": "", "text": "dropped — no url"},
                ],
            },
        },
    }
    result = tool._map(_action("research_linkedin", company="Deel"), run)
    arts = result.artifacts
    posts = [a for a in arts if a.source_type == "linkedin_post"]
    synth = [a for a in arts if a.source_type == "linkedin"]
    assert len(posts) == 2, "one artifact per valid post; the url-less post is dropped"
    assert {a.author for a in posts} == {"Dana", "Priya"}
    assert posts[0].url == "https://linkedin.com/posts/a" and posts[0].published_at is not None
    assert len(synth) == 1  # synthesis kept as its own artifact


def test_supported_actions_and_capabilities():
    tool = ExaAgentTool()
    assert tool.supports(_action("research_linkedin"))
    assert not tool.supports(_action("search_exa_web"))
    caps = tool.capabilities()
    assert "research_linkedin" in caps.supported_action_types
    # Honesty: the synthesis is labeled as not-first-party in the limitations.
    assert any("synthesis" in lim.lower() for lim in caps.known_limitations)
