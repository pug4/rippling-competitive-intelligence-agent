"""Advertising adapters: Google Ads Transparency + Meta Ad Library + LinkedIn.

Blueprint §12.5 / §12.6, §37.12 "Advertising adapters", §39.7 provider-reality,
and the ADS contract (dual collection path).

Two cooperating paths live inside the stable GoogleAdsTool / MetaAdsTool
classes (tool names and action types unchanged — the planner needs no change):

(a) official-API seam — Meta Ad Library Graph API (``ads_archive``) when the
    ``META_ADS_ACCESS_TOKEN`` environment variable is present: creative
    bodies, publisher platforms, delivery start/stop, and EU impression
    BUCKETS come from the platform record itself. Token absent -> a typed
    skip note ("no META_ADS_ACCESS_TOKEN — using public-web path") and the
    web path below.
(b) Exa/web path (Google + Meta) — advertiser-SCOPED discovery on the public
    library surfaces (adstransparency.google.com, facebook.com/ads/library):
    a kept result MUST match the competitor domain or advertiser name
    (case-insensitive); everything else is dropped and counted in the
    ToolResult's negative observations (this kills the Costco /
    Electoral-Commission junk class). Kept pages are fetched first-party
    (SharedHttp, robots-respecting; exa_contents fallback on HTTP failure
    only) and structured per-ad records are extracted via the agentic
    ``ad_intelligence`` prompt. The TOOL — never the model — loops the
    prompt's bounded ``next_queries`` (max 3 follow-up iterations, each one
    Exa search + fetches). Fixture mode never reaches any of this: the
    BaseTool boundary dispatches fixtures before ``_execute_live``.

Honesty boundary (§37.12 do_not_claim, §39.7, ADS contract): creatives,
formats, regions, run dates, active status, and impression BUCKETS are
claimable when the library shows them. Exact bid keywords, CPC, commercial
spend, CTR/CVR/ROAS are never claimable — never emitted, never inferred.
Every extracted record's ``creative_text`` must containment-verify against
the fetched page text (processing/normalize.contains_excerpt) or the record
is dropped and logged. A missing provider degrades to a TYPED result; no
creative is ever fabricated.
"""

from __future__ import annotations

import json
import re
from datetime import UTC
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from ..config import secret_from_env_or_settings
from ..processing.normalize import contains_excerpt, content_hash, html_to_text, normalize_text
from ..schemas.ad_intel import AdIntelligence, AdRecord
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext
from .http import retry_async

# Exa REST endpoints (same direct-provider contract as ``exa_search``): the
# x-api-key header, a short timeout, and bounded retries on 5xx / timeout.
EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"
_EXA_TIMEOUT_SECONDS = 20.0
_EXA_MAX_RETRIES = 2
_EXA_RETRY_BASE_DELAY = 0.5
_DEFAULT_NUM_RESULTS = 8

# Public ad-library surfaces the web path scopes discovery to.
_TRANSPARENCY_DOMAIN = "adstransparency.google.com"
_META_LIBRARY_DOMAIN = "facebook.com"

# Meta Ad Library official API (the only official seam that exists: worldwide
# political/social-issue ads + EU commercial transparency).
META_ADS_ARCHIVE_URL = "https://graph.facebook.com/v19.0/ads_archive"
META_ADS_TOKEN_ENV = "META_ADS_ACCESS_TOKEN"
# Exact honest reason recorded when the official-API seam is skipped.
NO_META_TOKEN_NOTE = "no META_ADS_ACCESS_TOKEN — using public-web path"
_META_TIMEOUT_SECONDS = 20.0
_META_ARCHIVE_FIELDS: tuple[str, ...] = (
    "id",
    "page_name",
    "ad_creative_bodies",
    "ad_creative_link_titles",
    "ad_creative_link_captions",
    "publisher_platforms",
    "ad_delivery_start_time",
    "ad_delivery_stop_time",
    "impressions",
    "languages",
)

# Agentic extraction bounds. The TOOL owns the loop over the prompt's
# next_queries: at most 3 follow-up iterations after the initial discovery,
# each iteration exactly one Exa search plus fetches.
AD_INTEL_TASK = "ad_intelligence"
AD_INTEL_PROMPT_NAME = "ad_intelligence"
_MAX_NEXT_QUERY_ITERATIONS = 3
_MAX_NEXT_QUERIES_PER_PAGE = 3
_MAX_PAGES_PER_ITERATION = 5
_MAX_EXTRACTION_PAGES = 8
_MAX_PAGE_TEXT_CHARS = 20_000

AD_INTEL_SYSTEM = (
    "You extract structured public-ad records from ad-library page text. "
    "Claim only what the page visibly shows; never bid keywords, CPC, spend, "
    "or performance rates. Return only the structured result."
)

# The normalized public-ad metadata keys shared by all ad adapters (§37.12).
PUBLIC_AD_METADATA_KEYS: tuple[str, ...] = (
    "advertiser",
    "platform_or_surface",
    "creative_body",
    "headline",
    "cta",
    "format",
    "first_observed",
    "last_observed",
    "region",
    "landing_url",
    "creative_hash",
)


# ---- advertiser scoping (STRICT) --------------------------------------------


