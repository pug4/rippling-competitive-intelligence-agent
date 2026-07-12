"""Reviews / buyer-language discovery adapter (blueprint §21, §37.12).

Surfaces third-party review pages and buyer discussions (G2, Capterra,
TrustRadius) via the Exa Search API so downstream synthesis can mine
BUYER LANGUAGE and OBJECTIONS: repeated cons, implementation pains, support
pains, and comparative alternatives.

CRITICAL FRAMING: reviews are a selection-biased, non-representative sample.
The self-selected reviewers who post publicly are not a representative
draw from a company's customer base. This adapter is for pain language and
objection mining ONLY — never for representative market statistics or
counts presented as measures of overall sentiment (blueprint §21 caveat).
That caveat rides on every artifact (``representativeness`` metadata) and
is declared in the tool capabilities' ``known_limitations``.

Like :mod:`exa_search`, this is a direct authenticated provider call to
``api.exa.ai`` (x-api-key header), NOT the ``SharedHttp`` public-fetch
pipeline; the API key is never logged.
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

# Review destinations whose pages carry the buyer language we want to mine.
_REVIEW_DOMAINS: tuple[str, ...] = ("g2.com", "capterra.com", "trustradius.com")


def _parse_published(value: Any) -> datetime | None:
    """Parse Exa's ISO-8601 ``publishedDate``; None when absent/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ReviewsTool(BaseTool):
    """Buyer-language / objection discovery via review sites (Exa-backed)."""

    name: ClassVar[str] = "reviews"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "reviews"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_reviews",)

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
                "Reviews are a self-selected, selection-biased sample: public "
                "reviewers are not a representative draw from the customer base. "
                "Use for PAIN LANGUAGE and objection mining, NEVER as a "
                "representative market statistic or overall-sentiment measure "
                "(blueprint §21).",
                "Review counts and star ratings must not be reported as measures "
                "of market sentiment without explicit sampling caveats.",
                "Result text is provider-extracted and may be partial; fetch the "
                "review page first-party before quoting exact buyer wording.",
                "Neural search recall is query-dependent; no results is not "
                "evidence that a company has no reviews or no complaints.",
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
                    f"Reviews discovery not attempted for '{action.action_type}': "
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
                error_message="parameter 'company' is required for search_reviews actions",
            )

        query = self._build_query(company)
        payload, filters = self._build_payload(params, query)
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

        return self._map_response(action, company, query, filters, data)

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

    @staticmethod
    def _build_query(company: str) -> str:
        """Buyer-language query biased toward pros/cons on the review sites."""
        return f'"{company}" review OR "cons" OR "pros" G2 OR Capterra OR TrustRadius'

    def _build_payload(
        self, params: dict[str, Any], query: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the Exa request body plus the exact filters for provenance."""
        num_results = int(params.get("num_results") or _DEFAULT_NUM_RESULTS)
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "contents": {"text": True},
        }
        filters: dict[str, Any] = {"numResults": num_results}

        # Scope to the review destinations unless the caller overrides. An
        # explicit include_domains wins; otherwise default to the review sites
        # unless include_review_domains is turned off (e.g. to catch buyer
        # discussion on forums/Reddit that isn't on G2/Capterra/TrustRadius).
        include_domains = params.get("include_domains")
        if include_domains:
            domains = list(include_domains)
        elif params.get("include_review_domains", True):
            domains = list(_REVIEW_DOMAINS)
        else:
            domains = []
        if domains:
            payload["includeDomains"] = domains
            filters["includeDomains"] = domains

        exclude_domains = params.get("exclude_domains")
        if exclude_domains:
            payload["excludeDomains"] = list(exclude_domains)
            filters["excludeDomains"] = list(exclude_domains)
        return payload, filters

    def _map_response(
        self,
        action: ResearchAction,
        company: str,
        query: str,
        filters: dict[str, Any],
        data: dict[str, Any],
    ) -> ToolResult:
        results = data.get("results") or []
        request_id = data.get("requestId")

        artifacts: list[RawArtifact] = []
        skipped = 0
        for item in results:
            if not isinstance(item, dict) or not str(item.get("url") or "").strip():
                skipped += 1
                continue
            artifacts.append(self._artifact(action, item, query, filters, request_id))

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
                    f"Exa reviews discovery returned no results for '{company}' "
                    f"(query '{query}', filters={filters}). No public reviews were "
                    "found — this is not evidence that the company has none."
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
            # Framing that MUST travel with the evidence downstream.
            "source_perspective": "review_site",
            "representativeness": "non_representative_selection_biased",
            "exa_id": item.get("id"),
            "score": item.get("score"),  # not in current docs; kept when present
            "author": item.get("author"),
        }
        if request_id:
            metadata["exa_request_id"] = request_id
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="reviews",
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
            collection_method="exa_reviews",
            # No page text back from the provider = discovery pointer only.
            is_partial=not raw_text,
        )
