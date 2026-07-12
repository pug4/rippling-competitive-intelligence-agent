"""Unit tests for the conferences / events discovery adapter."""

from __future__ import annotations

from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.events import EventsTool

COMPANY = "Example HR"
EXPECTED_QUERY = (
    '"Example HR" sponsor OR exhibitor OR speaker OR booth OR keynote OR webinar conference'
)

EXA_RESPONSE = {
    "requestId": "req-events-1",
    "costDollars": {"total": 0.005},
    "results": [
        {
            "id": "https://hrtechconf.com/2026/sponsors/example-hr",
            "url": "https://hrtechconf.com/2026/sponsors/example-hr",
            "title": "Example HR - Platinum Sponsor | HR Tech 2026",
            "publishedDate": "2026-05-14T00:00:00Z",
            "author": None,
            "text": "Example HR returns as a Platinum Sponsor. Visit booth #412 for a live demo.",
        },
        {
            # No URL -> must be dropped (partial), never fabricated.
            "id": "no-url",
            "title": "Orphan result",
            "text": "no url here",
        },
    ],
}


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **record: Any) -> None:
        self.records.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class StubEventsTool(EventsTool):
    """Captures the outgoing Exa payload/key and returns a canned response."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.captured_payload: dict[str, Any] | None = None
        self.captured_api_key: str | None = None

    async def _post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        self.captured_payload = payload
        self.captured_api_key = api_key
        return self._response


class NoRetryStubEventsTool(StubEventsTool):
    """Retry-free variant so failure tests do not sleep through backoff."""

    max_live_retries = 0
    retry_base_delay = 0.0


def _json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code, json=payload, request=httpx.Request("POST", "https://api.exa.ai/search")
    )


def make_context(
    mode: str = "live",
    *,
    exa_api_key: str = "test-key",
    events_enabled: bool = True,
) -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"events": events_enabled},
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
        action_id="ACT-test-events",
        action_type="search_events",
        company_id="example-hr",
        parameters=parameters,
    )


async def test_live_success_maps_events_artifact_with_query_and_presence_types() -> None:
    tool = StubEventsTool(_json_response(EXA_RESPONSE))
    result = await tool.execute(make_action(), make_context())

    # One result lacked a URL and was dropped -> partial, not fabricated.
    assert result.status == "partial"
    assert len(result.artifacts) == 1
    assert result.negative_observations  # records the dropped result

    # The exact query built from the company name is sent to Exa.
    assert tool.captured_api_key == "test-key"
    assert tool.captured_payload is not None
    assert tool.captured_payload["query"] == EXPECTED_QUERY
    assert tool.captured_payload["numResults"] == 8
    assert tool.captured_payload["contents"] == {"text": True}

    artifact = result.artifacts[0]
    assert artifact.source_type == "events"
    assert artifact.collection_method == "exa_events"
    assert artifact.url == "https://hrtechconf.com/2026/sponsors/example-hr"
    assert artifact.final_url == artifact.url
    assert "platinum sponsor" in artifact.normalized_text.lower()
    # The exact query rides on every artifact (provenance).
    assert artifact.metadata["exa_query"] == EXPECTED_QUERY
    assert artifact.metadata["presence_types_possible"] == [
        "sponsor",
        "exhibitor",
        "speaker",
        "host",
        "webinar",
        "customer_speaker",
    ]
    assert artifact.artifact_id.startswith("ART-")
    assert artifact.is_fixture is False
    assert result.cost_usd == 0.005


async def test_missing_api_key_is_unsupported_not_failure() -> None:
    tool = StubEventsTool(_json_response(EXA_RESPONSE))
    result = await tool.execute(make_action(), make_context(exa_api_key=""))

    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    assert result.artifacts == []
    assert result.negative_observations
    # No provider call should have been attempted.
    assert tool.captured_payload is None


async def test_empty_results_is_empty_with_query_in_negative_observation() -> None:
    tool = StubEventsTool(_json_response({"results": [], "costDollars": {"total": 0.001}}))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations
    assert EXPECTED_QUERY in result.negative_observations[0]


async def test_auth_error_is_failed_terminal() -> None:
    tool = NoRetryStubEventsTool(_json_response({"error": "unauthorized"}, status_code=401))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "provider_auth"
    assert result.artifacts == []


async def test_rate_limit_is_failed_retryable() -> None:
    tool = NoRetryStubEventsTool(_json_response({"error": "slow down"}, status_code=429))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert result.error_type == "rate_limited"


async def test_missing_company_parameter_is_failed_terminal() -> None:
    tool = StubEventsTool(_json_response(EXA_RESPONSE))
    result = await tool.execute(make_action(company=""), make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "invalid_parameters"
    assert tool.captured_payload is None


async def test_disabled_source_is_skipped_not_attempted() -> None:
    tool = StubEventsTool(_json_response(EXA_RESPONSE))
    result = await tool.execute(make_action(), make_context(events_enabled=False))

    assert result.status == "skipped_disabled"
    assert tool.captured_payload is None


async def test_fixture_mode_dispatches_recorded_event() -> None:
    result = await EventsTool().execute(make_action(), make_context(mode="fixture"))

    assert result.status == "success"
    assert result.tool_name == "events"
    assert result.action_id == "ACT-test-events"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.is_fixture is True
    assert artifact.source_type == "events"
    assert artifact.collection_method == "exa_events"
    assert "sponsor" in artifact.normalized_text.lower()
    assert artifact.metadata["presence_types_possible"] == [
        "sponsor",
        "exhibitor",
        "speaker",
        "host",
        "webinar",
        "customer_speaker",
    ]
