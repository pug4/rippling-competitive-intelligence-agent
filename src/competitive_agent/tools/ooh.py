"""Out-of-home (OOH) discovery adapter (blueprint §13, §36.9, §37.12).

Out-of-home advertising — billboards, transit wraps, subway/airport panels,
Times Square takeovers, digital OOH — has NO universal public registry. There
is no equivalent of an ad-library or a domain sitemap that enumerates a
company's OOH activity. The only public trace an OOH campaign leaves is
incidental: a press announcement, an agency portfolio page, an operator case
study, a trade-press write-up, an award entry, or a photo an employee or
passer-by posted. This adapter fans a set of §13.2 query families across the
Exa Search API and maps whatever surfaces into ``ooh`` artifacts.

HARD RULE (§13, §36.9): a lack of discovered OOH evidence is LOW COVERAGE, not
proof of no OOH activity. Every empty result is reported as a coverage gap with
that framing, never as an absence claim, because no registry exists to confirm
absence. No spend, reach, frequency, or flight-date data is publicly knowable
(``spend_known`` / ``reach_known`` / ``performance_known`` are always false).

Like :mod:`exa_search`, Exa is an authenticated provider API (x-api-key header)
called with a direct ``httpx.AsyncClient`` — NOT the SharedHttp public-fetch
pipeline — and the API key is never logged. A missing key yields ``unsupported``
so the report still renders when OOH discovery is unavailable.
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

# Network faults that should down-weight a single query rather than sink the
# whole fan-out (one flaky billboard query must not lose the other ten).
_QUERY_NETWORK_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.ConnectError,
)


def _parse_published(value: Any) -> datetime | None:
    """Parse Exa's ISO-8601 ``publishedDate``; None when absent/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_queries(company: str) -> list[tuple[str, str]]:
    """The §13.2 OOH query families for ``company`` as (family, query) pairs.

    The company name is phrase-quoted so hits reference the brand rather than
    the generic ad format; the two ``site:`` families target public LinkedIn
    and Instagram posts where employees/passers-by surface OOH creative.
    """
    quoted = f'"{company}"'
    return [
        ("billboard", f"{quoted} billboard"),
        ("out_of_home", f'{quoted} "out of home"'),
        ("ooh_dooh", f"{quoted} OOH OR DOOH"),
        ("subway_ad", f"{quoted} subway ad"),
        ("transit_campaign", f"{quoted} transit campaign"),
        ("airport_advertising", f"{quoted} airport advertising"),
        ("times_square", f"{quoted} Times Square"),
        ("outdoor_campaign", f"{quoted} outdoor campaign"),
        ("campaign_agency", f"{quoted} campaign agency"),
        ("linkedin_posts", f"site:linkedin.com/posts {quoted} billboard"),
        ("instagram", f"site:instagram.com {quoted} billboard"),
    ]


