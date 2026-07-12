"""Exa Agent adapter — agentic web research for LinkedIn presence, employee
posts, and company positioning (https://exa.ai/products/agent).

Unlike a single /search call, the Exa Agent (POST /agent/runs, async) does
multi-step research and returns a cited synthesis. We use it to surface the
LinkedIn/social dimension the plain search never reached, and (optionally)
premium data partners like Similarweb via ``dataSources``. The result is an
HONEST provider synthesis WITH citations — labeled partial, never presented as a
first-party page.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import httpx

from ..processing.normalize import content_hash, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext

EXA_AGENT_URL = "https://api.exa.ai/agent/runs"
_POST_TIMEOUT = 30.0
_POLL_TIMEOUT = 20.0
_POLL_INTERVAL = 4.0
_MAX_POLLS = 30  # ~2 min ceiling
_TERMINAL = {"completed", "failed", "cancelled"}

# action_type -> artifact source_type.
_SOURCE_TYPE_BY_ACTION: dict[str, str] = {
    "research_linkedin": "linkedin",
    "research_company": "exa_agent",
}


def _query_for(action: ResearchAction) -> str:
    p = action.parameters
    company = str(p.get("company") or p.get("domain") or "the company").strip()
    linkedin = str(p.get("linkedin_url") or "").strip()
    focal = str(p.get("focal") or "").strip()
    if action.action_type == "research_linkedin":
        q = (
            f"Research {company}'s LinkedIn presence and how {company} and its employees "
            f"position the product publicly on LinkedIn. "
            + (f"Company LinkedIn: {linkedin}. " if linkedin else "")
            + "Summarize the 3-5 dominant public messaging themes, the audiences/personas "
            "addressed, the status-quo or competitors they position against, and any notable "
            "recent employee or company posts. Ground every theme in a cited source."
        )
    else:
        q = (
            f"Research {company}'s public marketing positioning, product messaging, and target "
            f"segments" + (f" relative to {focal}" if focal else "") + ". "
            "Summarize the dominant themes with cited sources."
        )
    return q


class ExaAgentTool(BaseTool):
    """Agentic LinkedIn/company research via the Exa Agent API."""

    name: ClassVar[str] = "exa_agent"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "exa_linkedin"
    ACTION_TYPES: ClassVar[tuple[str, ...]] = tuple(_SOURCE_TYPE_BY_ACTION)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(_SOURCE_TYPE_BY_ACTION),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=False,
            returns_estimates=False,
            known_limitations=[
                "Exa Agent returns a CITED SYNTHESIS, not first-party page text — "
                "labeled partial; treat as a discovery/positioning signal.",
                "LinkedIn recall depends on public indexing; no result is not "
                "evidence of no activity.",
                "Premium dataSources (e.g. Similarweb) require plan access; the "
                "call still succeeds without them using public web research.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in _SOURCE_TYPE_BY_ACTION

    def _agent_cfg(self, context: ToolContext) -> tuple[str, list[str]]:
        cfg = getattr(context.config, "exa_agent", None) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        effort = str(cfg.get("effort", "low"))
        data_sources = [str(s) for s in (cfg.get("data_sources") or [])]
        return effort, data_sources

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message="provider not configured: exa_api_key is not set",
                negative_observations=[
                    f"Exa Agent not attempted for '{action.action_type}': no API key."
                ],
            )
        effort, data_sources = self._agent_cfg(context)
        body: dict[str, Any] = {"query": _query_for(action), "effort": effort}
        if data_sources:
            body["dataSources"] = data_sources

        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_POST_TIMEOUT)) as client:
                start = await client.post(EXA_AGENT_URL, json=body, headers=headers)
                if start.status_code in (401, 403):
                    return self._result(action, status="failed_terminal", error_type="provider_auth",
                                        error_message=f"Exa rejected the key (HTTP {start.status_code}).")
                if start.status_code == 429:
                    return self._result(action, status="failed_retryable", error_type="rate_limited",
                                        error_message="Exa Agent rate limit (429).", retryable=True)
                if start.status_code >= 400:
                    return self._result(action, status="failed_terminal",
                                        error_type=f"provider_http_{start.status_code}",
                                        error_message=f"Exa Agent HTTP {start.status_code}.")
                run = start.json()
                run_id = run.get("id")
                run = await self._poll(client, run_id, headers, run)
        except (httpx.HTTPError, TimeoutError) as exc:
            return self._result(action, status="failed_retryable", error_type=type(exc).__name__,
                                error_message=f"Exa Agent request failed: {type(exc).__name__}", retryable=True)

        if run is None or run.get("status") != "completed":
            status = (run or {}).get("status", "unknown")
            return self._result(action, status="failed_retryable", error_type="agent_incomplete",
                                error_message=f"Exa Agent run did not complete (status={status}).",
                                retryable=True)
        return self._map(action, run)

    async def _poll(self, client: httpx.AsyncClient, run_id: Any, headers: dict, run: dict) -> dict | None:
        if run.get("status") in _TERMINAL or not run_id:
            return run
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            r = await client.get(f"{EXA_AGENT_URL}/{run_id}", headers=headers, timeout=_POLL_TIMEOUT)
            if r.status_code >= 400:
                return None
            run = r.json()
            if run.get("status") in _TERMINAL:
                return run
        return run

    def _map(self, action: ResearchAction, run: dict[str, Any]) -> ToolResult:
        source_type = _SOURCE_TYPE_BY_ACTION[action.action_type]
        out = run.get("output") or {}
        text = str(out.get("text") or "")
        structured = out.get("structured")
        grounding = out.get("grounding") or out.get("citations")
        if not text:
            return self._result(action, status="empty",
                                negative_observations=[f"Exa Agent returned no text for {action.action_type}."])
        meta: dict[str, Any] = {
            "exa_agent_run_id": run.get("id"),
            "exa_agent_query": run.get("request", {}).get("query"),
            "exa_agent_cost": run.get("costDollars"),
            "citations": grounding,
            "structured": structured,
            "provider_synthesis": True,  # NOT a first-party page
        }
        art = RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type=source_type,
            source_name=self.name,
            url=str(action.parameters.get("linkedin_url") or "https://www.linkedin.com/"),
            final_url=str(action.parameters.get("linkedin_url") or "https://www.linkedin.com/"),
            title=f"Exa Agent research: {action.action_type}",
            retrieved_at=utcnow(),
            time_window_ids=list(action.time_window_ids),
            raw_text=text,
            normalized_text=normalize_text(text),
            content_hash=content_hash(text),
            metadata=meta,
            collection_method="exa_agent",
            is_partial=True,  # cited synthesis, not first-party page text
        )
        return self._result(action, status="success", artifacts=[art],
                            cost_usd=float((run.get("costDollars") or {}).get("total", 0.0)))
