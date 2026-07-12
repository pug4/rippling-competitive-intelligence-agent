"""Exa public-web discovery adapter (blueprint §27.1, §37.12).

Calls the Exa Search API (POST https://api.exa.ai/search) with page text
enabled so every hit arrives with provider-extracted content. Exa is an
authenticated provider API, NOT a public-URL fetch: it is deliberately
called with a direct ``httpx.AsyncClient`` (x-api-key header, 20 s timeout,
2 retries on 5xx/timeout) instead of the ``SharedHttp`` public-fetch
pipeline, and the API key is never logged.

Request/response field names verified against the official docs on
2026-07-11 — see ``docs/provider_notes.md``.
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

# action_type -> artifact source_type (provenance is honest about intent).
_SOURCE_TYPE_BY_ACTION: dict[str, str] = {
    "search_exa_web": "exa_web",
    "search_news_launches": "news",
    "search_comparison_pages": "comparison",
    # LinkedIn-scoped Exa search: each result (post/profile) becomes its own
    # artifact with its URL + Exa-extracted text (a complementary discovery path
    # to the Exa Agent's structured per-post research).
    "search_linkedin_posts": "linkedin_post",
}


def _parse_published(value: Any) -> datetime | None:
    """Parse Exa's ISO-8601 ``publishedDate``; None when absent/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ExaSearchTool(BaseTool):
    """Public-web discovery via the Exa Search API."""

    name: ClassVar[str] = "exa_search"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "exa_web"

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(_SOURCE_TYPE_BY_ACTION),
            supports_date_filters=True,
            supports_historical_data=False,
            supports_exact_content=False,
            returns_estimates=False,
            known_limitations=[
                "Result text is provider-extracted and may be partial; fetch the "
                "page first-party before quoting exact content.",
                "Generic search snippets must never outrank complete first-party "
                "pages that support the same claim (§37.12).",
                "Neural search recall is query-dependent; no results is not "
                "evidence of no activity.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in _SOURCE_TYPE_BY_ACTION

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
                    f"Exa search not attempted for '{action.action_type}': no API key configured."
                ],
            )

        query = str(action.parameters.get("query") or "").strip()
        if not query:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message="parameter 'query' is required for Exa search actions",
            )

        payload, filters = self._build_payload(action, query)
        response = await self._post(payload, api_key)

        if response.status_code in (401, 403):
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_auth",
                error_message=f"Exa rejected the API key (HTTP {response.status_code}).",
            )
        if response.status_code == 402:
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_out_of_credits",
                error_message="Exa is out of credits (HTTP 402) — top up the Exa key to enable Exa sources.",
                negative_observations=[
                    f"Exa '{action.action_type}' not collected: Exa key is out of credits (402)."
                ],
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

        return self._map_response(action, query, filters, data)

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

    # ---- request / response mapping -----------------------------------------

    def _build_payload(
        self, action: ResearchAction, query: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the Exa request body plus the exact filters for provenance."""
        params = action.parameters
        num_results = int(params.get("num_results") or _DEFAULT_NUM_RESULTS)
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "contents": {"text": True},
        }
        filters: dict[str, Any] = {"numResults": num_results}

        include_domains = params.get("include_domains")
        if include_domains:
            payload["includeDomains"] = list(include_domains)
            filters["includeDomains"] = list(include_domains)
        exclude_domains = params.get("exclude_domains")
        if exclude_domains:
            payload["excludeDomains"] = list(exclude_domains)
            filters["excludeDomains"] = list(exclude_domains)
        start_published_date = params.get("start_published_date")
        if start_published_date:
            payload["startPublishedDate"] = str(start_published_date)
            filters["startPublishedDate"] = str(start_published_date)
        # Both bounds together let the planner sample a PRIOR time window from
        # Exa's index — real published-in-window evidence beyond Wayback's
        # sparse archive (retrieval only; classification stays in Claude).
        end_published_date = params.get("end_published_date")
        if end_published_date:
            payload["endPublishedDate"] = str(end_published_date)
            filters["endPublishedDate"] = str(end_published_date)
        category = params.get("category")
        if category:
            payload["category"] = str(category)
            filters["category"] = str(category)
        # Multi-angle sweep in ONE call (retrieval breadth, up to 10 variants).
        additional = params.get("additional_queries")
        if additional:
            payload["additionalQueries"] = [str(q) for q in list(additional)[:10]]
            filters["additionalQueries"] = payload["additionalQueries"]
        return payload, filters

    def _map_response(
        self,
        action: ResearchAction,
        query: str,
        filters: dict[str, Any],
        data: dict[str, Any],
    ) -> ToolResult:
        results = data.get("results") or []
        request_id = data.get("requestId")
        source_type = _SOURCE_TYPE_BY_ACTION[action.action_type]

        artifacts: list[RawArtifact] = []
        skipped = 0
        for item in results:
            if not isinstance(item, dict) or not str(item.get("url") or "").strip():
                skipped += 1
                continue
            artifacts.append(self._artifact(action, item, source_type, query, filters, request_id))

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
                    f"Exa search returned no results for query '{query}' "
                    f"(action '{action.action_type}', filters={filters})."
                ],
            )

        negative_observations: list[str] = []
        if skipped:
            negative_observations.append(
                f"{skipped} Exa result(s) without a URL were dropped for query '{query}'."
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
        source_type: str,
        query: str,
        filters: dict[str, Any],
        request_id: Any,
    ) -> RawArtifact:
        url = str(item.get("url"))
        raw_text = str(item.get("text") or "")
        metadata: dict[str, Any] = {
            # Provenance rule: the exact query + filters ride on every artifact.
            "exa_query": query,
            "exa_filters": filters,
            "exa_id": item.get("id"),
            "score": item.get("score"),  # not in current docs; kept when present
            "author": item.get("author"),
        }
        if request_id:
            metadata["exa_request_id"] = request_id
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type=source_type,
            source_name=self.name,
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
            collection_method="exa_search",
            # No page text back from the provider = discovery pointer only.
            is_partial=not raw_text,
        )