class OOHTool(BaseTool):
    """Out-of-home (OOH/DOOH) discovery via a fan-out of Exa searches."""

    name: ClassVar[str] = "ooh"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "ooh"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_ooh",)

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
                # HARD RULE (§13, §36.9): keep this first and verbatim in intent.
                "A lack of discovered OOH evidence must be reported as LOW "
                "COVERAGE, never as proof of no OOH activity: no universal OOH "
                "registry exists, so absence can never be confirmed.",
                "No spend, reach, frequency, or flight-date data is publicly "
                "knowable for OOH; discovery is qualitative only.",
                "OOH surfaces publicly only when someone announces, photographs, "
                "or writes about it (press, agency portfolios, operator case "
                "studies, award entries, employee/social posts), so coverage is "
                "inherently partial and skewed toward notable campaigns.",
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
                    "OOH discovery not attempted for "
                    f"'{action.action_type}': no Exa API key configured. This is "
                    "LOW COVERAGE, not evidence of no OOH activity."
                ],
            )

        company = str(action.parameters.get("company") or "").strip()
        if not company:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message="parameter 'company' is required for search_ooh actions",
            )

        num_results = _coerce_num_results(action.parameters.get("num_results"))
        queries = _build_queries(company)

        artifacts: list[RawArtifact] = []
        seen_urls: set[str] = set()
        failed_queries: list[str] = []
        successful_queries = 0
        cost_usd = 0.0

        async with self._build_client(api_key) as client:
            for family, query in queries:
                payload = {"query": query, "numResults": num_results, "contents": {"text": True}}
                try:
                    response = await self._post(client, payload)
                except _QUERY_NETWORK_EXCEPTIONS as exc:
                    # One transient query failure must not sink the fan-out.
                    failed_queries.append(f"{query!r} ({type(exc).__name__})")
                    continue

                # Auth / rate-limit faults are global to the key, so short-circuit
                # with a typed failure instead of hammering the remaining queries.
                if response.status_code in (401, 403):
                    return self._result(
                        action,
                        status="failed_terminal",
                        error_type="provider_auth",
                        error_message=(f"Exa rejected the API key (HTTP {response.status_code})."),
                        cost_usd=cost_usd,
                    )
                if response.status_code == 429:
                    return self._result(
                        action,
                        status="failed_retryable",
                        error_type="rate_limited",
                        error_message="Exa rate limit hit (HTTP 429).",
                        retryable=True,
                        cost_usd=cost_usd,
                    )
                if response.status_code >= 400:
                    # Per-query 5xx/4xx: record and keep going.
                    failed_queries.append(f"{query!r} (HTTP {response.status_code})")
                    continue

                try:
                    data = response.json()
                except Exception:  # noqa: BLE001 - malformed body is a per-query fault
                    failed_queries.append(f"{query!r} (invalid JSON)")
                    continue

                successful_queries += 1
                cost_usd += _extract_cost(data)
                for item in data.get("results") or []:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    artifacts.append(self._artifact(action, item, query, family))

        return self._finalize(
            action,
            company=company,
            artifacts=artifacts,
            queries=queries,
            successful_queries=successful_queries,
            failed_queries=failed_queries,
            cost_usd=cost_usd,
        )

    def _build_client(self, api_key: str) -> httpx.AsyncClient:
        """Configured provider client. Overridden in tests to inject a transport.

        Direct provider call by design: api.exa.ai requires the x-api-key header
        and is not subject to the public-URL fetch pipeline.
        """
        return httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key},
        )

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
        return await retry_async(
            lambda: client.post(EXA_SEARCH_URL, json=payload),
            retries=_MAX_RETRIES,
            base_delay=_RETRY_BASE_DELAY,
        )

    # ---- result assembly ---------------------------------------------------

    def _finalize(
        self,
        action: ResearchAction,
        *,
        company: str,
        artifacts: list[RawArtifact],
        queries: list[tuple[str, str]],
        successful_queries: int,
        failed_queries: list[str],
        cost_usd: float,
    ) -> ToolResult:
        # The coverage-gap framing every OOH outcome carries (§13, §36.9).
        low_coverage_note = (
            f"OOH discovery for '{company}' searched {len(queries)} §13.2 query "
            "families and surfaced no evidence. This is LOW COVERAGE, not proof of "
            "no OOH activity: no universal OOH registry exists, so billboards, "
            "transit, subway, and airport campaigns are only publicly discoverable "
            "when announced, photographed, or written about."
        )
        query_failure_notes = [
            f"OOH query {failure} failed to complete." for failure in failed_queries
        ]

        if artifacts:
            return self._result(
                action,
                status="partial" if failed_queries else "success",
                artifacts=artifacts,
                cost_usd=cost_usd,
                negative_observations=query_failure_notes,
            )

        if successful_queries == 0:
            # Every query errored: we could not actually search — retryable.
            return self._result(
                action,
                status="failed_retryable",
                error_type="provider_unavailable",
                error_message=(
                    f"All {len(queries)} Exa OOH queries for '{company}' failed: "
                    + "; ".join(failed_queries)
                ),
                retryable=True,
                cost_usd=cost_usd,
            )

        # We searched successfully and found nothing: a genuine coverage gap.
        return self._result(
            action,
            status="empty",
            cost_usd=cost_usd,
            negative_observations=[low_coverage_note, *query_failure_notes],
        )

    def _artifact(
        self,
        action: ResearchAction,
        item: dict[str, Any],
        query: str,
        family: str,
    ) -> RawArtifact:
        url = str(item.get("url"))
        raw_text = str(item.get("text") or "")
        metadata: dict[str, Any] = {
            # Provenance rule: the exact query that surfaced this artifact rides
            # on it, plus the honest "what OOH data is and isn't knowable" flags.
            "exa_query": query,
            "coverage": "low",
            "spend_known": False,
            "reach_known": False,
            "performance_known": False,
            "query_family": family,
            "exa_id": item.get("id"),
            "author": item.get("author"),
        }
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="ooh",
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
            collection_method="exa_ooh",
            # No page text back from the provider = discovery pointer only.
            is_partial=not raw_text,
        )


def _coerce_num_results(value: Any) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_NUM_RESULTS
    return num if num > 0 else _DEFAULT_NUM_RESULTS


def _extract_cost(data: dict[str, Any]) -> float:
    try:
        return float((data.get("costDollars") or {}).get("total") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
