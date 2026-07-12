"""Unit tests for the reviews / buyer-language discovery adapter."""

from __future__ import annotations

from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.reviews import ReviewsTool

COMPANY = "Example HR"

EXA_RESULTS = {
    "requestId": "req-reviews-1",
    "costDollars": {"total": 0.005},
    "results": [
        {
            "id": "https://www.g2.com/products/example-hr/reviews",
            "url": "https://www.g2.com/products/example-hr/reviews",
            "title": "Example HR Reviews & Ratings",
            "publishedDate": "2026-05-14T00:00:00Z",
            "author": "Verified G2 User",
            "text": (
                "Cons: onboarding was painful and support was slow to respond. "
                "Pros: contractor payments are easy."
            ),
        },
        {
            "id": "https://www.capterra.com/p/example-hr/reviews/",
            "url": "https://www.capterra.com/p/example-hr/reviews/",
            "title": "Example HR Reviews 2026 - Capterra",
            "publishedDate": "2026-04-02T00:00:00Z",
            "text": "The biggest complaint was pricing at renewal and clunky approvals.",
        },
    ],
}


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class StubReviewsTool(ReviewsTool):
    """Captures the Exa request body and returns a canned response.

    Avoids any real network I/O while still exercising ``_execute_live``'s
    payload construction and response mapping end to end.
    """

    max_live_retries = 0
    retry_base_delay = 0.0

    def __init__(self, response: httpx.Response) -> None:
        super().__init__()
        self._response = response
        self.sent_payload: dict[str, Any] | None = None

    async def _post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        self.sent_payload = payload
        return self._response


def make_context(exa_api_key: str = "test-key", mode: str = "live") -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"reviews": True},
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
        action_id="ACT-test-reviews",
        action_type="search_reviews",
        company_id="example-hr",
        parameters=parameters,
    )


def _resp(status: int, **kwargs: Any) -> httpx.Response:
    request = httpx.Request("POST", "https://api.exa.ai/search")
    return httpx.Response(status, request=request, **kwargs)


async def test_live_maps_review_results_with_pain_language_framing() -> None:
    tool = StubReviewsTool(_resp(200, json=EXA_RESULTS))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "success"
    assert len(result.artifacts) == 2
    assert result.cost_usd == 0.005

    # The query is buyer-language biased and scoped to the review sites.
    expected_query = '"Example HR" review OR "cons" OR "pros" G2 OR Capterra OR TrustRadius'
    assert tool.sent_payload is not None
    assert tool.sent_payload["query"] == expected_query
    assert tool.sent_payload["numResults"] == 8
    assert tool.sent_payload["contents"] == {"text": True}
    assert tool.sent_payload["includeDomains"] == ["g2.com", "capterra.com", "trustradius.com"]

    for artifact in result.artifacts:
        assert artifact.source_type == "reviews"
        assert artifact.collection_method == "exa_reviews"
        assert artifact.source_name == "reviews"
        assert artifact.url == artifact.final_url
        assert artifact.artifact_id.startswith("ART-")
        assert not artifact.is_fixture
        # Framing that must ride on every artifact.
        assert artifact.metadata["exa_query"] == expected_query
        assert artifact.metadata["source_perspective"] == "review_site"
        assert artifact.metadata["representativeness"] == "non_representative_selection_biased"

    first = result.artifacts[0]
    assert "onboarding was painful" in first.normalized_text.lower()
    assert first.title == "Example HR Reviews & Ratings"


async def test_missing_api_key_is_unsupported_not_a_crash() -> None:
    tool = StubReviewsTool(_resp(200, json=EXA_RESULTS))
    result = await tool.execute(make_action(), make_context(exa_api_key=""))

    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    assert result.artifacts == []
    assert result.negative_observations
    # The stub's _post must never have been reached.
    assert tool.sent_payload is None


async def test_empty_results_is_negative_observation_not_absence_claim() -> None:
    tool = StubReviewsTool(_resp(200, json={"results": []}))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations
    observation = result.negative_observations[0]
    assert "Example HR" in observation
    assert "not evidence" in observation.lower()


async def test_auth_failure_is_failed_terminal() -> None:
    tool = StubReviewsTool(_resp(401, text="unauthorized"))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "provider_auth"
    assert result.artifacts == []


async def test_rate_limit_is_failed_retryable() -> None:
    tool = StubReviewsTool(_resp(429, text="slow down"))
    result = await tool.execute(make_action(), make_context())

    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert result.error_type == "rate_limited"


async def test_include_review_domains_can_be_disabled() -> None:
    tool = StubReviewsTool(_resp(200, json=EXA_RESULTS))
    await tool.execute(make_action(include_review_domains=False), make_context())
    assert tool.sent_payload is not None
    assert "includeDomains" not in tool.sent_payload


async def test_capabilities_declare_non_representative_limitation() -> None:
    caps = ReviewsTool().capabilities()
    assert "search_reviews" in caps.supported_action_types
    joined = " ".join(caps.known_limitations).lower()
    assert "selection-biased" in joined or "selection bias" in joined
    assert "representative" in joined
    assert "objection mining" in joined


async def test_fixture_mode_dispatches_recorded_reviews() -> None:
    result = await ReviewsTool().execute(make_action(), make_context(mode="fixture"))

    assert result.status == "success"
    assert result.tool_name == "reviews"
    assert result.action_id == "ACT-test-reviews"
    assert len(result.artifacts) == 2
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.source_type == "reviews"
        assert artifact.collection_method == "exa_reviews"
        assert artifact.metadata["source_perspective"] == "review_site"
        assert artifact.metadata["representativeness"] == "non_representative_selection_biased"
    assert "onboarding is painful" in result.artifacts[0].normalized_text.lower()
