"""Unit tests for the out-of-home (OOH) discovery adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.ooh import OOHTool

COMPANY = "Example HR"


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **record: Any) -> None:
        self.records.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


def make_context(
    *,
    mode: str = "live",
    exa_api_key: str = "test-key",
    ooh_enabled: bool = True,
) -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"ooh": ooh_enabled},
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
        company_id="example-hr",
        mode=mode,  # type: ignore[arg-type]
        config=config,
        settings=Settings(exa_api_key=exa_api_key),
        repository=FakeRepository(),
    )


def make_action(**overrides: Any) -> ResearchAction:
    parameters: dict[str, Any] = {"company": COMPANY, "num_results": 8}
    parameters.update(overrides)
    return ResearchAction(
        action_id="ACT-test-ooh",
        action_type="search_ooh",
        company_id="example-hr",
        parameters=parameters,
    )


def _mock_tool(handler) -> OOHTool:
    """An OOHTool whose provider client is backed by an httpx MockTransport."""

    class _MockedOOHTool(OOHTool):
        # No boundary retries so failure tests never sleep through backoff.
        max_live_retries = 0
        retry_base_delay = 0.0

        def _build_client(self, api_key: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers={"x-api-key": api_key},
            )

    return _MockedOOHTool()


def _exa_result(url: str, text: str = "billboard campaign copy") -> dict[str, Any]:
    return {"url": url, "title": "OOH hit", "text": text, "id": url}


async def test_live_aggregates_and_dedups_across_query_families() -> None:
    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        seen_queries.append(query)
        # billboard + linkedin families each return the SAME url once, plus a
        # unique url; dedup must collapse the shared one.
        if query == '"Example HR" billboard':
            return httpx.Response(
                200,
                json={
                    "results": [
                        _exa_result("https://press.example/ooh-billboard"),
                        _exa_result("https://shared.example/ooh"),
                    ],
                    "costDollars": {"total": 0.01},
                },
            )
        if "linkedin" in query:
            return httpx.Response(
                200,
                json={"results": [_exa_result("https://shared.example/ooh")]},
            )
        return httpx.Response(200, json={"results": []})

    result = await _mock_tool(handler).execute(make_action(), make_context())

    assert result.status == "success"
    # All 11 §13.2 query families were issued.
    assert len(seen_queries) == 11
    assert '"Example HR" billboard' in seen_queries
    assert 'site:linkedin.com/posts "Example HR" billboard' in seen_queries
    assert 'site:instagram.com "Example HR" billboard' in seen_queries

    # Dedup: shared url collapses to one -> 2 distinct artifacts total.
    urls = sorted(a.url for a in result.artifacts)
    assert urls == ["https://press.example/ooh-billboard", "https://shared.example/ooh"]

    for artifact in result.artifacts:
        assert artifact.source_type == "ooh"
        assert artifact.collection_method == "exa_ooh"
        assert artifact.artifact_id.startswith("ART-")
        assert not artifact.is_fixture
        meta = artifact.metadata
        assert meta["coverage"] == "low"
        assert meta["spend_known"] is False
        assert meta["reach_known"] is False
        assert meta["performance_known"] is False
        # The exact query that surfaced the artifact is recorded.
        assert '"Example HR"' in meta["exa_query"]

    assert result.cost_usd == 0.01


async def test_empty_is_low_coverage_not_absence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    result = await _mock_tool(handler).execute(make_action(), make_context())

    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations
    note = result.negative_observations[0]
    assert "LOW COVERAGE" in note
    assert "not proof of" in note
    assert "no universal ooh registry" in note.lower()


async def test_missing_api_key_is_unsupported() -> None:
    result = await OOHTool().execute(make_action(), make_context(exa_api_key=""))

    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    # Even unavailability is framed as low coverage, never as absence.
    assert result.negative_observations
    assert "LOW COVERAGE" in result.negative_observations[0]


async def test_auth_error_is_failed_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    result = await _mock_tool(handler).execute(make_action(), make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "provider_auth"
    assert result.artifacts == []


async def test_rate_limit_is_failed_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"})

    result = await _mock_tool(handler).execute(make_action(), make_context())

    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert result.error_type == "rate_limited"


async def test_all_queries_erroring_is_failed_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    result = await _mock_tool(handler).execute(make_action(), make_context())

    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert result.artifacts == []


async def test_partial_when_some_queries_have_hits_and_others_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["query"] == '"Example HR" billboard':
            return httpx.Response(200, json={"results": [_exa_result("https://press.example/ooh")]})
        return httpx.Response(503, text="upstream unavailable")

    result = await _mock_tool(handler).execute(make_action(), make_context())

    assert result.status == "partial"
    assert len(result.artifacts) == 1
    # Every failed query is disclosed as a coverage note, not swallowed.
    assert result.negative_observations
    assert any("failed to complete" in note for note in result.negative_observations)


async def test_capabilities_carry_the_hard_low_coverage_rule() -> None:
    caps = OOHTool().capabilities()
    assert "search_ooh" in caps.supported_action_types
    hard_rule = caps.known_limitations[0]
    assert "LOW COVERAGE" in hard_rule
    assert "never" in hard_rule
    assert "no universal ooh registry" in hard_rule.lower()


async def test_disabled_source_is_skipped() -> None:
    result = await OOHTool().execute(make_action(), make_context(ooh_enabled=False))
    assert result.status == "skipped_disabled"


async def test_fixture_mode_dispatches_recorded_ooh_evidence() -> None:
    result = await OOHTool().execute(make_action(), make_context(mode="fixture"))

    assert result.status == "success"
    assert result.tool_name == "ooh"
    assert result.action_id == "ACT-test-ooh"
    assert len(result.artifacts) == 2
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.source_type == "ooh"
        assert artifact.collection_method == "exa_ooh"
        assert artifact.metadata["coverage"] == "low"
        assert artifact.metadata["spend_known"] is False
        assert "exa_query" in artifact.metadata