def _name_matches(text: str, advertiser: str) -> bool:
    """Case-insensitive advertiser-name match on word boundaries."""
    name = (advertiser or "").strip()
    if not name:
        return False
    return re.search(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", text or "") is not None


def _domain_matches(text: str, domain: str) -> bool:
    """Label-boundary domain match: 'deel.com' matches 'deel.com' and
    'app.deel.com' but never 'wheeldeel.com' (substring would fail open)."""
    dom = (domain or "").strip()
    if not dom:
        return False
    return re.search(rf"(?i)(?<![a-z0-9-]){re.escape(dom)}", text or "") is not None


def matches_advertiser_scope(haystack: str, advertiser: str, advertiser_domain: str) -> bool:
    """STRICT advertiser scoping for discovery results: keep only content that
    matches the competitor domain or the advertiser name, case-insensitively."""
    return _name_matches(haystack, advertiser) or _domain_matches(haystack, advertiser_domain)


def _host_matches_domain(url: str, domain: str) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    dom = (domain or "").lower().strip()
    return bool(dom) and (host == dom or host.endswith("." + dom))


def record_in_scope(record: AdRecord, advertiser: str, advertiser_domain: str) -> bool:
    """STRICT scoping for extracted records: the record's advertiser must match
    the competitor name/domain, or its landing URL must be on the competitor
    domain. Anything else is another advertiser's ad and is dropped."""
    if _name_matches(record.advertiser, advertiser):
        return True
    if _domain_matches(record.advertiser, advertiser_domain):
        return True
    return _host_matches_domain(record.landing_url or "", advertiser_domain)


def _valid_http_url(url: str | None) -> bool:
    parts = urlsplit((url or "").strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def repair_record_provenance(
    record: AdRecord, *, platform: str, page_url: str
) -> tuple[AdRecord, bool]:
    """Defensive provenance repair for extracted records — never a drop.

    ``source_url`` is persisted as the artifact's canonical URL and drives
    downstream classification, so a blank/invalid value is replaced with the
    URL of the page the record was actually extracted from; a ``platform``
    that is not this tool's platform is replaced with the tool's own (the tool
    KNOWS which library it searched — the model does not get a vote). Returns
    ``(record, True)`` when either field was corrected, ``(record, False)``
    when the record was already sound (and is returned untouched).
    """
    updates: dict[str, Any] = {}
    if not _valid_http_url(record.source_url):
        updates["source_url"] = page_url
    if record.platform != platform:
        updates["platform"] = platform
    if not updates:
        return record, False
    return record.model_copy(update=updates), True


# ---- artifact builders -------------------------------------------------------


def _public_ad_artifact(
    action: ResearchAction,
    *,
    source_type: str,
    source_name: str,
    collection_method: str,
    url: str,
    advertiser: str,
    platform_or_surface: str,
    creative_body: str | None = None,
    headline: str | None = None,
    cta: str | None = None,
    ad_format: str | None = None,
    first_observed: str | None = None,
    last_observed: str | None = None,
    region: str | None = None,
    landing_url: str | None = None,
    title: str | None = None,
    raw_text: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    is_partial: bool = False,
) -> RawArtifact:
    """Build one normalized public-ad artifact.

    The §37.12 public-ad shape rides in ``metadata``. ``creative_hash`` is a
    content hash of the creative body *only when a creative body is present* —
    a discovery pointer with no parsed creative gets ``None`` rather than a hash
    of nothing. No spend / performance field is ever written here.
    """
    stored_text = raw_text if raw_text is not None else (creative_body or "")
    creative_hash = content_hash(creative_body) if creative_body else None
    metadata: dict[str, Any] = {
        "advertiser": advertiser,
        "platform_or_surface": platform_or_surface,
        "creative_body": creative_body,
        "headline": headline,
        "cta": cta,
        "format": ad_format,
        "first_observed": first_observed,
        "last_observed": last_observed,
        "region": region,
        "landing_url": landing_url,
        "creative_hash": creative_hash,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return RawArtifact(
        artifact_id=new_id("ART"),
        company_id=action.company_id,
        source_type=source_type,
        source_name=source_name,
        url=url,
        final_url=url,
        title=title or headline,
        retrieved_at=utcnow(),
        time_window_ids=list(action.time_window_ids),
        raw_text=stored_text,
        normalized_text=normalize_text(stored_text),
        content_hash=content_hash(stored_text),
        metadata=metadata,
        collection_method=collection_method,
        is_partial=is_partial,
    )


def _ad_record_artifact(
    action: ResearchAction,
    *,
    source_type: str,
    platform_surface: str,
    collection_method: str,
    record: AdRecord,
    provenance: dict[str, Any],
) -> RawArtifact:
    """Persist one extracted/mapped AdRecord as an artifact.

    ``metadata`` carries BOTH the normalized §37.12 public-ad shape (so the
    report / paid-search rollups see it with zero changes) and the full
    ``AdRecord`` under ``ad_record``. Creative rows never carry
    ``is_discovery_pointer`` so ``is_junk_ads_artifact`` passes them through.
    """
    return _public_ad_artifact(
        action,
        source_type=source_type,
        source_name=source_type,
        collection_method=collection_method,
        url=record.source_url,
        advertiser=record.advertiser,
        platform_or_surface=platform_surface,
        creative_body=record.creative_text,
        headline=record.headline,
        cta=record.cta,
        ad_format=record.format,
        first_observed=record.first_seen,
        last_observed=record.last_seen,
        region=", ".join(record.regions) if record.regions else None,
        landing_url=record.landing_url,
        title=record.headline or f"{record.advertiser} ad creative",
        extra_metadata={
            "ad_record": record.model_dump(),
            "active": record.active,
            "impression_bucket": record.impression_bucket,
            "extraction_confidence": record.extraction_confidence,
            **provenance,
        },
    )


def _exa_cost(data: dict[str, Any]) -> float:
    try:
        return float((data.get("costDollars") or {}).get("total") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _impression_bucket(value: Any) -> str | None:
    """Render the Meta API's impressions bucket ({lower_bound, upper_bound}) as
    the range string the library shows. Missing/precise-looking values -> None:
    only buckets are claimable, never counts."""
    if not isinstance(value, dict):
        return None
    lower = str(value.get("lower_bound") or "").strip()
    upper = str(value.get("upper_bound") or "").strip()
    if lower and upper:
        return f"{lower}-{upper}"
    if lower:
        return f"{lower}+"
    return None


def _meta_active_status(start_time: str | None, stop_time: str | None) -> bool | None:
    """Delivery status claimable from the Meta API's start/stop times.

    Running ads show no stop time -> active=True (when a start proves the ad
    delivered at all). A present stop time only proves "ended" when it parses
    and is in the PAST — the API can return a future scheduled stop for an ad
    that is still delivering, and claiming inactive there would be false.
    """
    if not start_time:
        return None
    if stop_time is None:
        return True
    from datetime import datetime

    raw = stop_time.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None  # unparseable stop time: status unknown, never claimed
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return False if parsed <= utcnow() else None


class _AdLibraryTool(BaseTool):
    """Shared Exa/web collection path for public ad-library surfaces.

    Subclasses set the platform knobs and may override ``_is_junk_discovery``
    for library-specific junk rules. Fixture mode never reaches this code —
    the BaseTool boundary dispatches fixtures before ``_execute_live``, so the
    web path (network + model calls) only ever runs in live/cached-fallback
    modes.
    """

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ()
    PLATFORM: ClassVar[str] = "other"  # AdRecord.platform value
    PLATFORM_SURFACE: ClassVar[str] = ""  # metadata platform_or_surface
    LIBRARY_INCLUDE_DOMAINS: ClassVar[tuple[str, ...]] = ()
    LIBRARY_LABEL: ClassVar[str] = ""
    # Library-specific provider-reality line appended to EMPTY results so the
    # honesty surfaces say precisely WHY the library yielded nothing.
    EMPTY_REALITY_NOTE: ClassVar[str] = ""

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    # ---- per-library hooks ---------------------------------------------------

    def _initial_query(self, advertiser: str) -> str:
        return f"{advertiser} ads"

    def _is_junk_discovery(self, url: str, advertiser_domain: str) -> bool:
        """Library-specific junk rule for discovery URLs; default keeps all
        (the strict advertiser-scope filter still applies)."""
        return False

    # ---- seams (monkeypatched in tests; never called in fixture mode) --------

    def _build_gateway(self, context: ToolContext) -> Any | None:
        """Model gateway for structured extraction. None => extraction is
        skipped with a typed honest note (discovery pointers only) — records
        are never fabricated to fill the gap."""
        if not (context.settings.anthropic_api_key or "").strip():
            return None
        try:
            from ..model_gateway import build_gateway

            return build_gateway(context.mode, context.settings, context.config)
        except Exception:
            return None

    async def _exa_post(self, url: str, payload: dict[str, Any], api_key: str) -> httpx.Response:
        # Direct provider call by design (matches exa_search): api.exa.ai needs
        # the x-api-key header and is not a public-URL fetch. Key never logged.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_EXA_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key},
        ) as client:
            return await retry_async(
                lambda: client.post(url, json=payload),
                retries=_EXA_MAX_RETRIES,
                base_delay=_EXA_RETRY_BASE_DELAY,
            )

    # ---- Exa plumbing ----------------------------------------------------------

    def _exa_error(self, action: ResearchAction, response: httpx.Response) -> ToolResult | None:
        """Map Exa HTTP status to a typed failure; None means proceed."""
        code = response.status_code
        if code == 403 and "domains are not available" in (response.text or ""):
            # Exa does not index this library's domain at all (verified live:
            # facebook.com). Not an auth problem — an honest provider boundary:
            # the only Meta path is the official API token.
            return self._result(
                action,
                status="unsupported",
                error_type="provider_domain_unindexed",
                error_message=(
                    f"Exa does not index {', '.join(self.LIBRARY_INCLUDE_DOMAINS)} — "
                    "the public-web path cannot reach this ad library. Set "
                    "META_ADS_ACCESS_TOKEN for the official Meta Ad Library API."
                ),
            )
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

    async def _exa_contents_text(self, url: str, api_key: str) -> str:
        """exa_contents fallback — used ONLY after a first-party HTTP failure
        left us with no page text (never a robots bypass)."""
        try:
            response = await self._exa_post(
                EXA_CONTENTS_URL, {"urls": [url], "text": True}, api_key
            )
            if response.status_code >= 400:
                return ""
            data = response.json()
        except Exception:
            return ""
        for item in data.get("results") or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                return str(item.get("text"))
        return ""

    async def _page_text(
        self, context: ToolContext, url: str, exa_text: str, api_key: str
    ) -> tuple[str, str]:
        """Best available page text for extraction, with honest provenance.

        Order: first-party fetch (SharedHttp, robots-respecting) -> on HTTP
        failure, exa_contents fallback (only when the Exa discovery snippet is
        empty) -> the Exa discovery snippet. Robots-disallowed pages are NOT
        routed through exa_contents: we respect robots ourselves.
        """
        http = context.http
        fetch_failed = False
        if http is not None:
            try:
                if await http.robots.is_allowed(url):
                    response = await http.get(url)
                    if response.status_code < 400:
                        text = html_to_text(response.text)
                        if text and text.strip():
                            return text, "first_party_fetch"
                        fetch_failed = True
                    else:
                        fetch_failed = True
            except Exception:
                fetch_failed = True
        if fetch_failed and not exa_text.strip():
            contents_text = await self._exa_contents_text(url, api_key)
            if contents_text:
                return contents_text, "exa_contents_fallback"
        return exa_text, "exa_search_text"

    # ---- model extraction --------------------------------------------------------

    async def _extract(
        self,
        gateway: Any,
        prompt: Any,
        *,
        advertiser: str,
        advertiser_domain: str,
        page_url: str,
        page_text: str,
        queries_run: list[str],
        notes: list[str],
    ) -> tuple[AdIntelligence | None, float]:
        """One bounded extraction call; failures degrade to a note, never a crash."""
        user_content = prompt.render(
            advertiser=advertiser,
            advertiser_domain=advertiser_domain or "(unknown)",
            library=self.LIBRARY_LABEL,
            page_url=page_url,
            queries_already_run="\n".join(f"- {q}" for q in queries_run) or "(none)",
            content=page_text[:_MAX_PAGE_TEXT_CHARS],
        )
        try:
            result = await gateway.generate_structured(
                AD_INTEL_TASK,
                AD_INTEL_SYSTEM,
                user_content,
                AdIntelligence,
                prompt_name=getattr(prompt, "name", AD_INTEL_PROMPT_NAME),
                prompt_version=getattr(prompt, "version", ""),
            )
        except Exception as exc:  # noqa: BLE001 - typed degrade, never propagate
            notes.append(
                f"ad_intelligence extraction failed for {page_url} "
                f"({type(exc).__name__}); page kept as discovery pointer only."
            )
            return None, 0.0
        output = result.output
        if not isinstance(output, AdIntelligence):  # defensive: schema drift
            return None, float(result.cost_usd)
        return output, float(result.cost_usd)

    # ---- the web path --------------------------------------------------------------

    async def _web_path(
        self,
        action: ResearchAction,
        context: ToolContext,
        *,
        advertiser: str,
        advertiser_domain: str,
        api_key: str,
        pre_notes: list[str] | None = None,
    ) -> ToolResult:
        notes: list[str] = list(pre_notes or [])
        artifacts: list[RawArtifact] = []
        cost_usd = 0.0
        dropped_scope = 0
        dropped_junk = 0
        dropped_no_url = 0
        dropped_uncontained = 0
        corrected_provenance = 0
        kept_records = 0
        extraction_pages = 0
        next_iterations = 0
        seen_urls: set[str] = set()
        seen_creative_hashes: set[str] = set()
        queries_run: list[str] = []
        pending: list[str] = [self._initial_query(advertiser)]
        region = str(action.parameters.get("region") or "").strip() or None
        num_results = int(action.parameters.get("num_results") or _DEFAULT_NUM_RESULTS)

        gateway = self._build_gateway(context)
        prompt: Any = None
        if gateway is not None:
            try:
                from ..prompt_registry import PromptRegistry

                prompt = PromptRegistry().get(AD_INTEL_PROMPT_NAME)
            except Exception as exc:  # noqa: BLE001 - typed degrade
                gateway = None
                notes.append(
                    f"ad_intelligence prompt unavailable ({type(exc).__name__}); "
                    "structured extraction skipped — discovery pointers only."
                )
        else:
            notes.append(
                "ad_intelligence extraction skipped: no model gateway configured "
                "(anthropic_api_key not set) — discovery pointers only, no ad "
                "records fabricated."
            )
        extraction_enabled = gateway is not None and prompt is not None

        while pending:
            query = pending.pop(0).strip()
            if not query or query.casefold() in {q.casefold() for q in queries_run}:
                continue
            is_initial = not queries_run
            if not is_initial:
                # The TOOL owns the loop bound over model-proposed queries.
                if next_iterations >= _MAX_NEXT_QUERY_ITERATIONS:
                    break
                next_iterations += 1
            queries_run.append(query)

            payload: dict[str, Any] = {
                "query": query,
                "numResults": num_results,
                "contents": {"text": True},
                "includeDomains": list(self.LIBRARY_INCLUDE_DOMAINS),
            }
            response = await self._exa_post(EXA_SEARCH_URL, payload, api_key)
            error = self._exa_error(action, response)
            if error is not None:
                if is_initial:
                    if notes:
                        error.negative_observations = [
                            *notes,
                            *error.negative_observations,
                        ]
                    return error
                notes.append(
                    f"follow-up Exa query '{query}' failed "
                    f"({error.error_type}); next-query loop stopped early."
                )
                break
            try:
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                if is_initial:
                    return self._result(
                        action,
                        status="failed_terminal",
                        error_type="invalid_response",
                        error_message=f"Exa response was not valid JSON: {type(exc).__name__}",
                        negative_observations=notes,
                    )
                notes.append(f"follow-up Exa query '{query}' returned invalid JSON; loop stopped.")
                break
            cost_usd += _exa_cost(data)

            provenance: dict[str, Any] = {
                "exa_query": query,
                "exa_filters": {
                    "includeDomains": list(self.LIBRARY_INCLUDE_DOMAINS),
                    "numResults": num_results,
                },
                "region": region,
                "discovery_via": "exa_search",
            }

            page_budget = _MAX_PAGES_PER_ITERATION
            for item in data.get("results") or []:
                if page_budget <= 0:
                    break
                if not isinstance(item, dict) or not str(item.get("url") or "").strip():
                    dropped_no_url += 1
                    continue
                url = str(item.get("url"))
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                title = str(item.get("title") or "")
                exa_text = str(item.get("text") or "")
                # Library-specific junk rule (FAQ pages, blank-query pages,
                # explicit other-advertiser pages).
                if self._is_junk_discovery(url, advertiser_domain):
                    dropped_junk += 1
                    continue
                # STRICT advertiser scoping: the result must match the
                # competitor domain or advertiser name, case-insensitively.
                haystack = f"{title}\n{url}\n{exa_text[:4000]}"
                if not matches_advertiser_scope(haystack, advertiser, advertiser_domain):
                    dropped_scope += 1
                    continue
                page_budget -= 1

                page_text, text_via = await self._page_text(context, url, exa_text, api_key)

                artifacts.append(
                    _public_ad_artifact(
                        action,
                        source_type=self.name,
                        source_name=self.name,
                        collection_method="exa_discovery",
                        url=url,
                        advertiser=advertiser,
                        platform_or_surface=self.PLATFORM_SURFACE,
                        # A discovery pointer, not a parsed creative.
                        creative_body=None,
                        headline=title or None,
                        region=region,
                        title=title or None,
                        raw_text=page_text,
                        extra_metadata={
                            **provenance,
                            "exa_id": item.get("id"),
                            "is_discovery_pointer": True,
                            "advertiser_verified": True,  # enforced by the scope filter
                            "page_text_via": text_via,
                        },
                        is_partial=True,
                    )
                )

                if (
                    not extraction_enabled
                    or extraction_pages >= _MAX_EXTRACTION_PAGES
                    or not page_text.strip()
                ):
                    continue
                extraction_pages += 1
                intelligence, model_cost = await self._extract(
                    gateway,
                    prompt,
                    advertiser=advertiser,
                    advertiser_domain=advertiser_domain,
                    page_url=url,
                    page_text=page_text,
                    queries_run=queries_run,
                    notes=notes,
                )
                cost_usd += model_cost
                if intelligence is None:
                    continue
                for record in intelligence.ads:
                    if not record_in_scope(record, advertiser, advertiser_domain):
                        dropped_scope += 1
                        continue
                    # Accuracy gate: the creative must appear verbatim in the
                    # page text we actually fetched, or the record is dropped.
                    if not contains_excerpt(page_text, record.creative_text):
                        dropped_uncontained += 1
                        notes.append(
                            f"dropped 1 extracted ad record ('{record.advertiser}', "
                            f"{url}): creative_text not found verbatim in the "
                            "fetched page text."
                        )
                        continue
                    creative_hash = content_hash(record.creative_text)
                    if creative_hash in seen_creative_hashes:
                        continue
                    seen_creative_hashes.add(creative_hash)
                    # Defensive provenance repair, never a drop: source_url is
                    # the artifact's canonical URL, so a blank/invalid value
                    # becomes the page we extracted FROM; a platform that is
                    # not this tool's is set to the tool's own.
                    record, corrected = repair_record_provenance(
                        record, platform=self.PLATFORM, page_url=url
                    )
                    if corrected:
                        corrected_provenance += 1
                    kept_records += 1
                    artifacts.append(
                        _ad_record_artifact(
                            action,
                            source_type=self.name,
                            platform_surface=self.PLATFORM_SURFACE,
                            collection_method="ad_intelligence_extraction",
                            record=record,
                            provenance={
                                **provenance,
                                "extraction_page_url": url,
                                "page_text_via": text_via,
                            },
                        )
                    )
                for next_query in intelligence.next_queries[:_MAX_NEXT_QUERIES_PER_PAGE]:
                    if isinstance(next_query, str) and next_query.strip():
                        pending.append(next_query.strip())

        if dropped_scope:
            notes.append(
                f"{dropped_scope} result(s)/record(s) dropped by strict advertiser "
                f"scoping for '{advertiser}' "
                f"({advertiser_domain or 'no domain supplied'}): not attributable "
                "to the competitor."
            )
        if dropped_junk:
            notes.append(
                f"{dropped_junk} {self.LIBRARY_LABEL} result(s) dropped as "
                f"non-advertiser pages (FAQ/blank/other-advertiser) for '{advertiser}'."
            )
        if dropped_no_url:
            notes.append(f"{dropped_no_url} Exa result(s) without a URL were dropped.")
        if dropped_uncontained:
            notes.append(
                f"{dropped_uncontained} extracted record(s) dropped in total: "
                "creative_text failed verbatim containment against the fetched page text."
            )
        if corrected_provenance:
            notes.append(
                f"{corrected_provenance} record(s) had platform/source_url "
                "corrected to the extraction page."
            )

        if not artifacts:
            notes.append(
                f"No {self.LIBRARY_LABEL} pages attributable to '{advertiser}' were "
                f"found (queries: {queries_run}) — a coverage gap, not evidence of "
                "no advertising."
            )
            if self.EMPTY_REALITY_NOTE:
                notes.append(self.EMPTY_REALITY_NOTE)
            return self._result(
                action, status="empty", cost_usd=cost_usd, negative_observations=notes
            )
        if extraction_enabled and not kept_records:
            notes.append(
                f"No structured ad records were extracted for '{advertiser}' "
                "(discovery pointers only)."
            )
        clean = not (dropped_scope or dropped_junk or dropped_no_url or dropped_uncontained)
        status = "success" if (kept_records and clean) else "partial"
        return self._result(
            action,
            status=status,
            artifacts=artifacts,
            cost_usd=cost_usd,
            negative_observations=notes,
        )


class GoogleAdsTool(_AdLibraryTool):
    """Google Ads Transparency Center — advertiser-scoped public creative discovery.

    Live path: Exa discovery of ``adstransparency.google.com`` references for
    the advertiser (STRICTLY scoped to the competitor), first-party fetch, and
    structured extraction via the ``ad_intelligence`` prompt. No Exa key
    configured -> typed ``unsupported``. Never infers bid keywords or
    performance from the transparency corpus.
    """

    name: ClassVar[str] = "google_ads"
    adapter_version: ClassVar[str] = "0.2.0"
    source_flag_name: ClassVar[str] = "google_ads"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_google_ads",)
    PLATFORM: ClassVar[str] = "google"
    PLATFORM_SURFACE: ClassVar[str] = "google_ads_transparency"
    LIBRARY_INCLUDE_DOMAINS: ClassVar[tuple[str, ...]] = (_TRANSPARENCY_DOMAIN,)
    LIBRARY_LABEL: ClassVar[str] = "Google Ads Transparency Center"
    EMPTY_REALITY_NOTE: ClassVar[str] = (
        "Provider reality (verified live 2026-07-13): the Transparency Center "
        "is a JavaScript application — advertiser/creative pages return only "
        "an app shell to public fetchers (~600 chars, no creatives), so live "
        "extraction needs a browser path, which this run's config disables. "
        "Creatives visible in a browser are real; they are just not "
        "server-collectable here."
    )

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
                "Google Ads Transparency Center is a creative repository, not an "
                "analytics API: it exposes creatives but never spend, ROAS, CPA, "
                "conversions, revenue, or bid keywords (§39.7).",
                "There is no stable public API; live collection is best-effort Exa "
                "discovery of adstransparency.google.com references and may miss "
                "creatives — absence is never evidence the advertiser is not "
                "running ads (§39.7).",
                "No keyword-bidding or performance conclusions may be drawn from "
                "the transparency creative corpus.",
                "Extracted records claim only what the library visibly shows "
                "(creatives, formats, regions, run dates, active status, "
                "impression buckets); every creative is containment-verified "
                "against the fetched page text before it is kept.",
            ],
        )

    def _is_junk_discovery(self, url: str, advertiser_domain: str) -> bool:
        from ..synthesis import is_junk_ads_artifact

        return is_junk_ads_artifact(url, {"is_discovery_pointer": True}, advertiser_domain)

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message=(
                    "Google Ads Transparency has no stable public API; best-effort "
                    "live discovery requires an Exa API key (exa_api_key), which is "
                    "not set."
                ),
                negative_observations=[
                    f"Google Ads Transparency discovery not attempted for "
                    f"'{action.action_type}': no Exa API key configured (this is a "
                    "coverage gap, not evidence of no advertising)."
                ],
            )

        advertiser = str(
            action.parameters.get("advertiser") or action.parameters.get("query") or ""
        ).strip()
        if not advertiser:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message="parameter 'advertiser' (or 'query') is required for "
                "search_google_ads",
            )
        advertiser_domain = str(
            action.parameters.get("advertiser_domain") or action.parameters.get("domain") or ""
        ).strip()

        return await self._web_path(
            action,
            context,
            advertiser=advertiser,
            advertiser_domain=advertiser_domain,
            api_key=api_key,
        )


