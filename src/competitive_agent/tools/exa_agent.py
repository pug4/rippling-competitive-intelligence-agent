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
from datetime import datetime
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

# action_type -> artifact source_type. research_linkedin fans out into ONE
# per-post artifact (linkedin_post) per post + one synthesis (linkedin).
_SOURCE_TYPE_BY_ACTION: dict[str, str] = {
    "research_linkedin": "linkedin",
    "research_company": "exa_agent",
}
_POST_SOURCE_TYPE = "linkedin_post"

# Structured output for research_linkedin: a synthesis + a list of individual
# posts, so each post becomes its own classifiable artifact with its own URL.
_LINKEDIN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "synthesis": {
            "type": "string",
            "description": "2-4 sentences on the company's LinkedIn positioning and dominant themes.",
        },
        "posts": {
            "type": "array",
            "description": "Individual public LinkedIn posts by the company or its employees.",
            "items": {
                "type": "object",
                "properties": {
                    "post_url": {
                        "type": "string",
                        "description": "Direct URL to the LinkedIn post.",
                    },
                    "author": {"type": "string", "description": "Person who posted."},
                    "author_role": {
                        "type": "string",
                        "description": "Their role/title at the company.",
                    },
                    "posted_at": {
                        "type": "string",
                        "description": "ISO date if known, else empty.",
                    },
                    "theme": {"type": "string", "description": "The post's main marketing theme."},
                    "text": {
                        "type": "string",
                        "description": "The post's text (verbatim excerpt).",
                    },
                },
                "required": ["post_url", "text"],
            },
        },
    },
    "required": ["synthesis", "posts"],
}


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _query_for(action: ResearchAction) -> str:
    p = action.parameters
    company = str(p.get("company") or p.get("domain") or "the company").strip()
    linkedin = str(p.get("linkedin_url") or "").strip()
    focal = str(p.get("focal") or "").strip()
    n = int(p.get("num_posts") or 15)
    if action.action_type == "research_linkedin":
        q = (
            f"Find and analyze up to {n} individual PUBLIC LinkedIn posts by {company} and its "
            f"employees. "
            + (f"Company LinkedIn: {linkedin}. " if linkedin else "")
            + "For EACH post return its direct post_url, the author and their role, the date, the "
            "post's marketing theme, and a verbatim text excerpt. Also give a short synthesis of "
            "the dominant public messaging themes, the audiences addressed, and the competitors/"
            "status-quo they position against. Only include posts you can cite with a real URL."
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

    # P0 item 3: the async agent poller can run far longer than the 60s run-level
    # default (a live research_linkedin call died at the boundary 13/13 times).
    # Its own give-up budget is the initial POST plus one poll-interval sleep per
    # poll: _POST_TIMEOUT (30) + _MAX_POLLS (30) * _POLL_INTERVAL (4) = 150s.
    # Boundary = that budget + ~40s headroom so the tool returns a typed
    # agent_incomplete result before the boundary ever fires. Kept in sync with
    # the poll constants by tests/unit/test_tool_timeout.py.
    TOOL_TIMEOUT_SECONDS: ClassVar[int | None] = 190

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
        if action.action_type == "research_linkedin":
            body["outputSchema"] = _LINKEDIN_OUTPUT_SCHEMA
        if data_sources:
            body["dataSources"] = data_sources

        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_POST_TIMEOUT)) as client:
                start = await client.post(EXA_AGENT_URL, json=body, headers=headers)
                if start.status_code == 402:
                    return self._result(
                        action,
                        status="failed_terminal",
                        error_type="provider_out_of_credits",
                        error_message="Exa is out of credits (HTTP 402) — top up the "
                        "Exa key to enable LinkedIn/agent research.",
                    )
                if start.status_code in (401, 403):
                    return self._result(
                        action,
                        status="failed_terminal",
                        error_type="provider_auth",
                        error_message=f"Exa rejected the key (HTTP {start.status_code}).",
                    )
                if start.status_code == 429:
                    return self._result(
                        action,
                        status="failed_retryable",
                        error_type="rate_limited",
                        error_message="Exa Agent rate limit (429).",
                        retryable=True,
                    )
                if start.status_code >= 400:
                    return self._result(
                        action,
                        status="failed_terminal",
                        error_type=f"provider_http_{start.status_code}",
                        error_message=f"Exa Agent HTTP {start.status_code}.",
                    )
                run = start.json()
                run_id = run.get("id")
                run = await self._poll(client, run_id, headers, run)
        except (httpx.HTTPError, TimeoutError) as exc:
            return self._result(
                action,
                status="failed_retryable",
                error_type=type(exc).__name__,
                error_message=f"Exa Agent request failed: {type(exc).__name__}",
                retryable=True,
            )

        if run is None or run.get("status") != "completed":
            status = (run or {}).get("status", "unknown")
            return self._result(
                action,
                status="failed_retryable",
                error_type="agent_incomplete",
                error_message=f"Exa Agent run did not complete (status={status}).",
                retryable=True,
            )
        return self._map(action, run)

    async def _poll(
        self, client: httpx.AsyncClient, run_id: Any, headers: dict, run: dict
    ) -> dict | None:
        if run.get("status") in _TERMINAL or not run_id:
            return run
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            r = await client.get(
                f"{EXA_AGENT_URL}/{run_id}", headers=headers, timeout=_POLL_TIMEOUT
            )
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
        _s = out.get("structured")
        structured: dict[str, Any] = _s if isinstance(_s, dict) else {}
        grounding = out.get("grounding") or out.get("citations")
        run_id = run.get("id")
        cost = float((run.get("costDollars") or {}).get("total", 0.0))
        base_meta: dict[str, Any] = {
            "exa_agent_run_id": run_id,
            "exa_agent_query": run.get("request", {}).get("query"),
            "exa_agent_cost": run.get("costDollars"),
            "citations": grounding,
            "provider_synthesis": True,  # provider synthesis / extraction, not a first-party page
        }
        artifacts: list[RawArtifact] = []

        # research_linkedin: fan out ONE artifact per post, plus a synthesis.
        if action.action_type == "research_linkedin":
            posts = structured.get("posts")
            for post in posts or []:
                if not isinstance(post, dict):
                    continue
                ptext = str(post.get("text") or "").strip()
                purl = str(post.get("post_url") or "").strip()
                if not ptext or not purl:
                    continue
                # Provider often omits author — derive it from the post URL slug
                # (linkedin.com/posts/<author-slug>_...), deterministic.
                author = post.get("author")
                if not author:
                    import re as _re

                    m = _re.search(r"linkedin\.com/posts/([a-z0-9-]+?)_", purl)
                    if m:
                        slug = _re.sub(r"-\d+$", "", m.group(1))
                        author = " ".join(w.capitalize() for w in slug.split("-") if w) or None
                post = {**post, "author": author}
                artifacts.append(
                    self._artifact(
                        action,
                        source_type=_POST_SOURCE_TYPE,
                        url=purl,
                        text=ptext,
                        title=(str(post.get("author") or "LinkedIn post") + " — LinkedIn post"),
                        author=post.get("author"),
                        published_at=_parse_iso(post.get("posted_at")),
                        meta={
                            **base_meta,
                            "author_role": post.get("author_role"),
                            "theme": post.get("theme"),
                        },
                    )
                )
            synthesis = str(structured.get("synthesis") or text).strip()
            if synthesis:
                artifacts.append(
                    self._artifact(
                        action,
                        source_type=source_type,  # "linkedin"
                        url=str(
                            action.parameters.get("linkedin_url") or "https://www.linkedin.com/"
                        ),
                        text=synthesis,
                        title="LinkedIn positioning synthesis",
                        meta={**base_meta, "structured": structured, "post_count": len(artifacts)},
                    )
                )
            if not artifacts:
                return self._result(
                    action,
                    status="empty",
                    negative_observations=["Exa Agent returned no LinkedIn posts or synthesis."],
                )
            return self._result(action, status="success", artifacts=artifacts, cost_usd=cost)

        # research_company (and any other): single synthesis artifact.
        if not text:
            return self._result(
                action,
                status="empty",
                negative_observations=[f"Exa Agent returned no text for {action.action_type}."],
            )
        artifacts.append(
            self._artifact(
                action,
                source_type=source_type,
                url=str(action.parameters.get("linkedin_url") or "https://www.linkedin.com/"),
                text=text,
                title=f"Exa Agent research: {action.action_type}",
                meta={**base_meta, "structured": structured},
            )
        )
        return self._result(action, status="success", artifacts=artifacts, cost_usd=cost)

    def _artifact(
        self, action, *, source_type, url, text, title, meta, author=None, published_at=None
    ):
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type=source_type,
            source_name=self.name,
            url=url,
            final_url=url,
            title=title,
            author=(str(author) if author else None),
            published_at=published_at if isinstance(published_at, datetime) else None,
            retrieved_at=utcnow(),
            time_window_ids=list(action.time_window_ids),
            raw_text=text,
            normalized_text=normalize_text(text),
            content_hash=content_hash(text),
            metadata=meta,
            collection_method="exa_agent",
            is_partial=True,  # provider synthesis/extraction, not first-party page text
        )
