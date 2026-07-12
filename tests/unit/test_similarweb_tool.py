"""Unit tests for the Similarweb-via-Exa enrichment adapter.

The Exa Agent API is stubbed by monkeypatching ``httpx.AsyncClient`` inside the
adapter module, so no network is touched. The tests pin the three contract
guarantees the blueprint requires: (1) the payload is capability-checked so
absent fields are dropped, never synthesized; (2) every returned metric is
labeled estimated; (3) a missing API key degrades to ``unsupported`` and never
blocks the report.
"""

from __future__ import annotations

from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools import similarweb as similarweb_module
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.similarweb import SimilarwebTool

DOMAIN = "example-hr.com"


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **record: Any) -> None:
        self.records.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient``: returns a canned Agent-run response.

    Records the last POST body so tests can assert the Similarweb provider was
    explicitly attached and the request schema is bounded.
    """

    last_post_json: dict[str, Any] | None = None
    response_payload: dict[str, Any] = {}
    status_code: int = 200

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any] | None = None) -> httpx.Response:
        FakeAsyncClient.last_post_json = json
        return httpx.Response(
            FakeAsyncClient.status_code,
            json=FakeAsyncClient.response_payload,
            request=httpx.Request("POST", url),
        )

    async def get(self, url: str) -> httpx.Response:  # pragma: no cover - completed inline
        return httpx.Response(
            FakeAsyncClient.status_code,
            json=FakeAsyncClient.response_payload,
            request=httpx.Request("GET", url),
        )


def make_context(exa_api_key: str = "test-key", mode: str = "live") -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"similarweb": True},
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
    parameters: dict[str, Any] = {"domain": DOMAIN}
    parameters.update(overrides)
    return ResearchAction(
        action_id="ACT-test-similarweb",
        action_type="enrich_similarweb",
        company_id="example-hr",
        parameters=parameters,
    )


def _install_agent_response(
    monkeypatch: Any, payload: dict[str, Any], status_code: int = 200
) -> None:
    FakeAsyncClient.response_payload = payload
    FakeAsyncClient.status_code = status_code
    FakeAsyncClient.last_post_json = None
    monkeypatch.setattr(similarweb_module.httpx, "AsyncClient", FakeAsyncClient)


# A payload that returns SOME fields and OMITS others (traffic_trend and
# estimated_paid_keywords are absent) so the capability-check has something to drop.
_PARTIAL_OUTPUT = {
    "id": "agentrun_test_1",
    "status": "completed",
    "output": {
        "estimated_monthly_visits": 1250000,
        "observation_period": "2026-04 to 2026-06",
        "channel_mix": {
            "direct": 0.41,
            "organic_search": 0.33,
            "paid_search": 0.09,
            # referral/social/display/mail deliberately omitted by the provider
            "bogus_channel": 0.99,  # not a recognized channel -> must be dropped
        },
        "top_countries": [
            {"country": "United States", "share": 0.38},
            {"country": "United Kingdom", "share": 0.11},
        ],
        "digital_competitors": [
            {"domain": "deel.com", "affinity": 0.62},
        ],
        # traffic_trend and estimated_paid_keywords intentionally NOT returned.
    },
    "costDollars": {"total": 0.012},
}


async def test_capability_check_drops_absent_fields_and_never_synthesizes(
    monkeypatch: Any,
) -> None:
    _install_agent_response(monkeypatch, _PARTIAL_OUTPUT)

    result = await SimilarwebTool().execute(make_action(), make_context())

    # Some metrics returned, some missing -> partial (not empty, not success).
    assert result.status == "partial"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]

    metrics = artifact.metadata["metrics"]
    # Present fields are kept...
    assert set(metrics) == {
        "estimated_monthly_visits",
        "channel_mix",
        "top_countries",
        "digital_competitors",
    }
    # ...and ABSENT fields stay absent — never synthesized to a placeholder.
    assert "traffic_trend" not in metrics
    assert "estimated_paid_keywords" not in metrics

    # channel_mix keeps only recognized, numeric channel shares.
    assert set(metrics["channel_mix"]["value"]) == {"direct", "organic_search", "paid_search"}
    assert "bogus_channel" not in metrics["channel_mix"]["value"]

    # The provider was explicitly attached and the request stayed bounded.
    body = FakeAsyncClient.last_post_json
    assert body is not None
    assert body["dataSources"] == [{"provider": "similarweb"}]
    assert set(body["outputSchema"]["properties"]) <= {
        "estimated_monthly_visits",
        "observation_period",
        "traffic_trend",
        "channel_mix",
        "top_countries",
        "digital_competitors",
        "estimated_paid_keywords",
    }

    # Provenance + honest collection method.
    assert artifact.source_type == "similarweb"
    assert artifact.collection_method == "exa_similarweb"
    assert artifact.metadata["provider"] == "similarweb"
    assert artifact.metadata["observation_period"] == "2026-04 to 2026-06"
    assert artifact.metadata["domain"] == DOMAIN
    assert artifact.is_fixture is False
    assert artifact.artifact_id.startswith("ART-")

    # The missing fields are disclosed as a negative observation, not filled in.
    assert result.negative_observations
    assert "traffic_trend" in result.negative_observations[0]


async def test_every_metric_is_labeled_estimated(monkeypatch: Any) -> None:
    _install_agent_response(monkeypatch, _PARTIAL_OUTPUT)

    result = await SimilarwebTool().execute(make_action(), make_context())
    artifact = result.artifacts[0]

    # Dataset-level label.
    assert artifact.metadata["estimated"] is True
    assert artifact.metadata["unit"]  # primary-metric unit present
    assert artifact.metadata["retrieval_timestamp"]

    # Per-metric label: EVERY returned metric is individually marked estimated,
    # carries a unit, and the report text says so.
    metrics = artifact.metadata["metrics"]
    assert metrics  # non-empty
    for name, metric in metrics.items():
        assert metric["estimated"] is True, name
        assert metric["unit"], name
        assert "value" in metric, name
    assert "All values estimated." in artifact.raw_text


async def test_missing_api_key_degrades_to_unsupported(monkeypatch: Any) -> None:
    # Even if httpx were reachable, no key must short-circuit to unsupported.
    _install_agent_response(monkeypatch, _PARTIAL_OUTPUT)

    result = await SimilarwebTool().execute(make_action(), make_context(exa_api_key=""))

    assert result.status == "unsupported"
    assert result.artifacts == []
    assert result.error_type == "provider_not_configured"
    # Non-blocking: the run is recorded as a traceable negative observation.
    assert result.negative_observations
    # No request was ever attempted without a key.
    assert FakeAsyncClient.last_post_json is None


async def test_empty_payload_is_empty_not_fabricated(monkeypatch: Any) -> None:
    _install_agent_response(
        monkeypatch, {"id": "agentrun_empty", "status": "completed", "output": {}}
    )

    result = await SimilarwebTool().execute(make_action(), make_context())

    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations


async def test_missing_domain_is_terminal(monkeypatch: Any) -> None:
    _install_agent_response(monkeypatch, _PARTIAL_OUTPUT)

    action = ResearchAction(
        action_id="ACT-test-similarweb",
        action_type="enrich_similarweb",
        company_id="example-hr",
        parameters={},
    )
    result = await SimilarwebTool().execute(action, make_context())

    assert result.status == "failed_terminal"
    assert result.error_type == "invalid_parameters"


async def test_fixture_mode_dispatches_recorded_estimates() -> None:
    result = await SimilarwebTool().execute(make_action(), make_context(mode="fixture"))

    assert result.status == "success"
    assert result.tool_name == "similarweb"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.is_fixture is True
    assert artifact.source_type == "similarweb"
    assert artifact.collection_method == "exa_similarweb"
    assert all(m["estimated"] for m in artifact.metadata["metrics"].values())
