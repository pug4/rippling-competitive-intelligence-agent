"""Adversarial-context news adapter — what is happening TO the competitor.

Red-team finding "lawsuit blind spot": the rest of the system crawls what a
competitor SAYS (its site, its ads, its jobs) but never what is happening TO
it — litigation, funding, acquisitions, launches, or how the market frames it
against the focal company. ``NewsTool`` closes that gap with a bounded sweep of
dated public news via the Exa Search API, then deterministically CATEGORIZES
each hit with pure-Python keyword matching (never a model call).

Honesty boundary: every artifact is a DATED public news item whose text is the
provider's snippet — the tool never infers beyond the snippet, never fabricates
a headline, and a query that returns nothing is a TYPED empty (a coverage gap),
not a failure. Absence of news is never evidence that nothing happened.

Reuses the direct-provider Exa REST idiom from ``exa_search`` / ``ads`` (the
``x-api-key`` header, a short timeout, bounded retries on 5xx/timeout); the key
is never logged. Fixture mode never reaches the network: the BaseTool boundary
dispatches ``tests/fixtures/tools/news_market/…`` before ``_execute_live``.
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

# Direct-provider Exa REST contract (identical to exa_search / ads).
EXA_SEARCH_URL = "https://api.exa.ai/search"
_EXA_TIMEOUT_SECONDS = 20.0
_EXA_MAX_RETRIES = 2
_EXA_RETRY_BASE_DELAY = 0.5

# Bounds: ~5 queries x ~5 results = a hard cap of ~25 kept artifacts.
_NUM_RESULTS_PER_QUERY = 5
_MAX_TOTAL_RESULTS = 25

NEWS_SOURCE_TYPE = "news_market"
NO_EXA_KEY_NOTE = (
    "Adversarial-context news sweep not attempted: no Exa API key configured "
    "(exa_api_key) — this is a coverage gap, not evidence that nothing is "
    "happening to the competitor."
)

# ---- deterministic categorization -------------------------------------------
#
# Categories are assigned by pure-Python keyword matching over the lowercased
# ``title + snippet`` — never a model call, so it is fully deterministic and
# free. The lists are checked in the order below and the FIRST category whose
# keyword appears wins, so "harder" adversarial signals (litigation) are
# preferred over softer ones (a launch/press mention), and funding is checked
# before launch so "announces $50M Series B" lands on ``funding`` rather than
# on the ubiquitous "announces" launch cue.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "litigation",
        (
            "lawsuit",
            "class action",
            "settlement",
            "settles",
            "trade secret",
            "injunction",
            "sued",
            " sues ",
            "litigation",
            "alleges",
            "antitrust",
            "subpoena",
            "fined",
            "court",
            "complaint",
            "infringement",
        ),
    ),
    (
        "funding",
        (
            "raised",
            "raises",
            "series a",
            "series b",
            "series c",
            "series d",
            "series e",
            "funding round",
            "valuation",
            "valued at",
            "ipo",
            "went public",
            "seed round",
            "venture",
            "led the round",
        ),
    ),
    (
        "m_and_a",
        (
            "acquires",
            "acquisition",
            "acquired by",
            "acquired",
            "merger",
            "merges with",
            "buyout",
            "takeover",
            "to buy",
        ),
    ),
    (
        "launch",
        (
            "launches",
            "launched",
            "new product",
            "announces",
            "announced",
            "unveils",
            "introduces",
            "rolls out",
            "partnership",
            "partners with",
            "now available",
            "general availability",
        ),
    ),
    (
        "comparison",
        (
            " vs ",
            " vs.",
            "versus",
            "compared to",
            "comparison",
            "alternative to",
            "alternatives to",
            "head-to-head",
        ),
    ),
)


def categorize_news(title: str, snippet: str) -> str:
    """Deterministically bucket a news hit into one of the fixed categories.

    Returns one of ``litigation | funding | m_and_a | launch | comparison |
    other`` by first-match keyword scan over ``title + snippet`` (lowercased).
    Pure Python — no model call, no network, fully deterministic.
    """
    haystack = f" {title or ''} {snippet or ''} ".lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return category
    return "other"


def _parse_published(value: Any) -> datetime | None:
    """Parse Exa's ISO-8601 ``publishedDate``; None when absent/unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class NewsTool(BaseTool):
    """Adversarial-context news: what is happening TO the competitor."""

    name: ClassVar[str] = "news_market"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "news_market"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_market_context",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=True,
            supports_exact_content=False,
            returns_estimates=False,
            known_limitations=[
                "Results are DATED public news snippets from the Exa index; the "
                "tool never infers beyond the snippet and never fabricates a "
                "headline or a date.",
                "Categorization is deterministic keyword matching over the "
                "snippet, not a semantic judgment — a mislabeled 'other' is a "
                "conservative default, never an invented category.",
                "Neural search recall is query-dependent; no results for a query "
                "is a coverage gap, never evidence that nothing happened to the "
                "competitor.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    # ---- query construction --------------------------------------------------

    def _build_queries(self, company: str, focal: str) -> list[str]:
        """Bounded adversarial-context query set for the competitor.

        One query per adversarial angle (litigation, funding, M&A, launch/press)
        plus a competitor-vs-focal comparison query when a focal name is known.
        The keyword coverage mirrors the categories the results are bucketed into
        so the sweep pulls the right news, but the CATEGORY of each hit is always
        re-derived from the result text, never assumed from the query.
        """
        queries = [
            f'{company} lawsuit OR "class action" OR settlement OR "trade secret" OR injunction',
            f'{company} raised OR "series" OR valuation OR IPO OR "went public"',
            f'{company} acquires OR acquisition OR "acquired by" OR merger',
            f'{company} launches OR "new product" OR announces OR partnership',
        ]
        if focal:
            queries.append(f"{company} vs {focal}")
        return queries

    # ---- Exa seam (monkeypatched in tests; never called in fixture mode) -----

    async def _exa_post(self, url: str, payload: dict[str, Any], api_key: str) -> httpx.Response:
        # Direct provider call by design (matches exa_search / ads): api.exa.ai
        # needs the x-api-key header and is not a public-URL fetch. Key never
        # logged.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_EXA_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key},
        ) as client:
            return await retry_async(
                lambda: client.post(url, json=payload),
                retries=_EXA_MAX_RETRIES,
                base_delay=_EXA_RETRY_BASE_DELAY,
            )

    def _exa_error(self, action: ResearchAction, response: httpx.Response) -> ToolResult | None:
        """Map an Exa HTTP status to a typed failure; None means proceed."""
        code = response.status_code
        if code in (401, 403):
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_auth",
                error_message=f"Exa rejected the API key (HTTP {code}).",
            )
        if code == 402:
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_out_of_credits",
                error_message="Exa is out of credits (HTTP 402) — top up the Exa key.",
            )
        if code == 429:
            return self._result(
                action,
                status="failed_retryable",
                error_type="rate_limited",
                error_message="Exa rate limit hit (HTTP 429).",
                retryable=True,
            )
        if code >= 500:
            return self._result(
                action,
                status="failed_retryable",
                error_type="provider_5xx",
                error_message=f"Exa server error (HTTP {code}) after retries.",
                retryable=True,
            )
        if code >= 400:
            return self._result(
                action,
                status="failed_terminal",
                error_type=f"provider_http_{code}",
                error_message=f"Exa returned HTTP {code}.",
            )
        return None

    # ---- live path -----------------------------------------------------------

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message="provider not configured: exa_api_key is not set",
                negative_observations=[NO_EXA_KEY_NOTE],
            )

        company = str(
            action.parameters.get("company") or action.parameters.get("competitor") or ""
        ).strip()
        if not company:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message=(
                    "parameter 'company' (or 'competitor') is required for search_market_context"
                ),
            )
        focal = str(
            action.parameters.get("focal") or action.parameters.get("focal_company") or ""
        ).strip()

        queries = self._build_queries(company, focal)
        artifacts: list[RawArtifact] = []
        notes: list[str] = []
        seen_urls: set[str] = set()
        cost_usd = 0.0
        errored = False

        for query in queries:
            if len(artifacts) >= _MAX_TOTAL_RESULTS:
                break
            payload: dict[str, Any] = {
                "query": query,
                "numResults": _NUM_RESULTS_PER_QUERY,
                "contents": {"text": True},
            }
            response = await self._exa_post(EXA_SEARCH_URL, payload, api_key)
            error = self._exa_error(action, response)
            if error is not None:
                # A hard auth/credit error on the very first query means no data
                # can be collected at all: surface it typed. Later-query errors
                # degrade to a note over whatever was already collected.
                if not artifacts and query is queries[0]:
                    error.negative_observations = [*notes, *error.negative_observations]
                    return error
                notes.append(
                    f"news query '{query}' failed ({error.error_type}); sweep "
                    "continued with the remaining queries."
                )
                errored = True
                continue
            try:
                data = response.json()
            except Exception as exc:  # noqa: BLE001 - a bad body degrades, never crashes
                notes.append(
                    f"news query '{query}' returned invalid JSON ({type(exc).__name__}); skipped."
                )
                errored = True
                continue
            cost_usd += _exa_cost(data)

            kept_this_query = 0
            for item in data.get("results") or []:
                if len(artifacts) >= _MAX_TOTAL_RESULTS:
                    break
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                artifacts.append(self._artifact(action, item, query))
                kept_this_query += 1
            if kept_this_query == 0:
                # A query with no results is a typed empty (a coverage gap),
                # never a failure — recorded so the limitations panel sees it.
                notes.append(
                    f"news query '{query}' returned no results — a coverage gap, "
                    "not evidence of no activity."
                )

        if not artifacts:
            notes.append(
                f"No adversarial-context news found for '{company}' across "
                f"{len(queries)} quer{'y' if len(queries) == 1 else 'ies'} — a "
                "coverage gap, not evidence that nothing is happening to the "
                "competitor."
            )
            return self._result(
                action, status="empty", cost_usd=cost_usd, negative_observations=notes
            )

        status = "partial" if errored else "success"
        return self._result(
            action,
            status=status,
            artifacts=artifacts,
            cost_usd=cost_usd,
            negative_observations=notes,
        )

    def _artifact(self, action: ResearchAction, item: dict[str, Any], query: str) -> RawArtifact:
        url = str(item.get("url"))
        title = item.get("title") or None
        snippet = str(item.get("text") or "")
        published_raw = item.get("publishedDate")
        published_at = _parse_published(published_raw)
        category = categorize_news(str(title or ""), snippet)
        metadata: dict[str, Any] = {
            "category": category,
            # The DATED provenance of the item, kept exactly as the provider
            # reported it (ISO string) — never a fabricated or inferred date.
            "published_at": str(published_raw) if published_raw else None,
            "title": title,
            "query": query,
            "url": url,
        }
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type=NEWS_SOURCE_TYPE,
            source_name=self.name,
            url=url,
            final_url=url,
            title=title,
            published_at=published_at,
            retrieved_at=utcnow(),
            time_window_ids=list(action.time_window_ids),
            raw_text=snippet,
            normalized_text=normalize_text(snippet),
            content_hash=content_hash(snippet),
            metadata=metadata,
            collection_method="news_market_exa",
            # Snippet-only discovery: the full article was not fetched first-party.
            is_partial=True,
        )


def _exa_cost(data: dict[str, Any]) -> float:
    try:
        return float((data.get("costDollars") or {}).get("total") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
