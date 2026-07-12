"""Conferences / events discovery adapter (blueprint §14, §37.12).

Discovers a competitor's public event footprint — conference sponsorships,
exhibitor booths, speaking slots / keynotes, webinars, and customer-speaker
appearances — via the Exa Search API (POST https://api.exa.ai/search) with
page text enabled. Exa is an authenticated provider API, NOT a public-URL
fetch, so (mirroring ``exa_search.py``) it is called with a direct
``httpx.AsyncClient`` (x-api-key header, 20 s timeout, 2 retries on
5xx/timeout) instead of the ``SharedHttp`` public-fetch pipeline, and the API
key is never logged.

What is discoverable is only the PRESENCE of an event role, never its
economics: event spend, booth traffic, meetings booked, and sourced pipeline
remain unavailable unless publicly disclosed (blueprint §37.12). Sponsor and
event pages are also routinely taken down after the event, and private /
invite-only events leave no public trace — so "found nothing" is never
evidence of no activity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import httpx

from ..processing.normalize import content_hash, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext
from .http import retry_async

EXA_SEARCH_URL = "https://api.exa.ai/search"

_TIMEOUT_SECONDS = 20.0
_MAX_RETRIES = 2  # on 5xx / connect / timeout, inside retry_async
_RETRY_BASE_DELAY = 0.5

_DEFAULT_NUM_RESULTS = 8

# Event roles the public web can surface (blueprint §37.12). This is the space
# of what MIGHT be observable, stamped on every artifact so downstream
# extraction knows which presence types to look for — it is never a claim that
# any given role was found.
_PRESENCE_TYPES_POSSIBLE = [
    "sponsor",
    "exhibitor",
    "speaker",
    "host",
    "webinar",
    "customer_speaker",
]


def _parse_published(value: Any) -> datetime | None:
    """Parse Exa's ISO-8601 ``publishedDate``; None when absent/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_query(company: str) -> str:
    """Event-footprint query: presence roles OR'd across conference contexts."""
    return (
        f'"{company}" sponsor OR exhibitor OR speaker OR booth OR keynote '
        "OR webinar conference"
    )


class EventsTool(BaseTool):
    """Conference / event footprint discovery via the Exa Search API."""

    name: ClassVar[str] = "events"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "events"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_events",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=False,
            returns_estimates=False,
            known_limitations=[
                "Sponsor and event pages routinely disappear after the event, so "
                "coverage of past presence is partial and skewed toward recent events.",
                "Private / invite-only events leave no public trace; 'found nothing' "
                "is never evidence of no activity.",
                "Event spend, booth traffic, meetings booked, and sourced pipeline "
                "are NOT publicly knowable unless the company discloses them.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    # ---- live path ---------------------------------------------------------

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message="provider not configured: exa_api_key is not set",
                negative_observations=[
                    f"Events discovery not attempted for '{action.action_type}': "
                    "no Exa API key configured."
                ],
            )

        params: dict[str, Any] = action.parameters or {}
        company = str(params.get("company") or "").strip()
        if not company:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message="parameter 'company' is required for search_events actions",
            )

        query = _build_query(company)
        num_results = int(params.get("num_results") or _DEFAULT_NUM_RESULTS)
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "contents": {"text": True},
        }
        response = await self._post(payload, api_key)

        if response.status_code in (401, 403):
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_auth",
                error_message=f"Exa rejected the API key (HTTP {response.status_code}).",
            )
        if response.status_code == 429:
            return self._result(
                action,
                status="failed_retryable",
                error_type="rate_limited",
                error_message="Exa rate limit hit (HTTP 429).",
                retryable=True,
            )
        if response.status_code >= 500:
            return self._result(
                action,
                status="failed_retryable",
                error_type="provider_5xx",
                error_message=f"Exa server error (HTTP {response.status_code}) after retries.",
                retryable=True,
            )
        if response.status_code >= 400:
            return self._result(
                action,
                status="failed_terminal",
                error_type=f"provider_http_{response.status_code}",
                error_message=f"Exa returned HTTP {response.status_code}.",
            )

        try:
            data = response.json()
        except Exception as exc:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_response",
                error_message=f"Exa response was not valid JSON: {type(exc).__name__}",
            )

        return self._map_response(action, query, data)

    async def _post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        # Direct provider call by design: api.exa.ai requires the x-api-key
        # header and is not subject to the public-URL fetch pipeline.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key},
        ) as client:
            return await retry_async(
                lambda: client.post(EXA_SEARCH_URL, json=payload),
                retries=_MAX_RETRIES,
                base_delay=_RETRY_BASE_DELAY,
            )

    # ---- response mapping --------------------------------------------------

    def _map_response(
        self, action: ResearchAction, query: str, data: dict[str, Any]
    ) -> ToolResult:
        results = data.get("results") or []
        request_id = data.get("requestId")

        artifacts: list[RawArtifact] = []
        skipped = 0
        for item in results:
            if not isinstance(item, dict) or not str(item.get("url") or "").strip():
                skipped += 1
                continue
            artifacts.append(self._artifact(action, item, query, request_id))

        cost_usd = 0.0
        try:
            cost_usd = float((data.get("costDollars") or {}).get("total") or 0.0)
        except (TypeError, ValueError):
            cost_usd = 0.0

        if not artifacts:
            return self._result(
                action,
                status="empty",
                cost_usd=cost_usd,
                negative_observations=[
                    f"Events discovery returned no results for query '{query}' "
                    "(no public event footprint found; private events and taken-down "
                    "sponsor pages are invisible — this is not evidence of no activity)."
                ],
            )

        negative_observations: list[str] = []
        if skipped:
            negative_observations.append(
                f"{skipped} event result(s) without a URL were dropped for query '{query}'."
            )
        return self._result(
            action,
            status="partial" if skipped else "success",
            artifacts=artifacts,
            cost_usd=cost_usd,
            negative_observations=negative_observations,
        )

    def _artifact(
        self,
        action: ResearchAction,
        item: dict[str, Any],
        query: str,
        request_id: Any,
    ) -> RawArtifact:
        url = str(item.get("url"))
        raw_text = str(item.get("text") or "")
        metadata: dict[str, Any] = {
            # Provenance rule: the exact query rides on every artifact.
            "exa_query": query,
            # The observable role space, never a found-role claim.
            "presence_types_possible": list(_PRESENCE_TYPES_POSSIBLE),
            "exa_id": item.get("id"),
            "author": item.get("author"),
        }
        if request_id:
            metadata["exa_request_id"] = request_id
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="events",
            source_name=action.source_name or self.name,
            url=url,
            final_url=url,
            title=item.get("title") or None,
            published_at=_parse_published(item.get("publishedDate")),
            retrieved_at=utcnow(),
            time_window_ids=list(action.time_window_ids),
            raw_text=raw_text,
            normalized_text=normalize_text(raw_text),
            content_hash=content_hash(raw_text),
            metadata=metadata,
            collection_method="exa_events",
            # No page text back from the provider = discovery pointer only.
            is_partial=not raw_text,
        )