class MetaAdsTool(_AdLibraryTool):
    """Meta / Instagram Ad Library — dual path.

    Official-API seam: the Ad Library Graph API (``ads_archive``) when
    ``META_ADS_ACCESS_TOKEN`` is present (worldwide political/social-issue ads
    + EU commercial transparency: creative bodies, platforms, delivery dates,
    impression BUCKETS). Token absent -> typed skip note and the public-web
    path (advertiser-scoped Exa discovery of facebook.com/ads/library pages +
    structured extraction). Only observed labels are captured — never spend,
    delivery allocation, or performance.
    """

    name: ClassVar[str] = "meta_ads"
    adapter_version: ClassVar[str] = "0.2.0"
    source_flag_name: ClassVar[str] = "meta_ads"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_meta_ads",)
    PLATFORM: ClassVar[str] = "meta"
    PLATFORM_SURFACE: ClassVar[str] = "meta_ad_library"
    LIBRARY_INCLUDE_DOMAINS: ClassVar[tuple[str, ...]] = (_META_LIBRARY_DOMAIN,)
    LIBRARY_LABEL: ClassVar[str] = "Meta Ad Library"

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
                "The Meta Ad Library API covers worldwide political/social-issue "
                "ads and UK/EU commercial ads within a supported window only; US "
                "commercial active ads are exposed through the public Ad Library "
                "UI only (§39.7).",
                "Official-API seam: ads_archive is used only when "
                "META_ADS_ACCESS_TOKEN is set; otherwise collection degrades to "
                "advertiser-scoped public-web discovery with a typed skip note — "
                "nothing is fabricated.",
                "Platform fields (e.g. Instagram presence) show observed publisher "
                "platforms, not spend or delivery allocation (§39.7).",
                "Impression figures are BUCKETS the library shows (EU "
                "transparency), never precise counts; no spend, ROAS, CPA, "
                "conversion, or revenue figures are available or claimed.",
            ],
        )

    def _initial_query(self, advertiser: str) -> str:
        return f"{advertiser} ad library ads"

    # ---- official-API seam -----------------------------------------------------

    async def _meta_get(self, params: dict[str, str]) -> httpx.Response:
        # Direct provider call: graph.facebook.com with the access token as a
        # query parameter (Graph API convention). The token is never logged or
        # persisted; error paths emit generic messages.
        async with httpx.AsyncClient(timeout=httpx.Timeout(_META_TIMEOUT_SECONDS)) as client:
            return await retry_async(
                lambda: client.get(META_ADS_ARCHIVE_URL, params=params),
                retries=_EXA_MAX_RETRIES,
                base_delay=_EXA_RETRY_BASE_DELAY,
            )

    async def _meta_api_path(
        self,
        action: ResearchAction,
        *,
        advertiser: str,
        advertiser_domain: str,
        token: str,
    ) -> ToolResult:
        region = str(action.parameters.get("region") or "").strip() or "US"
        params: dict[str, str] = {
            "search_terms": advertiser,
            "search_type": "KEYWORD_UNORDERED",
            "ad_type": "ALL",
            "ad_active_status": "ALL",
            "ad_reached_countries": json.dumps([region]),
            "fields": ",".join(_META_ARCHIVE_FIELDS),
            "limit": "25",
            "access_token": token,
        }
        try:
            response = await self._meta_get(params)
        except Exception as exc:  # noqa: BLE001 - generic message; token never surfaced
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
            return self._result(
                action,
                status="failed_retryable" if retryable else "failed_terminal",
                error_type=type(exc).__name__,
                error_message=f"Meta ads_archive request failed: {type(exc).__name__}",
                retryable=retryable,
            )

        code = response.status_code
        if code in (401, 403):
            return self._result(
                action,
                status="failed_terminal",
                error_type="provider_auth",
                error_message=f"Meta Graph API rejected the access token (HTTP {code}).",
            )
        if code == 429:
            return self._result(
                action,
                status="failed_retryable",
                error_type="rate_limited",
                error_message="Meta Graph API rate limit hit (HTTP 429).",
                retryable=True,
            )
        if code >= 500:
            return self._result(
                action,
                status="failed_retryable",
                error_type="provider_5xx",
                error_message=f"Meta Graph API server error (HTTP {code}) after retries.",
                retryable=True,
            )
        if code >= 400:
            return self._result(
                action,
                status="failed_terminal",
                error_type=f"provider_http_{code}",
                error_message=(
                    f"Meta ads_archive returned HTTP {code} (commercial coverage is "
                    "EU-window only; permission or parameter error)."
                ),
            )
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_response",
                error_message=f"Meta ads_archive response was not valid JSON: {type(exc).__name__}",
            )

        items = data.get("data") or []
        artifacts: list[RawArtifact] = []
        dropped_scope = 0
        skipped_bodyless = 0
        seen_creative_hashes: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            page_name = str(item.get("page_name") or "").strip()
            bodies = [
                b
                for b in (item.get("ad_creative_bodies") or [])
                if isinstance(b, str) and b.strip()
            ]
            if not bodies:
                skipped_bodyless += 1
                continue
            # STRICT advertiser scoping on the platform's own advertiser label.
            if not matches_advertiser_scope(page_name, advertiser, advertiser_domain):
                dropped_scope += 1
                continue
            creative = bodies[0].strip()
            # Uniform accuracy gate: the creative must be verbatim in the
            # platform record we received (trivially true here by construction,
            # but the invariant is checked, not assumed).
            titles = [
                t
                for t in (item.get("ad_creative_link_titles") or [])
                if isinstance(t, str) and t.strip()
            ]
            record_source_text = "\n".join([*bodies, *titles])
            if not contains_excerpt(record_source_text, creative):
                skipped_bodyless += 1
                continue
            creative_hash = content_hash(creative)
            if creative_hash in seen_creative_hashes:
                continue
            seen_creative_hashes.add(creative_hash)
            ad_id = str(item.get("id") or "").strip()
            stop_time = str(item.get("ad_delivery_stop_time") or "").strip() or None
            start_time = str(item.get("ad_delivery_start_time") or "").strip() or None
            record = AdRecord(
                advertiser=page_name or advertiser,
                platform="meta",
                creative_text=creative,
                headline=titles[0] if titles else None,
                cta=None,
                format=None,
                regions=[region],
                first_seen=start_time,
                last_seen=stop_time,
                # Library semantics: a running ad shows no delivery stop time.
                # A present stop time only proves "ended" once it is verifiably
                # in the past — the API can return a future SCHEDULED stop for
                # an ad still delivering; that case stays unknown rather than
                # a false "inactive" claim.
                active=_meta_active_status(start_time, stop_time),
                impression_bucket=_impression_bucket(item.get("impressions")),
                landing_url=None,
                # Public permalink built from the ad id — NEVER ad_snapshot_url,
                # which embeds the access token.
                source_url=f"https://www.facebook.com/ads/library/?id={ad_id}"
                if ad_id
                else "https://www.facebook.com/ads/library/",
                extraction_confidence="high",
            )
            publisher_platforms = [
                p
                for p in (item.get("publisher_platforms") or [])
                if isinstance(p, str) and p.strip()
            ]
            artifacts.append(
                _ad_record_artifact(
                    action,
                    source_type=self.name,
                    platform_surface=self.PLATFORM_SURFACE,
                    collection_method="meta_ads_archive_api",
                    record=record,
                    provenance={
                        "discovery_via": "meta_ads_archive_api",
                        "ad_library_id": ad_id or None,
                        "publisher_platforms": publisher_platforms,
                        "ad_reached_countries": [region],
                        "platform_note": (
                            "observed publisher platforms only; NOT spend or "
                            "delivery allocation (§39.7)."
                        ),
                    },
                )
            )

        notes: list[str] = []
        if dropped_scope:
            notes.append(
                f"{dropped_scope} ads_archive record(s) dropped by strict advertiser "
                f"scoping for '{advertiser}' ({advertiser_domain or 'no domain supplied'})."
            )
        if skipped_bodyless:
            notes.append(
                f"{skipped_bodyless} ads_archive record(s) without a usable creative "
                "body were skipped."
            )
        if not artifacts:
            notes.append(
                f"Meta ads_archive returned no usable commercial records for "
                f"'{advertiser}' in {region} (API coverage is political/social-issue "
                "worldwide + EU commercial windows only) — a coverage gap, not "
                "evidence of no advertising."
            )
            return self._result(action, status="empty", negative_observations=notes)
        return self._result(
            action,
            status="partial" if (dropped_scope or skipped_bodyless) else "success",
            artifacts=artifacts,
            negative_observations=notes,
        )

    # ---- live dispatch -----------------------------------------------------------

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        advertiser = str(
            action.parameters.get("advertiser") or action.parameters.get("query") or ""
        ).strip()
        if not advertiser:
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_parameters",
                error_message="parameter 'advertiser' (or 'query') is required for search_meta_ads",
            )
        advertiser_domain = str(
            action.parameters.get("advertiser_domain") or action.parameters.get("domain") or ""
        ).strip()

        notes: list[str] = []
        token = secret_from_env_or_settings(META_ADS_TOKEN_ENV)
        if token:
            api_result = await self._meta_api_path(
                action,
                advertiser=advertiser,
                advertiser_domain=advertiser_domain,
                token=token,
            )
            if api_result.artifacts:
                return api_result
            notes.extend(api_result.negative_observations)
            notes.append(
                f"Meta ads_archive API path yielded no records "
                f"(status '{api_result.status}'); falling back to public-web discovery."
            )
        else:
            # Exact honest reason for skipping the official-API seam.
            notes.append(NO_META_TOKEN_NOTE)

        api_key = (context.settings.exa_api_key or "").strip()
        if not api_key:
            return self._result(
                action,
                status="unsupported",
                error_type="provider_not_configured",
                error_message=(
                    "Meta ads collection not configured: "
                    + (
                        "no META_ADS_ACCESS_TOKEN for the official API, and "
                        if not token
                        else "the ads_archive API returned nothing, and "
                    )
                    + "the public-web path requires an Exa API key (exa_api_key), "
                    "which is not set."
                ),
                negative_observations=[
                    *notes,
                    "Meta Ad Library public-web discovery not attempted: no Exa API "
                    "key configured (a coverage gap, not evidence of no advertising).",
                ],
            )
        return await self._web_path(
            action,
            context,
            advertiser=advertiser,
            advertiser_domain=advertiser_domain,
            api_key=api_key,
            pre_notes=notes,
        )


