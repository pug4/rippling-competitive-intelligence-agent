"""Public job-postings adapter (blueprint §37.12 'Jobs adapter').

Discovers a company's *public* job postings via the Exa Search API and turns
each hit into a :class:`RawArtifact`. Job openings are FORWARD-LOOKING signals:
they reveal where a competitor is *investing* — SDR/AE hiring (sales motion),
PLG/growth roles (self-serve bet), partner/channel roles (ecosystem push),
target-segment language, and geographic expansion. They are leading
indicators, NOT proof that a strategy is already executed: a posting is an
intent to build, and it may sit evergreen for months or be pulled unfilled.

Exa is an authenticated provider API, NOT a public-URL fetch: it is called
with a direct ``httpx.AsyncClient`` (x-api-key header, 20 s timeout, 2 retries
on 5xx/timeout) exactly like ``exa_search.py``, and the API key is never
logged. The search is scoped to the major public ATS/job hosts
(Greenhouse, Lever, LinkedIn Jobs) so results are genuinely public postings.
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

# Public ATS / job-board hosts scoped into the query so hits are genuinely
# public postings rather than generic marketing pages.
_JOB_SITE_SCOPE = "site:boards.greenhouse.io OR site:jobs.lever.co OR site:linkedin.com/jobs"


def _parse_published(value: Any) -> datetime | None:
    """Parse Exa's ISO-8601 ``publishedDate``; None when absent/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_jobs_query(company: str) -> str:
    """The exact Exa query for a company's public job postings (§37.12)."""
    return f'"{company}" careers OR jobs OR "we\'re hiring" {_JOB_SITE_SCOPE}'


class JobsTool(BaseTool):
    """Public job-posting discovery via the Exa Search API."""

    name: ClassVar[str] = "jobs"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "jobs"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_jobs",)

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
                "A job opening is a leading indicator, not proof that a strategy "
                "is implemented: a posting signals intent to invest, not an "
                "executed motion (§37.12).",
                "Only PUBLIC strategic signals are extractable — role function, "
                "geography, seniority, target-segment language, channel/motion "
                "hints, product focus, leadership investment; headcount, comp, "
                "and internal org design are not knowable.",
                "Postings may be evergreen or pulled unfilled; presence is not "
                "proof of a filled role, and absence is not proof of no hiring "
                "(the indexed subset of ATS/job hosts is never exhaustive).",
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
                    f"Job-posting search not attempted for '{action.action_type}': "
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
                error_message="parameter 'company' is required for search_jobs actions",
            )

        query = _build_jobs_query(company)
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

    def _map_response(self, action: ResearchAction, query: str, data: dict[str, Any]) -> ToolResult:
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
            # Empty is a finding, not a failure: no indexed public posting was
            # found for the query — never evidence the company is not hiring.
            return self._result(
                action,
                status="empty",
                cost_usd=cost_usd,
                negative_observations=[
                    f"Exa job-posting search returned no results for query '{query}' "
                    "(no indexed public posting found — not evidence of no hiring)."
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
        request_id: Any,
    ) -> RawArtifact:
        url = str(item.get("url"))
        raw_text = str(item.get("text") or "")
        metadata: dict[str, Any] = {
            # Provenance rule: the exact query rides on every artifact.
            "exa_query": query,
            # Jobs are forward-looking: mark the signal class so downstream
            # synthesis never treats a posting as an executed strategy.
            "signal_type": "leading_indicator",
            "exa_id": item.get("id"),
            "author": item.get("author"),
        }
        if request_id:
            metadata["exa_request_id"] = request_id
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="jobs",
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
            collection_method="exa_jobs",
            # No page text back from the provider = discovery pointer only.
            is_partial=not raw_text,
        )
