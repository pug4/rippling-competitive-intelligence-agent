"""Keyword intelligence (KEYWORDS contract + Gemini SERP addendum) — Level-B,
optional, honest.

PRIMARY path — live SERP intelligence (no volumes, ever, on this provider):

- ``GeminiSerpProvider`` — active when ``GEMINI_API_KEY`` is set. One
  ``generateContent`` call per keyword with the ``google_search`` tool, which
  returns REAL, CURRENT SERP observations (People-Also-Ask questions, related
  searches, ranking formats, SERP features) plus grounding-source URLs.
  GROUNDING-REQUIRED rule: a response without
  ``groundingMetadata.groundingChunks`` is model recall, not a SERP
  observation — it is DISCARDED, never shipped. Gemini does not return
  volume/CPC/difficulty; those fields simply do not exist on this path.
  Model comes from ``GEMINI_MODEL`` (default ``gemini-flash-latest``) with
  404 fallbacks through ``gemini-3.1-flash-lite`` then ``gemini-2.0-flash``.
  A 429 on the FIRST call means search-grounding quota needs billing on the
  key's project — a typed :class:`GeminiSerpQuotaError` carrying exactly
  :data:`GEMINI_BILLING_MESSAGE`; a 429 mid-batch stops enriching with a
  "rate-limited after N keywords" note.

FUTURE volume seam (kept intact; volumes are only knowable via a paid API):

- ``SemrushProvider`` — active when ``SEMRUSH_API_KEY`` is set. Calls the
  ``api.semrush.com`` analytics CSV endpoints and parses ``Search Volume`` /
  ``CPC`` / ``Competition``. ``ERROR 50 :: NOTHING FOUND`` parses as EMPTY —
  never as fabricated zeros. Ahrefs / Google Keyword Planner drop in by
  implementing :class:`KeywordProvider` and adding one ``(ENV_VAR, factory)``
  row to ``_PROVIDER_REGISTRY``.
- ``NoKeywordProvider`` — the fail-closed default when nothing is configured;
  the tool degrades to a TYPED unsupported result carrying exactly
  :data:`NO_PROVIDER_MESSAGE`.

Consumption paths:
- :class:`KeywordsTool` (action ``enrich_keywords``, source ``keywords``):
  prefers SERP intelligence when ``GEMINI_API_KEY`` is present (one labeled
  RawArtifact per keyword), falls back to volume metrics when only a volume
  key is present, and degrades typed when neither is configured;
- :func:`fetch_serp_intel` / :func:`fetch_keyword_metrics` for direct
  in-process use (``paid_search.py``) — each returns ``None`` when its
  provider is unconfigured, so callers render an honest empty state instead
  of inventing data.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from collections.abc import Iterable, Sequence
from typing import Any, ClassVar, Protocol, runtime_checkable
from urllib.parse import quote_plus

import httpx

from ..config import secret_from_env_or_settings
from ..processing.normalize import content_hash, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.keywords import KeywordMetric, SerpIntel
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext

SEMRUSH_API_URL = "https://api.semrush.com/"

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# NEVER hardcode gemini-2.5-flash (404 "no longer available to new users").
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"
GEMINI_MODEL_FALLBACKS: tuple[str, ...] = ("gemini-3.1-flash-lite", "gemini-2.0-flash")

# Exact message the spec pins for the unconfigured state.
NO_PROVIDER_MESSAGE = (
    "no keyword provider configured (set GEMINI_API_KEY for live SERP intelligence)"
)

# Exact typed-degrade note for a 429 on the FIRST grounded call: search
# grounding is paid-tier-only for new keys until billing is enabled.
GEMINI_BILLING_MESSAGE = (
    "Gemini search-grounding quota unavailable — enable billing on the Gemini "
    "API key's project (AI Studio -> Settings -> Plan)"
)

# Batch cap shared by every volume-provider caller (contract: "cap ~40").
MAX_KEYWORDS_PER_BATCH = 40

# One Gemini call per keyword — cap per batch/draft to bound cost + latency.
MAX_SERP_CALLS_PER_BATCH = 12

_TIMEOUT_SECONDS = 20.0
_SERP_TIMEOUT_SECONDS = 30.0

# Semrush error bodies come back HTTP 200 with a plain-text "ERROR <code> ::
# <message>" body. Code 50 ("NOTHING FOUND") means no data for the phrase —
# an EMPTY result, not a failure and never a zero. Any other error code is a
# real provider failure and must surface typed, not silently empty.
_NOTHING_FOUND_RE = re.compile(r"^ERROR\s+50\b", re.IGNORECASE)


class KeywordProviderError(RuntimeError):
    """A keyword provider failed in a way that must surface typed (never as data)."""


class GeminiSerpQuotaError(KeywordProviderError):
    """Gemini search-grounding quota exhausted (HTTP 429).

    Raised with exactly :data:`GEMINI_BILLING_MESSAGE` when the FIRST call of
    a batch 429s (billing not enabled on the key's project) — callers degrade
    typed and keep their provider envelope null.
    """


@runtime_checkable
class KeywordProvider(Protocol):
    """Minimal seam every keyword-metrics provider implements."""

    name: str

    def fetch(self, keywords: Sequence[str]) -> list[KeywordMetric]:
        """Return provider-reported metrics for the given keywords.

        Keywords the provider has no data for are simply absent from the
        result — never returned with fabricated values.
        """
        ...


def _clean_keywords(keywords: Iterable[Any], cap: int = MAX_KEYWORDS_PER_BATCH) -> list[str]:
    """Whitespace-normalize, casefold-dedupe (keeping first spelling), cap."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in keywords:
        if not isinstance(raw, str):
            continue
        kw = " ".join(raw.split())
        key = kw.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(kw)
        if len(out) >= cap:
            break
    return out


def _int_or_none(value: Any) -> int | None:
    try:
        text = str(value).strip()
        return int(float(text)) if text else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def parse_semrush_csv(body: str) -> list[dict[str, Any]]:
    """Parse a Semrush analytics CSV body into plain row dicts.

    Semrush returns ``;``-separated CSV with a header row such as
    ``Keyword;Search Volume;CPC;Competition``. Error bodies are plain text:
    ``ERROR 50 :: NOTHING FOUND`` parses as EMPTY (no data is a finding, not
    a failure); any other ``ERROR`` raises :class:`KeywordProviderError` so
    auth/limit problems surface typed instead of masquerading as no-data.
    """
    text = (body or "").strip()
    if not text:
        return []
    if text.upper().startswith("ERROR"):
        if _NOTHING_FOUND_RE.match(text) or "NOTHING FOUND" in text.upper():
            return []
        raise KeywordProviderError(f"semrush API error: {text[:160]}")
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        keyword = (row.get("Keyword") or "").strip()
        if not keyword:
            continue
        rows.append(
            {
                "keyword": keyword,
                "volume": _int_or_none(row.get("Search Volume")),
                "cpc_usd": _float_or_none(row.get("CPC")),
                "competition": _float_or_none(row.get("Competition")),
            }
        )
    return rows


class SemrushProvider:
    """Semrush analytics-API provider (CSV endpoints; provider-reported values)."""

    name = "semrush"

    def __init__(
        self,
        api_key: str,
        *,
        database: str = "us",
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._database = database
        self._timeout = timeout_seconds

    def fetch(self, keywords: Sequence[str]) -> list[KeywordMetric]:
        """``phrase_this`` per keyword; keywords with no data are omitted."""
        cleaned = _clean_keywords(keywords)
        metrics: list[KeywordMetric] = []
        if not cleaned:
            return metrics
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            for keyword in cleaned:
                body = self._get(
                    client,
                    {
                        "type": "phrase_this",
                        "key": self._api_key,
                        "phrase": keyword,
                        "database": self._database,
                        "export_columns": "Ph,Nq,Cp,Co",
                    },
                )
                metrics.extend(self._to_metrics(body))
        return metrics

    def fetch_related(self, phrase: str, *, limit: int = 10) -> list[KeywordMetric]:
        """``phrase_related`` expansion around one phrase (bounded)."""
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            body = self._get(
                client,
                {
                    "type": "phrase_related",
                    "key": self._api_key,
                    "phrase": phrase,
                    "database": self._database,
                    "export_columns": "Ph,Nq,Cp,Co",
                    "display_limit": str(limit),
                },
            )
        return self._to_metrics(body)

    def _get(self, client: httpx.Client, params: dict[str, str]) -> str:
        response = client.get(SEMRUSH_API_URL, params=params)
        if response.status_code >= 400:
            raise KeywordProviderError(f"semrush HTTP {response.status_code}")
        return response.text

    def _to_metrics(self, body: str) -> list[KeywordMetric]:
        retrieved_at = utcnow()
        return [
            KeywordMetric(source=self.name, retrieved_at=retrieved_at, **row)
            for row in parse_semrush_csv(body)
        ]


class NoKeywordProvider:
    """Fail-closed default: no key configured -> typed unavailability, no data."""

    name = "none"

    def fetch(self, keywords: Sequence[str]) -> list[KeywordMetric]:
        raise KeywordProviderError(NO_PROVIDER_MESSAGE)


# ---------------------------------------------------------------------------
# Gemini + Google-Search-grounding SERP intelligence (PRIMARY path)
# ---------------------------------------------------------------------------


def _serp_prompt(keyword: str) -> str:
    """Per-keyword grounded prompt (spec-pinned): report ONLY what the live
    results show, as one strict JSON object."""
    return (
        f'Search Google for "{keyword}" and report ONLY what the live results '
        "show, as JSON:\n"
        '{"paa_questions": [...], "related_searches": [...], '
        '"ranking_formats": [...],\n'
        ' "serp_features": [...], "intent_note": "one sentence on the dominant intent"}\n'
        "ranking_formats = content types in the top ~5 organic results "
        "(listicle, how-to, comparison, tool page, docs, video). "
        "serp_features = features occupying the page (featured_snippet, "
        "ai_overview, ads_top, people_also_ask, video_pack, local_pack). "
        "Report only what is actually present."
    )


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def parse_serp_json(text: str) -> dict[str, Any] | None:
    """Defensively extract the JSON object from a grounded Gemini text answer.

    ``responseSchema`` is NOT combinable with the ``google_search`` tool, so
    the model is prompted for JSON in plain text and the answer may arrive
    fenced (`` ```json ... ``` ``) or wrapped in prose. Tries, in order:
    direct ``json.loads``; the first fenced block; the outermost
    ``{ ... }`` substring. Returns ``None`` when nothing parses — callers
    retry once with a stricter instruction, then give up typed-empty.
    """
    candidate = (text or "").strip()
    if not candidate:
        return None
    attempts = [candidate]
    fenced = _FENCED_JSON_RE.search(candidate)
    if fenced:
        attempts.append(fenced.group(1))
    start, end = candidate.find("{"), candidate.rfind("}")
    if 0 <= start < end:
        attempts.append(candidate[start : end + 1])
    for attempt in attempts:
        try:
            obj = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _str_list(value: Any) -> list[str]:
    """Keep only non-empty strings (whitespace-normalized), order-preserving."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = " ".join(item.split())
            if text:
                out.append(text)
    return out


def _candidate_text(payload: dict[str, Any]) -> str:
    """Concatenate ``candidates[0].content.parts[*].text``."""
    candidates = payload.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    return "\n".join(
        p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)
    )


def _grounding_sources(payload: dict[str, Any]) -> list[str]:
    """Grounding URIs from ``candidates[0].groundingMetadata.groundingChunks``.

    Empty means the answer is UNGROUNDED (model recall) and must be discarded.
    """
    candidates = payload.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return []
    metadata = candidates[0].get("groundingMetadata") or {}
    sources: list[str] = []
    for chunk in metadata.get("groundingChunks") or []:
        uri = ((chunk or {}).get("web") or {}).get("uri")
        if isinstance(uri, str) and uri.strip() and uri.strip() not in sources:
            sources.append(uri.strip())
    return sources


class GeminiSerpProvider:
    """Live-SERP intelligence via Gemini with Google Search grounding.

    Returns OBSERVED, timestamped SERP data with grounding-source URLs; never
    volumes/CPC/difficulty (Gemini does not report them). Ungrounded answers
    are discarded per the GROUNDING-REQUIRED rule.
    """

    name = "gemini_serp"

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        timeout_seconds: float = _SERP_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        env_model = secret_from_env_or_settings("GEMINI_MODEL")
        self._model = model or env_model or GEMINI_DEFAULT_MODEL
        self._timeout = timeout_seconds
        # Human-readable degrade notes from the LAST fetch_serp call:
        # rate-limit stops, ungrounded discards, unparseable answers.
        self.notes: list[str] = []

    def fetch_serp(self, keywords: Sequence[str]) -> list[SerpIntel]:
        """One grounded call per keyword (capped); ungrounded rows discarded.

        A 429 on the first call raises :class:`GeminiSerpQuotaError` with
        exactly :data:`GEMINI_BILLING_MESSAGE`; a 429 mid-batch stops cleanly
        with a "rate-limited after N keywords" note.
        """
        cleaned = _clean_keywords(keywords, cap=MAX_SERP_CALLS_PER_BATCH)
        self.notes = []
        intel: list[SerpIntel] = []
        if not cleaned:
            return intel
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            for index, keyword in enumerate(cleaned):
                try:
                    row = self._fetch_one(client, keyword)
                except GeminiSerpQuotaError:
                    if index == 0:
                        # Billing not enabled for search grounding on this key
                        # — a typed degrade, never partial or invented data.
                        raise GeminiSerpQuotaError(GEMINI_BILLING_MESSAGE) from None
                    self.notes.append(f"rate-limited after {index} keywords")
                    break
                if row is not None:
                    intel.append(row)
        return intel

    def _fetch_one(self, client: httpx.Client, keyword: str) -> SerpIntel | None:
        payload = self._generate(client, _serp_prompt(keyword))
        sources = _grounding_sources(payload)
        if not sources:
            # GROUNDING-REQUIRED: no grounding chunks -> model recall, not an
            # observed SERP. Discard; never ship.
            self.notes.append(
                f'ungrounded Gemini answer for "{keyword}" discarded (no grounding chunks)'
            )
            return None
        data = parse_serp_json(_candidate_text(payload))
        if data is None:
            # One bounded re-ask for strict JSON, then give up typed-empty.
            payload = self._generate(
                client, _serp_prompt(keyword) + "\nReturn ONLY the JSON object."
            )
            retry_sources = _grounding_sources(payload)
            data = parse_serp_json(_candidate_text(payload))
            if data is None or not retry_sources:
                self.notes.append(
                    f'unparseable Gemini answer for "{keyword}" dropped (no valid JSON object)'
                )
                return None
            sources = retry_sources
        return SerpIntel(
            keyword=keyword,
            paa_questions=_str_list(data.get("paa_questions")),
            related_searches=_str_list(data.get("related_searches")),
            ranking_formats=_str_list(data.get("ranking_formats")),
            serp_features=_str_list(data.get("serp_features")),
            intent_note=" ".join(str(data.get("intent_note") or "").split()),
            sources=sources,
            retrieved_at=utcnow(),
        )

    def _generate(self, client: httpx.Client, prompt: str) -> dict[str, Any]:
        """POST generateContent, walking the 404 model-fallback list once each."""
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        }
        tried_404: list[str] = []
        models = [self._model] + [m for m in GEMINI_MODEL_FALLBACKS if m != self._model]
        for model in models:
            response = self._post(client, model, body)
            if response.status_code == 404:
                tried_404.append(model)  # unavailable to this key — walk on
                continue
            if response.status_code == 429:
                raise GeminiSerpQuotaError("gemini HTTP 429 (search-grounding quota)")
            if response.status_code >= 400:
                raise KeywordProviderError(f"gemini HTTP {response.status_code}")
            self._model = model  # pin the working model for the rest of the batch
            try:
                payload = response.json()
            except ValueError as exc:
                raise KeywordProviderError("gemini returned a non-JSON body") from exc
            return payload if isinstance(payload, dict) else {}
        raise KeywordProviderError(
            "gemini: no available model (404 on " + ", ".join(tried_404) + ")"
        )

    def _post(self, client: httpx.Client, model: str, body: dict[str, Any]) -> httpx.Response:
        """One POST with a single retry on 5xx/timeout/connect failures."""
        url = f"{GEMINI_API_BASE}/{model}:generateContent"
        for attempt in (0, 1):
            try:
                response = client.post(url, params={"key": self._api_key}, json=body)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt == 0:
                    continue
                raise KeywordProviderError(f"gemini unreachable: {type(exc).__name__}") from exc
            if response.status_code >= 500 and attempt == 0:
                continue
            return response
        raise KeywordProviderError("gemini unreachable")  # pragma: no cover


def active_serp_provider() -> GeminiSerpProvider | None:
    """The configured SERP-intelligence provider, or None (env-driven per call)."""
    key = secret_from_env_or_settings("GEMINI_API_KEY")
    return GeminiSerpProvider(api_key=key) if key else None


def fetch_serp_intel(keywords: list[str]) -> list[SerpIntel] | None:
    """Direct (no tool plumbing) SERP-intelligence fetch for in-process callers.

    Returns None when no ``GEMINI_API_KEY`` is configured — the caller renders
    an honest no-provider state. Dedupes and caps the batch at
    ``MAX_SERP_CALLS_PER_BATCH``. A first-call billing 429 propagates as
    :class:`GeminiSerpQuotaError` (exact :data:`GEMINI_BILLING_MESSAGE`) for
    the caller to degrade on — it is never converted into empty "data".
    """
    provider = active_serp_provider()
    if provider is None:
        return None
    cleaned = _clean_keywords(keywords, cap=MAX_SERP_CALLS_PER_BATCH)
    if not cleaned:
        return []
    return provider.fetch_serp(cleaned)


# Volume-provider registry keyed by env-var presence; first configured entry
# wins. This is the FUTURE volume seam — Ahrefs / Google Keyword Planner drop
# in as new rows, e.g. ("AHREFS_API_TOKEN", lambda key: AhrefsProvider(api_key=key)).
_PROVIDER_REGISTRY: tuple[tuple[str, Any], ...] = (
    ("SEMRUSH_API_KEY", lambda key: SemrushProvider(api_key=key)),
)


def active_keyword_provider() -> KeywordProvider | None:
    """The configured provider, or None. Resolution is env-driven per call."""
    for env_var, factory in _PROVIDER_REGISTRY:
        key = secret_from_env_or_settings(env_var)
        if key:
            provider: KeywordProvider = factory(key)
            return provider
    return None


def resolve_keyword_provider() -> KeywordProvider:
    """Always returns a provider; :class:`NoKeywordProvider` when unconfigured."""
    return active_keyword_provider() or NoKeywordProvider()


def fetch_keyword_metrics(keywords: list[str]) -> list[KeywordMetric] | None:
    """Direct (no tool plumbing) metrics fetch for in-process callers.

    Returns None when no provider is configured — the caller renders an honest
    "no keyword API configured" state. Dedupes and caps the batch at
    ``MAX_KEYWORDS_PER_BATCH``. Provider failures propagate as
    :class:`KeywordProviderError` (or transport errors) for the caller to
    degrade on — they are never converted into empty "data".
    """
    provider = active_keyword_provider()
    if provider is None:
        return None
    cleaned = _clean_keywords(keywords)
    if not cleaned:
        return []
    return provider.fetch(cleaned)


class KeywordsTool(BaseTool):
    """Level-B keyword intelligence through the configured provider(s).

    SERP intelligence (Gemini grounding) is PREFERRED when ``GEMINI_API_KEY``
    is present; volume metrics run only when a volume key alone is configured;
    neither configured -> typed unsupported. Nothing is ever synthesized.
    """

    name: ClassVar[str] = "keywords"
    adapter_version: ClassVar[str] = "0.2.0"
    source_flag_name: ClassVar[str] = "keywords"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("enrich_keywords",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=False,
            returns_estimates=True,
            known_limitations=[
                "SERP intelligence (GEMINI_API_KEY, preferred) is OBSERVED live "
                "from Google results via search grounding — it never includes "
                "volume/CPC/difficulty, and ungrounded answers are discarded.",
                "Volume/CPC/competition come only from a paid keyword API "
                "(SEMRUSH_API_KEY volume seam) as provider-reported ESTIMATES, "
                "never measured auction truth.",
                f"With neither key the tool degrades to a typed unsupported "
                f"result ({NO_PROVIDER_MESSAGE}) and nothing is ever synthesized.",
                "Keywords the provider has no data for stay missing — absence "
                "is reported as a negative observation, not a zero.",
                f"Batches are capped at {MAX_KEYWORDS_PER_BATCH} keywords "
                f"({MAX_SERP_CALLS_PER_BATCH} for per-keyword SERP calls).",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        serp_provider = active_serp_provider()
        provider = resolve_keyword_provider()
        if serp_provider is None and isinstance(provider, NoKeywordProvider):
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message=NO_PROVIDER_MESSAGE,
                negative_observations=[
                    "Keyword intelligence not attempted for "
                    f"'{action.action_type}': {NO_PROVIDER_MESSAGE}."
                ],
            )

        raw_keywords = action.parameters.get("keywords")
        keywords = _clean_keywords(raw_keywords if isinstance(raw_keywords, list) else [])
        if not keywords:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message=(
                    "parameter 'keywords' (non-empty list of phrases) is required "
                    "for enrich_keywords"
                ),
            )

        # PRIMARY: live SERP intelligence whenever the Gemini key is present.
        if serp_provider is not None:
            return await self._execute_serp(action, serp_provider, keywords)

        # Fallback: the volume seam (only a volume key configured).
        # Provider calls are synchronous HTTP; run off-loop so the bounded
        # tool timeout still governs without blocking the event loop.
        metrics = await asyncio.to_thread(provider.fetch, keywords)

        if not metrics:
            return self._result(
                action,
                status="empty",
                negative_observations=[
                    f"Keyword provider '{provider.name}' returned no data for any of "
                    f"the {len(keywords)} requested keyword(s); nothing synthesized."
                ],
            )

        returned = {m.keyword.casefold() for m in metrics}
        missing = [kw for kw in keywords if kw.casefold() not in returned]
        artifact = self._artifact(action, provider.name, keywords, metrics)
        negative_observations: list[str] = []
        if missing:
            negative_observations.append(
                f"Keyword provider '{provider.name}' had no data for: "
                + ", ".join(missing[:10])
                + ("..." if len(missing) > 10 else "")
                + " — those keywords stay missing (not zeroed)."
            )
        return self._result(
            action,
            status="partial" if missing else "success",
            artifacts=[artifact],
            negative_observations=negative_observations,
        )

    def _artifact(
        self,
        action: ResearchAction,
        provider_name: str,
        keywords_requested: list[str],
        metrics: list[KeywordMetric],
    ) -> RawArtifact:
        retrieved_at = utcnow()
        raw_text = self._render_text(provider_name, metrics)
        url = "https://www.semrush.com/analytics/keywordoverview/"
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="keywords",
            source_name=action.source_name or self.name,
            url=url,
            final_url=url,
            title=f"Keyword metrics ({provider_name}) — {len(metrics)} keyword(s)",
            published_at=None,
            retrieved_at=retrieved_at,
            time_window_ids=list(action.time_window_ids),
            raw_text=raw_text,
            normalized_text=normalize_text(raw_text),
            content_hash=content_hash(raw_text),
            metadata={
                "provider": provider_name,
                "estimated": True,  # provider-modeled numbers, never auction truth
                "unit": "searches/month",
                "retrieval_timestamp": retrieved_at.isoformat(),
                "keywords_requested": keywords_requested,
                "metrics": [m.model_dump(mode="json") for m in metrics],
            },
            collection_method=f"{provider_name}_api",
            is_partial=len(metrics) < len(keywords_requested),
        )

    @staticmethod
    def _render_text(provider_name: str, metrics: list[KeywordMetric]) -> str:
        lines = [
            f"Keyword metrics from {provider_name} (provider-reported estimates; "
            "missing fields are unknown, never zero)."
        ]
        for m in metrics:
            volume = f"{m.volume}/mo" if m.volume is not None else "unknown"
            cpc = f"${m.cpc_usd:.2f}" if m.cpc_usd is not None else "unknown"
            competition = f"{m.competition:.2f}" if m.competition is not None else "unknown"
            lines.append(f'- "{m.keyword}": volume {volume}, CPC {cpc}, competition {competition}')
        return "\n".join(lines)

    # ---- SERP-intelligence path (Gemini grounding) -------------------------

    async def _execute_serp(
        self,
        action: ResearchAction,
        provider: GeminiSerpProvider,
        keywords: list[str],
    ) -> ToolResult:
        try:
            intel = await asyncio.to_thread(provider.fetch_serp, keywords)
        except GeminiSerpQuotaError as exc:
            # Billing-429 on the first grounded call: typed degrade carrying
            # exactly the spec's note — never partial or invented data.
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_quota",
                error_message=str(exc),
                negative_observations=[f"SERP intelligence not collected: {exc}."],
            )
        notes = list(provider.notes)
        if not intel:
            return self._result(
                action,
                status="empty",
                negative_observations=[
                    f"SERP provider '{provider.name}' returned no grounded data for "
                    f"any of the {len(keywords)} requested keyword(s); nothing "
                    "synthesized."
                ]
                + notes,
            )
        returned = {row.keyword.casefold() for row in intel}
        missing = [kw for kw in keywords if kw.casefold() not in returned]
        if missing:
            notes.append(
                "No grounded SERP intelligence for: "
                + ", ".join(missing[:10])
                + ("..." if len(missing) > 10 else "")
                + " — those keywords stay missing (never synthesized)."
            )
        artifacts = [self._serp_artifact(action, row) for row in intel]
        return self._result(
            action,
            status="partial" if missing else "success",
            artifacts=artifacts,
            negative_observations=notes,
        )

    def _serp_artifact(self, action: ResearchAction, intel: SerpIntel) -> RawArtifact:
        """One artifact per keyword: readable rollup + the SerpIntel record."""
        raw_text = self._render_serp_text(intel)
        url = "https://www.google.com/search?q=" + quote_plus(intel.keyword)
        return RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="keywords",
            source_name=action.source_name or self.name,
            url=url,
            final_url=url,
            title=f'Live SERP intelligence (gemini_serp) — "{intel.keyword}"',
            published_at=None,
            retrieved_at=intel.retrieved_at,
            time_window_ids=list(action.time_window_ids),
            raw_text=raw_text,
            normalized_text=normalize_text(raw_text),
            content_hash=content_hash(raw_text),
            metadata={
                "provider": "gemini_serp",
                # Observed on the live results page (grounded), never estimated
                # and never model recall.
                "observed": True,
                "retrieval_timestamp": intel.retrieved_at.isoformat(),
                "serp_intel": intel.model_dump(mode="json"),
                "sources": list(intel.sources),
            },
            collection_method="gemini_google_search_grounding",
            is_partial=False,
        )

    @staticmethod
    def _render_serp_text(intel: SerpIntel) -> str:
        lines = [
            f'Live Google SERP observations for "{intel.keyword}" (Gemini search '
            "grounding; observed on the results page at retrieval time, not model recall)."
        ]
        if intel.paa_questions:
            lines.append("People also ask:")
            lines.extend(f"- {q}" for q in intel.paa_questions)
        if intel.related_searches:
            lines.append("Related searches: " + "; ".join(intel.related_searches))
        if intel.ranking_formats:
            lines.append(
                "Ranking formats (top organic results): " + ", ".join(intel.ranking_formats)
            )
        if intel.serp_features:
            lines.append("SERP features present: " + ", ".join(intel.serp_features))
        if intel.intent_note:
            lines.append(f"Dominant intent: {intel.intent_note}")
        lines.append("Grounding sources:")
        lines.extend(f"- {source}" for source in intel.sources)
        return "\n".join(lines)
