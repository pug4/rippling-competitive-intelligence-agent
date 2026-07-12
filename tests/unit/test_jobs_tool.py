"""Unit tests for the public job-postings (Exa-backed) adapter."""

from __future__ import annotations

from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.jobs import JobsTool, _build_jobs_query

COMPANY = "Acme Corp"

EXA_RESPONSE = {
    "requestId": "req-jobs-123",
    "results": [
        {
            "id": "exa-1",
            "url": "https://boards.greenhouse.io/acme/jobs/sdr-mid-market",
            "title": "Sales Development Representative, Mid-Market",
            "text": "We're hiring SDRs to build an outbound motion into the mid-market.",
            "publishedDate": "2026-06-01T00:00:00Z",
            "author": None,
        },
        {
            "id": "exa-2",
            "url": "https://jobs.lever.co/acme/partnerships-manager",
            "title": "Partnerships Manager",
            "text": "Own the channel and partner ecosystem for EMEA expansion.",
            "publishedDate": None,
        },
    ],
    "costDollars": {"total": 0.004},
}


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **record: Any) -> None:
        self.records.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class StubJobsTool(JobsTool):
    """Overrides the direct provider POST with a canned Exa response.

    Retries are disabled so retryable-status tests never sleep through backoff.
    """

    max_live_retries = 0
    retry_base_delay = 0.0

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.captured_payload: dict[str, Any] | None = None
        self.captured_api_key: str | None = None

    async def _post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        self.captured_payload = payload
        self.captured_api_key = api_key
        return self._response


def make_context(mode: str = "live", exa_api_key: str = "test-key") -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"jobs": True},
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
        company_id="acme",
        mode=mode,  # type: ignore[arg-type]
        config=config,
        settings=Settings(exa_api_key=exa_api_key),
        repository=FakeRepository(),
    )


def make_action(**overrides: Any) -> ResearchAction:
    parameters: dict[str, Any] = {"company": COMPANY, "num_results": 5}
    parameters.update(overrides)
    return ResearchAction(
        action_id="ACT-test-jobs",
        action_type="search_jobs",
        company_id="acme",
        parameters=parameters,
    )


async def test_live_maps_public_postings_with_exact_query_and_signal_type() -> None:
    tool = StubJobsTool(httpx.Response(200, json=EXA_RESPONSE))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "success"
    assert len(result.artifacts) == 2
    assert result.cost_usd == 0.004

    expected_query = _build_jobs_query(COMPANY)
    # The forward-looking query is scoped to public ATS / job hosts.
    assert expected_query == (
        '"Acme Corp" careers OR jobs OR "we\'re hiring" '
        "site:boards.greenhouse.io OR site:jobs.lever.co OR site:linkedin.com/jobs"
    )
    # num_results flows through and page text is requested.
    assert tool.captured_payload is not None
    assert tool.captured_payload["query"] == expected_query
    assert tool.captured_payload["numResults"] == 5
    assert tool.captured_payload["contents"] == {"text": True}
    assert tool.captured_api_key == "test-key"

    for artifact in result.artifacts:
        assert artifact.source_type == "jobs"
        assert artifact.collection_method == "exa_jobs"
        # Provenance: the exact query rides on every artifact.
        assert artifact.metadata["exa_query"] == expected_query
        # Jobs are leading indicators, never proof of an executed strategy.
        assert artifact.metadata["signal_type"] == "leading_indicator"
        assert artifact.artifact_id.startswith("ART-")
        assert not artifact.is_fixture
        assert artifact.final_url == artifact.url
        assert artifact.content_hash

    sdr = result.artifacts[0]
    assert sdr.url == "https://boards.greenhouse.io/acme/jobs/sdr-mid-market"
    assert "outbound motion" in sdr.normalized_text
    assert sdr.is_partial is False  # provider returned page text
    assert sdr.metadata["exa_request_id"] == "req-jobs-123"


async def test_missing_api_key_is_unsupported_not_a_crash() -> None:
    tool = StubJobsTool(httpx.Response(200, json=EXA_RESPONSE))
    result = await tool.execute(make_action(), make_context(exa_api_key=""))

    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    assert result.artifacts == []
    assert result.negative_observations
    # No provider call should have been attempted.
    assert tool.captured_payload is None


async def test_missing_company_parameter_is_failed_terminal() -> None:
    tool = StubJobsTool(httpx.Response(200, json=EXA_RESPONSE))
    result = await tool.execute(make_action(company=""), make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "invalid_parameters"
    assert tool.captured_payload is None


async def test_empty_results_is_finding_with_query_in_observation() -> None:
    tool = StubJobsTool(httpx.Response(200, json={"results": [], "requestId": "req-empty"}))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations
    observation = result.negative_observations[0]
    assert _build_jobs_query(COMPANY) in observation
    # Empty search is never phrased as "the company is not hiring".
    assert "not evidence of no hiring" in observation


async def test_auth_failure_is_failed_terminal() -> None:
    tool = StubJobsTool(httpx.Response(401, text="unauthorized"))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "provider_auth"
    assert result.artifacts == []


async def test_rate_limit_is_failed_retryable() -> None:
    tool = StubJobsTool(httpx.Response(429, text="rate limited"))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert result.error_type == "rate_limited"


async def test_fixture_mode_dispatches_recorded_postings() -> None:
    result = await JobsTool().execute(make_action(), make_context(mode="fixture"))

    assert result.status == "success"
    assert result.tool_name == "jobs"
    assert result.action_id == "ACT-test-jobs"
    assert len(result.artifacts) == 2
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.source_type == "jobs"
        assert artifact.collection_method == "exa_jobs"
        assert artifact.metadata["signal_type"] == "leading_indicator"
        assert artifact.artifact_id.startswith("ART-")
        assert artifact.company_id == "acme"  # re-stamped to the running company


def test_capabilities_declare_leading_indicator_limitation() -> None:
    caps = JobsTool().capabilities()
    assert caps.supported_action_types == ["search_jobs"]
    assert caps.live_available and caps.fixture_available
    assert any("leading indicator" in limit for limit in caps.known_limitations)
