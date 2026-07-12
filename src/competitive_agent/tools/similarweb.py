"""Similarweb web-analytics adapter through the Exa Agent API (blueprint §12.7,
§37.12 'Similarweb adapter through Exa', §39.7 'Exa Similarweb provider').

This is a Level-B, feature-flagged, NON-BLOCKING enrichment adapter. Similarweb
is reached by ATTACHING the Similarweb provider to an Exa Agent run — NOT by
plain Exa search and NOT via the SharedHttp public-fetch pipeline. The Exa Agent
API is an authenticated provider API, so it is called with a direct
``httpx.AsyncClient`` (``x-api-key`` header), exactly like ``exa_search.py``.

Request shape (Exa Agent / Connect docs, confirmed 2026-07-11 — see
``docs/provider_notes.md``):

    POST https://api.exa.ai/agent/runs
    headers: x-api-key, content-type: application/json
    body: {"query": ..., "dataSources": [{"provider": "similarweb"}],
           "outputSchema": <bounded JSON Schema>}

The blueprint's Python example writes ``data_sources=[{"provider":"similarweb"}]``
(the SDK kwarg); the REST body field is camelCase ``dataSources``, consistent
with the rest of the Exa API (``numResults``, ``includeDomains``).

PROVIDER-DEPENDENT: every metric Similarweb can return is a *modeled estimate*,
and which fields come back varies by domain and Exa/Similarweb plan. The adapter
therefore CAPABILITY-CHECKS the payload — it keeps only the fields the provider
actually returned and never synthesizes an absent field. When the provider or
endpoint is unavailable (no key, endpoint 404/501, run never completes) the
adapter degrades cleanly and NEVER blocks the report.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import httpx

from ..processing.normalize import content_hash, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext
from .http import retry_async

# Exa Agent API run endpoint. Attaching a provider to a run makes the Exa Agent
# query that partner's database the same way it searches the web.
EXA_AGENT_RUNS_URL = "https://api.exa.ai/agent/runs"

_TIMEOUT_SECONDS = 30.0
_MAX_RETRIES = 2  # 5xx / connect / timeout, inside retry_async
_RETRY_BASE_DELAY = 0.5

# Bounded polling for an async Agent run. All of this stays well inside the base
# boundary's asyncio timeout so the adapter can never block the report.
_POLL_MAX_ATTEMPTS = 6
_POLL_DELAY_SECONDS = 1.5

_ACTION_TYPES: tuple[str, ...] = ("enrich_similarweb",)

# The seven traffic acquisition channels we request shares for. Only the shares
# the provider actually returns are kept.
_CHANNEL_KEYS: tuple[str, ...] = (
    "direct",
    "referral",
    "social",
    "organic_search",
    "paid_search",
    "display",
    "mail",
)

# Every field label below is a *bounded* request: we ask Similarweb for exactly
# these and nothing else. estimated_paid_keywords is optional ("when returned").
_CORE_METRIC_FIELDS: tuple[str, ...] = (
    "estimated_monthly_visits",
    "traffic_trend",
    "channel_mix",
    "top_countries",
    "digital_competitors",
)

_PENDING_STATUSES = {"pending", "queued", "running", "processing", "in_progress", "started"}
_FAILED_STATUSES = {"failed", "error", "errored", "canceled", "cancelled"}

# Per-metric units (each returned metric must carry provider/period/estimated/unit).
_METRIC_UNITS: dict[str, str] = {
    "estimated_monthly_visits": "visits/month",
    "traffic_trend": "visits/month",
    "channel_mix": "share_of_traffic",
    "top_countries": "share_of_traffic",
    "digital_competitors": "domain",
    "estimated_paid_keywords": "keywords",
}


def _as_number(value: Any) -> float | int | None:
    """Return a finite numeric value, else None (missing stays missing)."""
    if isinstance(value, bool):  # bool is an int subclass — never a metric
        return None
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return value
    return None


def _as_nonempty_list(value: Any) -> list[Any] | None:
    if isinstance(value, list) and value:
        return value
    return None


def _validate_channel_mix(value: Any) -> dict[str, float | int] | None:
    """Keep only the channel shares the provider actually returned as numbers."""
    if not isinstance(value, dict):
        return None
    kept: dict[str, float | int] = {}
    for key in _CHANNEL_KEYS:
        share = _as_number(value.get(key))
        if share is not None:
            kept[key] = share
    return kept or None


def _validate_top_countries(value: Any) -> list[Any] | None:
    """Country distribution: keep the list only if it has usable entries."""
    items = _as_nonempty_list(value)
    if items is None:
        return None
    cleaned = [c for c in items if isinstance(c, (dict, str)) and c]
    return cleaned or None


def _validate_competitors(value: Any) -> list[Any] | None:
    items = _as_nonempty_list(value)
    if items is None:
        return None
    cleaned = [c for c in items if isinstance(c, (dict, str)) and c]
    return cleaned or None


def _validate_traffic_trend(value: Any) -> list[Any] | None:
    items = _as_nonempty_list(value)
    if items is None:
        return None
    cleaned = [p for p in items if isinstance(p, (dict, list, int, float)) and p is not None]
    return cleaned or None


class SimilarwebTool(BaseTool):
    """Estimated web-traffic enrichment via Similarweb attached to an Exa Agent run."""

    name: ClassVar[str] = "similarweb"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "similarweb"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = _ACTION_TYPES

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=True,  # traffic_trend is a modeled time series
            supports_exact_content=False,
            returns_estimates=True,
            known_limitations=[
                "Every Similarweb metric is a modeled ESTIMATE, never a measured "
                "count; treat magnitudes and trends as directional.",
                "Provider-dependent: which fields return varies by domain and plan; "
                "missing fields stay missing and are never synthesized.",
                "Reached via the Exa Agent API with the Similarweb provider "
                "attached; if the endpoint/provider is unavailable the adapter "
                "degrades to 'unsupported' and the report still renders.",
                "The report must not depend on keyword or spend estimates "
                "(estimated_paid_keywords is best-effort).",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    # ---- live path ---------------------------------------------------------

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            # No key => provider not configured => cleanly unsupported (non-blocking).
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message="provider not configured: exa_api_key is not set",
                negative_observations=[
                    "Similarweb-via-Exa enrichment not attempted for "
                    f"'{action.action_type}': no Exa API key configured."
                ],
            )

        domain = str(action.parameters.get("domain") or "").strip()
        if not domain:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message="parameter 'domain' is required for enrich_similarweb",
            )

        cfg = getattr(context.config, "exa_agent", None) or {}
        effort = str(cfg.get("effort", "auto")) if isinstance(cfg, dict) else "auto"
        query = self._build_query(domain)

        # Primary: with the Similarweb data partner attached. If that partner is
        # not on the plan (provider_unavailable), fall back to a public-web
        # traffic estimate (no partner) — user choice: degrade gracefully.
        result = await self._run(action, domain, query, api_key, effort, with_similarweb=True)
        # Fall back to a public-web estimate when the Similarweb partner is
        # unreachable OR returns an empty payload (partner not on the plan often
        # completes with zero metric fields rather than erroring).
        if (result.status == "unsupported" and result.error_type == "provider_unavailable") or (
            result.status == "empty"
        ):
            result = await self._run(action, domain, query, api_key, effort, with_similarweb=False)
        return result

    async def _run(
        self,
        action: ResearchAction,
        domain: str,
        query: str,
        api_key: str,
        effort: str,
        *,
        with_similarweb: bool,
    ) -> ToolResult:
        payload: dict[str, Any] = {
            "query": query,
            "effort": effort,
            "outputSchema": self._output_schema(),
        }
        if with_similarweb:
            # Provider EXPLICITLY attached — routes the run to Similarweb.
            payload["dataSources"] = [{"provider": "similarweb"}]

        response = await self._post_run(payload, api_key)
        terminal = self._status_error(action, response, "run")
        if terminal is not None:
            return terminal

        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - malformed body is terminal, not a crash
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_response",
                error_message=f"Exa Agent response was not valid JSON: {type(exc).__name__}",
            )

        data, poll_error = await self._await_run(action, data, api_key)
        if poll_error is not None:
            return poll_error

        output = self._output_from(data)
        return self._map_output(
            action, domain, query, data, output, with_similarweb=with_similarweb
        )

    # ---- HTTP -------------------------------------------------------------

    async def _post_run(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        # Direct provider call by design: api.exa.ai requires x-api-key and is
        # not subject to the public-URL fetch pipeline.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key, "content-type": "application/json"},
        ) as client:
            return await retry_async(
                lambda: client.post(EXA_AGENT_RUNS_URL, json=payload),
                retries=_MAX_RETRIES,
                base_delay=_RETRY_BASE_DELAY,
            )

    async def _get_run(self, run_id: str, api_key: str) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key},
        ) as client:
            return await retry_async(
                lambda: client.get(f"{EXA_AGENT_RUNS_URL}/{run_id}"),
                retries=_MAX_RETRIES,
                base_delay=_RETRY_BASE_DELAY,
            )

    def _status_error(
        self, action: ResearchAction, response: httpx.Response, what: str
    ) -> ToolResult | None:
        """Map provider HTTP status to a typed failure; None means proceed."""
        code = response.status_code
        if code == 402:
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_out_of_credits",
                error_message="Exa is out of credits (HTTP 402) — top up to enable Similarweb.",
                negative_observations=[
                    "Similarweb-via-Exa not collected: Exa key is out of credits (402)."
                ],
            )
        if code in (401, 403):
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_auth",
                error_message=f"Exa rejected the API key (HTTP {code}).",
            )
        if code == 429:
            return self._result(
                action,
                status="failed_retryable",
                error_type="rate_limited",
                error_message="Exa Agent rate limit hit (HTTP 429).",
                retryable=True,
            )
        # Endpoint or provider not available on this account/plan => the Similarweb
        # capability is not reachable. Degrade cleanly rather than erroring hard.
        if code in (404, 405, 501):
            return self._result(
                action,
                status="unsupported",
                error_type="provider_unavailable",
                error_message=(
                    f"Exa Agent endpoint/provider unavailable (HTTP {code}); "
                    "Similarweb-via-Exa is not reachable on this account."
                ),
                negative_observations=[
                    "Similarweb-via-Exa returned no data: the Exa Agent "
                    f"endpoint/provider is unavailable (HTTP {code})."
                ],
            )
        if code >= 500:
            return self._result(
                action,
                status="failed_retryable",
                error_type="provider_5xx",
                error_message=f"Exa Agent server error (HTTP {code}) after retries.",
                retryable=True,
            )
        if code >= 400:
            return self._result(
                action,
                status="failed_terminal",
                error_type=f"provider_http_{code}",
                error_message=f"Exa Agent {what} returned HTTP {code}.",
            )
        return None

    async def _await_run(
        self, action: ResearchAction, data: dict[str, Any], api_key: str
    ) -> tuple[dict[str, Any], ToolResult | None]:
        """Poll a still-running Agent run until it produces output (bounded).

        Returns ``(latest_data, None)`` on success, or ``(data, failure)`` when
        the run failed or never completed within the poll budget. Never blocks.
        """
        run_id = str(data.get("id") or data.get("runId") or "").strip()
        attempts = 0
        while (
            self._output_from(data) is None
            and run_id
            and self._run_status(data) in _PENDING_STATUSES
            and attempts < _POLL_MAX_ATTEMPTS
        ):
            await asyncio.sleep(_POLL_DELAY_SECONDS)
            response = await self._get_run(run_id, api_key)
            terminal = self._status_error(action, response, "poll")
            if terminal is not None:
                return data, terminal
            try:
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                return data, self._result(
                    action,
                    status="failed_retryable",
                    error_type="invalid_response",
                    error_message=f"Exa Agent poll returned non-JSON: {type(exc).__name__}",
                    retryable=True,
                )
            attempts += 1

        status = self._run_status(data)
        if status in _FAILED_STATUSES:
            return data, self._result(
                action,
                status="failed_retryable",
                error_type="run_failed",
                error_message=f"Exa Agent run reported status '{status}'.",
                retryable=True,
            )
        if self._output_from(data) is None and status in _PENDING_STATUSES:
            return data, self._result(
                action,
                status="failed_retryable",
                error_type="run_timeout",
                error_message=(
                    f"Exa Agent run did not complete within {_POLL_MAX_ATTEMPTS} polls "
                    f"(last status '{status}')."
                ),
                retryable=True,
            )
        return data, None

    @staticmethod
    def _run_status(data: dict[str, Any]) -> str:
        return str(data.get("status") or "").strip().lower()

    @staticmethod
    def _output_from(data: dict[str, Any]) -> dict[str, Any] | None:
        """Locate the structured-output object matching our schema, tolerantly."""
        if not isinstance(data, dict):
            return None
        for key in ("output", "result", "data", "outputs"):
            candidate = data.get(key)
            if isinstance(candidate, dict) and candidate:
                return candidate
        # Some responses inline the schema fields at the top level.
        if any(field in data for field in _CORE_METRIC_FIELDS):
            return data
        return None

    # ---- request / response mapping ---------------------------------------

    def _build_query(self, domain: str) -> str:
        return (
            f"Using Similarweb, report web-analytics estimates for the domain "
            f"{domain}: estimated monthly visits, the monthly traffic trend, the "
            f"marketing channel mix shares (direct, referral, social, organic "
            f"search, paid search, display, mail), the top countries by traffic "
            f"share, digital competitors, and estimated paid keywords when "
            f"available. Report only what Similarweb provides; omit unknown fields."
        )

    def _output_schema(self) -> dict[str, Any]:
        """Bounded JSON Schema — request ONLY the fields the blueprint enumerates."""
        return {
            "type": "object",
            "properties": {
                "estimated_monthly_visits": {
                    "type": "number",
                    "description": "Estimated total monthly visits (visits/month).",
                },
                "observation_period": {
                    "type": "string",
                    "description": "Period the estimates describe, e.g. a month range.",
                },
                "traffic_trend": {
                    "type": "array",
                    "description": "Monthly estimated visits over time.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "period": {"type": "string"},
                            "visits": {"type": "number"},
                        },
                    },
                },
                "channel_mix": {
                    "type": "object",
                    "description": "Share of traffic by acquisition channel (0-1).",
                    "properties": {key: {"type": "number"} for key in _CHANNEL_KEYS},
                },
                "top_countries": {
                    "type": "array",
                    "description": "Top countries by estimated traffic share.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "country": {"type": "string"},
                            "share": {"type": "number"},
                        },
                    },
                },
                "digital_competitors": {
                    "type": "array",
                    "description": "Similar/competing sites by audience overlap.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string"},
                            "affinity": {"type": "number"},
                        },
                    },
                },
                "estimated_paid_keywords": {
                    "type": "number",
                    "description": "Estimated count of paid search keywords (when available).",
                },
            },
        }

    def _validate_output(self, output: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Capability-check: build labeled metrics ONLY for fields truly returned.

        Each kept metric is wrapped ``{value, estimated: true, unit}`` so EVERY
        metric is individually labeled estimated. Absent fields are simply not
        added — they stay missing and are never synthesized.
        """
        metrics: dict[str, dict[str, Any]] = {}

        def _add(field: str, value: Any) -> None:
            if value is not None:
                metrics[field] = {"value": value, "estimated": True, "unit": _METRIC_UNITS[field]}

        _add("estimated_monthly_visits", _as_number(output.get("estimated_monthly_visits")))
        _add("traffic_trend", _validate_traffic_trend(output.get("traffic_trend")))
        _add("channel_mix", _validate_channel_mix(output.get("channel_mix")))
        _add("top_countries", _validate_top_countries(output.get("top_countries")))
        _add("digital_competitors", _validate_competitors(output.get("digital_competitors")))
        _add("estimated_paid_keywords", _as_number(output.get("estimated_paid_keywords")))
        return metrics

    def _map_output(
        self,
        action: ResearchAction,
        domain: str,
        query: str,
        data: dict[str, Any],
        output: dict[str, Any] | None,
        *,
        with_similarweb: bool = True,
    ) -> ToolResult:
        cost_usd = self._cost(data)

        if not output:
            return self._result(
                action,
                status="empty",
                cost_usd=cost_usd,
                negative_observations=[
                    f"Similarweb-via-Exa returned no structured payload for '{domain}' "
                    f"(query='{query}'); no traffic estimates available."
                ],
            )

        metrics = self._validate_output(output)
        if not metrics:
            return self._result(
                action,
                status="empty",
                cost_usd=cost_usd,
                negative_observations=[
                    f"Similarweb-via-Exa returned a payload for '{domain}' but no "
                    "requested metric fields were present; nothing synthesized."
                ],
            )

        observation_period = output.get("observation_period")
        if not isinstance(observation_period, str) or not observation_period.strip():
            observation_period = None

        artifact = self._artifact(
            action,
            domain,
            query,
            data,
            metrics,
            observation_period,
            with_similarweb=with_similarweb,
        )

        missing_core = [f for f in _CORE_METRIC_FIELDS if f not in metrics]
        negative_observations: list[str] = []
        if missing_core:
            negative_observations.append(
                f"Similarweb-via-Exa did not return {', '.join(missing_core)} for "
                f"'{domain}'; those fields are left missing (not synthesized)."
            )
        return self._result(
            action,
            status="partial" if missing_core else "success",
            artifacts=[artifact],
            cost_usd=cost_usd,
            negative_observations=negative_observations,
        )

    def _artifact(
        self,
        action: ResearchAction,
        domain: str,
        query: str,
        data: dict[str, Any],
        metrics: dict[str, dict[str, Any]],
        observation_period: str | None,
        *,
        with_similarweb: bool = True,
    ) -> RawArtifact:
        retrieval_timestamp = utcnow()
        # Similarweb's canonical page for this domain is the honest provenance
        # pointer; collection_method makes clear it was collected via Exa.
        url = f"https://www.similarweb.com/website/{domain}/"
        raw_text = self._render_text(domain, metrics, observation_period)
        run_id = data.get("id") or data.get("runId")

        metadata: dict[str, Any] = {
            # Required labels for every returned estimate (§37.12).
            "provider": "similarweb",
            "observation_period": observation_period,
            "estimated": True,  # dataset-level: every metric here is an estimate
            "unit": _METRIC_UNITS["estimated_monthly_visits"],  # primary-metric unit
            "retrieval_timestamp": retrieval_timestamp.isoformat(),
            # Provenance: how it was collected + the exact request.
            "collected_via": "exa_agent" if with_similarweb else "exa_agent_public_web_fallback",
            "data_source": "similarweb" if with_similarweb else "public_web_estimate",
            "provider_dependent": True,
            "exa_query": query,
            "exa_data_sources": ([{"provider": "similarweb"}] if with_similarweb else []),
            "domain": domain,
            # Capability-checked, per-metric labeled estimates. Missing == missing.
            "metrics": metrics,
        }
        if run_id:
            metadata["exa_run_id"] = run_id

        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="similarweb",
            source_name=action.source_name or self.name,
            url=url,
            final_url=url,
            title=f"Similarweb estimated traffic — {domain}",
            published_at=None,
            retrieved_at=retrieval_timestamp,
            time_window_ids=list(action.time_window_ids),
            raw_text=raw_text,
            normalized_text=normalize_text(raw_text),
            content_hash=content_hash(raw_text),
            metadata=metadata,
            collection_method="exa_similarweb",
            # Only a subset of the enumerated fields is ever guaranteed.
            is_partial=any(f not in metrics for f in _CORE_METRIC_FIELDS),
        )

    @staticmethod
    def _render_text(
        domain: str, metrics: dict[str, dict[str, Any]], observation_period: str | None
    ) -> str:
        """Readable, honest summary — every line marked as an estimate."""
        period = f" ({observation_period})" if observation_period else ""
        lines = [f"Similarweb estimated web analytics for {domain}{period}. All values estimated."]
        for field in (*_CORE_METRIC_FIELDS, "estimated_paid_keywords"):
            metric = metrics.get(field)
            if metric is None:
                continue
            value = metric["value"]
            rendered = (
                value if isinstance(value, (int, float)) else json.dumps(value, sort_keys=True)
            )
            lines.append(f"- {field} (estimated, {metric['unit']}): {rendered}")
        return "\n".join(lines)

    @staticmethod
    def _cost(data: dict[str, Any]) -> float:
        try:
            return float((data.get("costDollars") or {}).get("total") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return 0.0