class LinkedInAdsTool(BaseTool):
    """LinkedIn paid advertising — interface-only / best-effort.

    Live mode returns ``unsupported``: LinkedIn's public ad interface has no
    stable API and must never be report-critical (§39.7). Coverage, when
    present, comes from recorded fixtures or compliant capture.
    """

    name: ClassVar[str] = "linkedin_ads"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "linkedin_ads"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_linkedin_ads",)
    UNSUPPORTED_MESSAGE: ClassVar[str] = (
        "LinkedIn's public ad interface has no stable API; collection is "
        "best-effort (public pages, compliant browser automation, Exa "
        "discovery, or recorded fixtures) and is never report-critical "
        "(§39.7). No live route is configured."
    )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=False,
            fixture_available=False,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=False,
            returns_estimates=False,
            known_limitations=[
                "LinkedIn's public ad interface has no stable API; collection is "
                "best-effort (public pages, compliant browser automation, Exa "
                "discovery, or recorded fixtures) and must never be "
                "report-critical (§39.7).",
                "Availability varies by geography and the interface is brittle to "
                "automate; absence of a discovered ad is not evidence the "
                "advertiser is not advertising on LinkedIn.",
                "No spend, delivery, reach, ROAS, CPA, conversion, or revenue "
                "figures are available or claimed.",
            ],
        )

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        return self._result(
            action,
            status="unsupported",
            error_type="interface_only",
            error_message=self.UNSUPPORTED_MESSAGE,
            negative_observations=[
                f"{self.name}: live collection is not configured "
                f"({self.UNSUPPORTED_MESSAGE}); no creatives were fabricated. Provide "
                "a recorded fixture or compliant capture for coverage."
            ],
        )
