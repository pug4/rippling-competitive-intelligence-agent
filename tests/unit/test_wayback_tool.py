"""Unit tests for the Wayback historical-snapshot adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.wayback import WaybackTool

TARGET_URL = "https://example-hr.com/"

CDX_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]
CDX_THREE_ROWS = [
    CDX_HEADER,
    ["com,example-hr)/", "20240110080000", TARGET_URL, "text/html", "200", "AAA", "1000"],
    ["com,example-hr)/", "20240601120000", TARGET_URL, "text/html", "200", "BBB", "1100"],
    ["com,example-hr)/", "20241220153000", TARGET_URL, "text/html", "200", "CCC", "1200"],
]

SNAPSHOT_HTML = {
    "20240110080000": (
        "<html><head><title>Example HR - January</title></head>"
        "<body><h1>Global contractor payments</h1>"
        "<p>Pay contractors in 150+ countries.</p></body></html>"
    ),
    "20241220153000": (
        "<html><head><title>Example HR - December</title></head>"
        "<body><h1>All-in-one workforce platform</h1>"
        "<p>Payroll, HR, and IT in one place.</p></body></html>"
    ),
}


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class FakeHttp:
    """Stands in for SharedHttp: routes CDX and snapshot GETs to canned bodies."""

    def __init__(self, cdx_response: httpx.Response) -> None:
        self.cdx_response = cdx_response
        self.calls: list[str] = []

    async def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if "/cdx/search/cdx" in url:
            self.cdx_response.request = request
            return self.cdx_response
        timestamp = url.split("/web/")[1].split("id_/")[0]
        return httpx.Response(
            200,
            text=SNAPSHOT_HTML[timestamp],
            headers={"content-type": "text/html"},
            request=request,
        )


class NoRetryWaybackTool(WaybackTool):
    """Retry-free variant so failure tests do not sleep through backoff."""

    max_live_retries = 0
    retry_base_delay = 0.0


def make_context(mode: str = "live", http: FakeHttp | None = None) -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"wayback": True},
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
        settings=Settings(),
        repository=FakeRepository(),
        http=http,
    )


def make_action(**overrides: Any) -> ResearchAction:
    parameters = {
        "url": TARGET_URL,
        "window_start": "2024-01-01",
        "window_end": "2024-12-31",
        "max_snapshots": 2,
    }
    parameters.update(overrides)
    return ResearchAction(
        action_id="ACT-test-wayback",
        action_type="search_wayback",
        company_id="example-hr",
        parameters=parameters,
    )


async def test_live_picks_earliest_and_latest_with_actual_cdx_timestamps() -> None:
    http = FakeHttp(httpx.Response(200, json=CDX_THREE_ROWS))
    result = await WaybackTool().execute(make_action(), make_context(http=http))

    assert result.status == "success"
    assert len(result.artifacts) == 2

    cdx_call = http.calls[0]
    assert "/cdx/search/cdx" in cdx_call
    assert "from=20240101" in cdx_call
    assert "to=20241231" in cdx_call
    assert "output=json" in cdx_call

    earliest, latest = result.artifacts
    # archive_capture_at is the ACTUAL CDX capture moment, never the window edge.
    assert earliest.archive_capture_at == datetime(2024, 1, 10, 8, 0, 0, tzinfo=UTC)
    assert latest.archive_capture_at == datetime(2024, 12, 20, 15, 30, 0, tzinfo=UTC)
    assert earliest.published_at == earliest.archive_capture_at
    assert latest.published_at == latest.archive_capture_at

    # The middle capture was not selected (spread = earliest + latest for 2).
    assert not any("20240601120000" in call for call in http.calls)

    # Raw markup fetched through the id_ endpoint, recorded honestly.
    assert earliest.url == f"https://web.archive.org/web/20240110080000id_/{TARGET_URL}"
    assert latest.url == f"https://web.archive.org/web/20241220153000id_/{TARGET_URL}"
    assert earliest.url in http.calls and latest.url in http.calls
    assert earliest.raw_text == SNAPSHOT_HTML["20240110080000"]
    assert "global contractor payments" in earliest.normalized_text.lower()
    assert "all-in-one workforce platform" in latest.normalized_text.lower()

    for artifact in result.artifacts:
        assert artifact.source_type == "wayback"
        assert artifact.collection_method == "wayback_snapshot"
        assert artifact.metadata["original_url"] == TARGET_URL
        assert artifact.metadata["cdx_timestamp"] in ("20240110080000", "20241220153000")
        assert artifact.metadata["wayback_url"] == artifact.url
        assert artifact.final_url == artifact.url
        assert artifact.artifact_id.startswith("ART-")
        assert not artifact.is_fixture


async def test_empty_cdx_is_coverage_gap_not_page_absence() -> None:
    http = FakeHttp(httpx.Response(200, json=[]))
    result = await WaybackTool().execute(make_action(), make_context(http=http))

    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations
    observation = result.negative_observations[0]
    assert observation.startswith(f"no archive captures for {TARGET_URL} in 2024-01-01..2024-12-31")
    # Archive absence must never be phrased as the page being absent.
    assert "archive absence is never page absence" in observation


async def test_cdx_server_error_is_failed_retryable() -> None:
    http = FakeHttp(httpx.Response(503, text="upstream unavailable"))
    result = await NoRetryWaybackTool().execute(make_action(), make_context(http=http))

    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert result.error_type == "CdxRequestFailed"
    assert result.artifacts == []
    assert len(http.calls) == 1  # no snapshot fetches were attempted


async def test_fixture_mode_dispatches_recorded_snapshot() -> None:
    result = await WaybackTool().execute(make_action(), make_context(mode="fixture"))

    assert result.status == "success"
    assert result.tool_name == "wayback"
    assert result.action_id == "ACT-test-wayback"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.is_fixture is True
    assert artifact.collection_method == "wayback_snapshot"
    assert artifact.archive_capture_at == datetime(2024, 3, 12, 9, 45, 0, tzinfo=UTC)
    assert "global contractor payments" in artifact.normalized_text.lower()
    assert "id_" in artifact.url
