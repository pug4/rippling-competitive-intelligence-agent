"""NewsTool (adversarial-context news): fixture mode, keyless typed degrade,
deterministic keyword categorization, and URL dedup across the query sweep.

No network anywhere: fixture mode is served from
tests/fixtures/tools/news_market/, and the live path's Exa seam (``_exa_post``)
is monkeypatched so no HTTP ever leaves the process.
"""

from __future__ import annotations

from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.news import (
    EXA_SEARCH_URL,
    NO_EXA_KEY_NOTE,
    NewsTool,
    categorize_news,
)


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **kwargs: Any) -> None:
        self.records.append(kwargs)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


def make_context(mode: str = "live", exa_api_key: str = "test-exa-key") -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"news_market": True},
        execution={},
        budgets={},
        portfolio={},
        windows={},
        taxonomy={},
        model_routes={},
        source_capabilities={},
    )
    return ToolContext(
        run_id="RUN-test",
        company_id="example-competitor",
        mode=mode,  # type: ignore[arg-type]
        config=config,
        settings=Settings(exa_api_key=exa_api_key, anthropic_api_key=""),
        repository=FakeRepository(),
    )


def make_action(**params: Any) -> ResearchAction:
    return ResearchAction(
        action_id="ACT-test-news",
        action_type="search_market_context",
        company_id="example-competitor",
        parameters=params,
    )


def news_result(url: str, title: str, text: str, date: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"url": url, "title": title, "text": text}
    if date is not None:
        item["publishedDate"] = date
    return item


def install_exa(
    monkeypatch: Any, tool: NewsTool, responses_by_call: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Monkeypatch the tool's Exa seam; returns the recorded search payloads."""
    calls: list[dict[str, Any]] = []

    async def fake_exa_post(url: str, payload: dict[str, Any], api_key: str) -> httpx.Response:
        assert url == EXA_SEARCH_URL
        calls.append(payload)
        index = len(calls) - 1
        body = responses_by_call[index] if index < len(responses_by_call) else {"results": []}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(tool, "_exa_post", fake_exa_post)
    return calls


# ---- deterministic categorization (pure Python, no model) --------------------


def test_categorize_lawsuit_snippet_is_litigation() -> None:
    assert (
        categorize_news(
            "Competitor faces class action",
            "A class action lawsuit was filed alleging the company mishandled data.",
        )
        == "litigation"
    )


def test_categorize_funding_snippet_is_funding() -> None:
    assert (
        categorize_news(
            "Competitor raises Series C",
            "The company raised $120M in a Series C round at a higher valuation.",
        )
        == "funding"
    )


def test_categorize_covers_every_bucket() -> None:
    assert categorize_news("", "SecureCo acquires a rival in an all-stock merger.") == "m_and_a"
    assert categorize_news("", "The company launches a new product this week.") == "launch"
    assert categorize_news("", "Competitor vs Rippling: which platform wins?") == "comparison"
    # No keyword hit -> the conservative 'other' default, never an invented label.
    assert categorize_news("", "The company published a quarterly blog post.") == "other"


def test_funding_beats_launch_when_both_signals_present() -> None:
    # "announces" (launch) + "Series B"/"raised" (funding): funding is checked
    # first so the harder financial signal wins.
    assert categorize_news("", "The company announces it raised a $50M Series B.") == "funding"


# ---- fixture mode ------------------------------------------------------------


async def test_fixture_mode_returns_dated_categorized_artifacts() -> None:
    result = await NewsTool().execute(
        make_action(company="Example Competitor", focal="Rippling"),
        make_context(mode="fixture"),
    )
    assert result.status == "success"
    assert len(result.artifacts) == 3
    categories = set()
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.source_type == "news_market"
        # The company_id is re-stamped from the running action.
        assert artifact.company_id == "example-competitor"
        meta = artifact.metadata
        # Every required metadata key is present.
        for key in ("category", "published_at", "title", "query", "url"):
            assert key in meta
        # Dated public news item: a real published_at rides on the artifact.
        assert artifact.published_at is not None
        assert meta["published_at"]
        categories.add(meta["category"])
    # Synthetic items span multiple adversarial categories.
    assert {"litigation", "funding", "launch"} <= categories


# ---- keyless live -> typed unsupported ---------------------------------------


async def test_keyless_live_degrades_typed_unsupported() -> None:
    result = await NewsTool().execute(
        make_action(company="Example Competitor"),
        make_context(mode="live", exa_api_key=""),
    )
    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    assert result.artifacts == []
    assert NO_EXA_KEY_NOTE in result.negative_observations


# ---- live path: categorization applied + URL dedup ---------------------------


async def test_live_sweep_categorizes_and_dedups_by_url(monkeypatch: Any) -> None:
    tool = NewsTool()
    url_a = "https://news.example.com/example-competitor-lawsuit"
    url_b = "https://news.example.com/example-competitor-series-c"
    calls = install_exa(
        monkeypatch,
        tool,
        [
            {
                "results": [
                    news_result(
                        url_a,
                        "Example Competitor sued in class action",
                        "A class action lawsuit alleges the company breached its "
                        "contracts; the complaint seeks an injunction.",
                        date="2026-05-14T00:00:00Z",
                    ),
                    news_result(
                        url_b,
                        "Example Competitor raises Series C",
                        "The company raised $120M in a Series C round.",
                        date="2026-04-02T00:00:00Z",
                    ),
                ]
            },
            # Second query re-surfaces url_a (a duplicate) — must be dropped.
            {"results": [news_result(url_a, "dup", "dup litigation text")]},
            {"results": []},
            {"results": []},
        ],
    )
    # No focal -> exactly the four adversarial-angle queries.
    result = await tool.execute(make_action(company="Example Competitor"), make_context())
    assert len(calls) == 4
    assert result.status == "success"

    # url_a appears exactly once despite being returned by two queries.
    urls = [a.url for a in result.artifacts]
    assert urls.count(url_a) == 1
    assert set(urls) == {url_a, url_b}

    by_url = {a.url: a for a in result.artifacts}
    assert by_url[url_a].metadata["category"] == "litigation"
    assert by_url[url_b].metadata["category"] == "funding"
    # The originating query is recorded on each artifact for provenance.
    assert "lawsuit" in by_url[url_a].metadata["query"].lower()
    # Dated news: published_at parsed onto the artifact.
    assert by_url[url_a].published_at is not None


async def test_focal_query_added_when_focal_present(monkeypatch: Any) -> None:
    tool = NewsTool()
    calls = install_exa(monkeypatch, tool, [{"results": []}] * 5)
    result = await tool.execute(
        make_action(company="Example Competitor", focal="Rippling"), make_context()
    )
    # 4 adversarial-angle queries + 1 competitor-vs-focal query.
    assert len(calls) == 5
    assert any("vs Rippling" in c["query"] for c in calls)
    # Nothing found across the whole sweep -> a typed empty, never a failure.
    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations
