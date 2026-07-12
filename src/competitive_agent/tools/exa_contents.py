"""Exa /contents adapter — fetch SPECIFIC URLs through Exa's crawler.

Pure retrieval (Exa scrapes, Claude reasons): solves what the static first-party
fetcher can't — JS-rendered pages and anti-bot HTTP failures — by pulling page
text from Exa's index/crawler with freshness control. Proposed by the planner as
a FALLBACK for priority pages whose direct fetch failed with an HTTP error.
Robots-disallowed first-party pages are NOT routed here: we respect robots on
competitor sites ourselves; this is for fetch failures, not policy bypass.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from ..processing.normalize import content_hash, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext

EXA_CONTENTS_URL = "https://api.exa.ai/contents"
_TIMEOUT = 40.0
_MAX_URLS = 10


class ExaContentsTool(BaseTool):
    """Fetch explicit URLs via Exa's crawler (fresh or cached page text)."""

    name: ClassVar[str] = "exa_contents"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "exa_web"  # rides the Exa feature flag
    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("fetch_via_exa",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=True,
            returns_estimates=False,
            known_limitations=[
                "Text comes from Exa's crawler/index, not a first-party fetch — "
                "provenance is labeled collection_method=exa_contents.",
                "Freshness depends on maxAgeHours; 0 forces a live crawl (slower).",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            return self._result(
                action, status="unsupported", error_type="provider_not_configured",
                error_message="provider not configured: exa_api_key is not set",
            )
        urls = [str(u) for u in (action.parameters.get("urls") or []) if u][:_MAX_URLS]
        if not urls:
            return self._result(
                action, status="failed_terminal", error_type="invalid_parameters",
                error_message="parameter 'urls' is required for fetch_via_exa",
            )
        body: dict[str, Any] = {"urls": urls, "text": True}
        max_age = action.parameters.get("max_age_hours")
        if max_age is not None:
            body["maxAgeHours"] = int(max_age)
        subpages = action.parameters.get("subpages")
        if subpages:
            body["subpages"] = int(subpages)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT)) as client:
                resp = await client.post(
                    EXA_CONTENTS_URL, json=body,
                    headers={"x-api-key": api_key, "Content-Type": "application/json"},
                )
        except (httpx.HTTPError, TimeoutError) as exc:
            return self._result(action, status="failed_retryable", error_type=type(exc).__name__,
                                error_message=f"Exa contents request failed: {type(exc).__name__}",
                                retryable=True)
        if resp.status_code == 402:
            return self._result(action, status="failed_terminal",
                                error_type="provider_out_of_credits",
                                error_message="Exa is out of credits (HTTP 402).")
        if resp.status_code in (401, 403):
            return self._result(action, status="failed_terminal", error_type="provider_auth",
                                error_message=f"Exa rejected the key (HTTP {resp.status_code}).")
        if resp.status_code >= 400:
            return self._result(action, status="failed_terminal",
                                error_type=f"provider_http_{resp.status_code}",
                                error_message=f"Exa contents HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._result(action, status="failed_terminal", error_type="invalid_response",
                                error_message=f"Exa contents response not JSON: {type(exc).__name__}")

        artifacts: list[RawArtifact] = []
        negatives: list[str] = []
        for item in data.get("results", []) or []:
            url = str(item.get("url") or "")
            text = str(item.get("text") or "")
            if not url or not text:
                negatives.append(f"{url or '?'}: Exa returned no text")
                continue
            artifacts.append(
                RawArtifact(
                    artifact_id=new_id("ART"),
                    company_id=action.company_id,
                    source_type=str(action.parameters.get("source_type") or "webpage"),
                    source_name=self.name,
                    url=url,
                    final_url=url,
                    title=item.get("title") or None,
                    author=item.get("author") or None,
                    retrieved_at=utcnow(),
                    time_window_ids=list(action.time_window_ids),
                    raw_text=text,
                    normalized_text=normalize_text(text),
                    content_hash=content_hash(text),
                    metadata={"collected_via": "exa_contents", "exa_id": item.get("id")},
                    collection_method="exa_contents",
                    is_partial=False,
                )
            )
        missing = len(urls) - len(artifacts)
        cost = float((data.get("costDollars") or {}).get("total", 0.0))
        if not artifacts:
            return self._result(action, status="empty", cost_usd=cost,
                                negative_observations=negatives or ["Exa contents returned nothing."])
        return self._result(
            action,
            status="partial" if missing else "success",
            artifacts=artifacts,
            cost_usd=cost,
            negative_observations=negatives,
        )
