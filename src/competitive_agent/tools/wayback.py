"""Wayback Machine historical-snapshot adapter (blueprint §37.12, §39.7).

Queries the CDX index for captures of a target URL inside a date window,
selects up to ``max_snapshots`` spread across the window (earliest + latest
when 2), fetches each selected capture via the ``id_`` raw-markup endpoint,
and returns one :class:`RawArtifact` per snapshot with the ACTUAL capture
timestamp recorded in ``archive_capture_at`` — never the requested date.

IMPORTANT: archive absence is NEVER page absence. The Wayback Machine only
holds pages its crawlers happened to capture; "no snapshot in the window"
means only that no capture exists, not that the page did not exist. That
distinction is preserved as a negative observation, never as a page claim.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, ClassVar
from urllib.parse import urlencode

from ..processing.normalize import content_hash, html_to_text, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext

CDX_ENDPOINT = "http://web.archive.org/cdx/search/cdx"
# ``id_`` infix asks the Wayback Machine for the original raw markup,
# without the archive's replay toolbar/rewriting injected into the page.
SNAPSHOT_URL_TEMPLATE = "https://web.archive.org/web/{timestamp}id_/{original_url}"

_CDX_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"


def _parse_cdx_timestamp(value: str) -> datetime:
    """Parse a CDX ``YYYYMMDDhhmmss`` timestamp into an aware UTC datetime."""
    return datetime.strptime(value, _CDX_TIMESTAMP_FORMAT).replace(tzinfo=UTC)


def _iso_to_cdx_date(value: str) -> str:
    """ISO date/datetime string -> CDX ``YYYYMMDD``. Raises ValueError if bad."""
    return date.fromisoformat(str(value)[:10]).strftime("%Y%m%d")


def _select_spread(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    """Pick up to ``count`` snapshots spread across the (sorted) row list.

    ``count`` == 2 yields exactly the earliest and the latest capture; larger
    counts interpolate evenly between the two ends.
    """
    if count >= len(rows):
        return list(rows)
    if count == 1:
        return [rows[0]]
    last = len(rows) - 1
    indices = sorted({round(i * last / (count - 1)) for i in range(count)})
    return [rows[i] for i in indices]


class WaybackTool(BaseTool):
    """Historical page snapshots via the Internet Archive Wayback Machine."""

    name: ClassVar[str] = "wayback"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "wayback"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_wayback",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=True,
            supports_historical_data=True,
            supports_exact_content=True,
            returns_estimates=False,
            known_limitations=[
                "Archive absence is never page absence: the Wayback Machine only "
                "holds pages its crawlers captured.",
                "Capture cadence is irregular; coverage inside a window may be sparse.",
                "Snapshots reflect the capture moment, not the full publication period.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        params: dict[str, Any] = action.parameters or {}
        target_url = str(params.get("url") or "").strip()
        if not target_url:
            return self._result(
                action,
                status="failed_terminal",
                error_type="ValueError",
                error_message="search_wayback requires a 'url' parameter.",
            )
        if context.http is None:
            return self._result(
                action,
                status="failed_terminal",
                error_type="RuntimeError",
                error_message="No HTTP client available in the tool context.",
            )

        window_start = params.get("window_start")
        window_end = params.get("window_end")
        try:
            max_snapshots = max(1, int(params.get("max_snapshots", 2)))
            cdx_query: dict[str, str] = {
                "url": target_url,
                "output": "json",
                "filter": "statuscode:200",
                "collapse": "timestamp:6",
            }
            if window_start:
                cdx_query["from"] = _iso_to_cdx_date(window_start)
            if window_end:
                cdx_query["to"] = _iso_to_cdx_date(window_end)
        except (TypeError, ValueError) as exc:
            return self._result(
                action,
                status="failed_terminal",
                error_type=type(exc).__name__,
                error_message=f"Invalid search_wayback parameters: {exc}",
            )

        window_desc = f"{window_start or 'earliest'}..{window_end or 'latest'}"

        # --- CDX snapshot discovery ------------------------------------------
        cdx_url = f"{CDX_ENDPOINT}?{urlencode(cdx_query)}"
        cdx_response = await context.http.get(cdx_url)
        if cdx_response.status_code != 200:
            # CDX failure means we could not even LIST captures; retryable.
            return self._result(
                action,
                status="failed_retryable",
                retryable=True,
                error_type="CdxRequestFailed",
                error_message=(
                    f"Wayback CDX query returned HTTP {cdx_response.status_code} for {target_url}."
                ),
            )
        try:
            rows = cdx_response.json()
        except ValueError:
            return self._result(
                action,
                status="failed_retryable",
                retryable=True,
                error_type="CdxRequestFailed",
                error_message="Wayback CDX query returned a non-JSON response.",
            )

        snapshots = self._parse_cdx_rows(rows)
        if not snapshots:
            # Archive absence is NEVER page absence: the page may have existed
            # the whole time without being captured. Record a coverage gap.
            return self._result(
                action,
                status="empty",
                negative_observations=[
                    f"no archive captures for {target_url} in {window_desc} "
                    "(archive absence is never page absence — the page may "
                    "simply not have been captured)."
                ],
            )

        snapshots.sort(key=lambda row: row["timestamp"])
        selected = _select_spread(snapshots, max_snapshots)

        # --- snapshot fetches --------------------------------------------------
        artifacts: list[RawArtifact] = []
        fetch_failures: list[str] = []
        for row in selected:
            wayback_url = SNAPSHOT_URL_TEMPLATE.format(
                timestamp=row["timestamp"], original_url=row["original"]
            )
            try:
                response = await context.http.get(wayback_url)
            except Exception as exc:  # noqa: BLE001 - one bad capture must not sink the rest
                fetch_failures.append(
                    f"snapshot {row['timestamp']} fetch failed: {type(exc).__name__}"
                )
                continue
            if response.status_code != 200:
                fetch_failures.append(
                    f"snapshot {row['timestamp']} returned HTTP {response.status_code}"
                )
                continue
            artifacts.append(self._artifact(action, row, wayback_url, response))

        if not artifacts:
            return self._result(
                action,
                status="failed_retryable",
                retryable=True,
                error_type="SnapshotFetchFailed",
                error_message=(
                    f"All {len(selected)} selected Wayback snapshots for "
                    f"{target_url} failed to fetch: " + "; ".join(fetch_failures)
                ),
            )

        negative_observations = [
            f"Wayback capture unavailable for {target_url}: {failure} "
            "(archive fetch failure, not page absence)."
            for failure in fetch_failures
        ]
        return self._result(
            action,
            status="partial" if fetch_failures else "success",
            artifacts=artifacts,
            negative_observations=negative_observations,
        )

    # ---- helpers --------------------------------------------------------------

    @staticmethod
    def _parse_cdx_rows(rows: Any) -> list[dict[str, str]]:
        """CDX json output -> list of {timestamp, original} dicts.

        The first row is a column header; an empty result is ``[]``.
        Rows with unparseable timestamps are dropped rather than guessed at.
        """
        if not isinstance(rows, list) or len(rows) < 2:
            return []
        header = rows[0]
        try:
            ts_index = header.index("timestamp")
            original_index = header.index("original")
        except (ValueError, AttributeError):
            return []
        parsed: list[dict[str, str]] = []
        for row in rows[1:]:
            if not isinstance(row, list) or len(row) <= max(ts_index, original_index):
                continue
            timestamp = str(row[ts_index])
            try:
                _parse_cdx_timestamp(timestamp)
            except ValueError:
                continue
            parsed.append({"timestamp": timestamp, "original": str(row[original_index])})
        return parsed

    def _artifact(
        self,
        action: ResearchAction,
        row: dict[str, str],
        wayback_url: str,
        response: Any,
    ) -> RawArtifact:
        raw_text = response.text or ""
        content_type = str(response.headers.get("content-type", "")).lower()
        looks_like_html = "html" in content_type or raw_text.lstrip()[:1] == "<"
        normalized = normalize_text(html_to_text(raw_text) if looks_like_html else raw_text)
        # The ACTUAL capture moment from the CDX index — never the requested
        # window boundary (§40.1 accuracy gate).
        capture_at = _parse_cdx_timestamp(row["timestamp"])
        truncated = bool(getattr(response, "extensions", {}).get("truncated", False))
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="wayback",
            source_name=action.source_name or self.name,
            url=wayback_url,
            final_url=wayback_url,
            title=self._extract_title(raw_text) if looks_like_html else None,
            published_at=capture_at,
            retrieved_at=utcnow(),
            time_window_ids=list(action.time_window_ids),
            raw_text=raw_text,
            normalized_text=normalized,
            content_hash=content_hash(raw_text),
            metadata={
                "original_url": row["original"],
                "cdx_timestamp": row["timestamp"],
                "wayback_url": wayback_url,
            },
            collection_method="wayback_snapshot",
            is_partial=truncated,
            archive_capture_at=capture_at,
        )

    @staticmethod
    def _extract_title(html: str) -> str | None:
        import re

        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        title = normalize_text(match.group(1))
        return title or None
