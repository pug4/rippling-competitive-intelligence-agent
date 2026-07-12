"""Advertising adapters: Google Ads Transparency + Meta / LinkedIn ad interface.

Blueprint §12.5 / §12.6, §37.12 "Advertising adapters" (lines 4553-4584), and
§39.7 provider-reality (lines 8993-9034).

Ad libraries have NO stable public API for commercial ads:
- Google Ads Transparency Center is a *creative repository* searchable by
  advertiser/website with region + date filters; there is no stable public API,
  and it never exposes spend, performance, or bid keywords.
- The Meta Ad Library API covers worldwide political/social-issue ads and UK/EU
  commercial ads within a supported window only; US commercial *active* ads are
  reachable through the public Ad Library UI only.
- LinkedIn's public ad interface is best-effort and must never be
  report-critical.

These are therefore implemented as INTERFACE + FIXTURE adapters that are honest
about coverage. All three normalize to ONE public-ad shape carried in
``RawArtifact.metadata`` (blueprint §37.12):

    advertiser, platform_or_surface, creative_body, headline, cta, format,
    first_observed, last_observed, region, landing_url, creative_hash

do_not_claim (§37.12 lines 4576-4584 — never emitted, never inferred, unless the
result contains a direct, attributable, public disclosure):

    ROAS, CPA, conversion rate, revenue, true spend, winning creative,
    bid keywords, delivery / reach / impression allocation

When live collection is not configured (no Exa key for Google; interface-only
for Meta / LinkedIn), the adapter degrades to ``unsupported`` — it never
fabricates creatives. Deterministic recorded creatives live in the fixtures.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from ..processing.normalize import content_hash, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext
from .http import retry_async

# Google Ads Transparency has no public API. Best-effort live discovery reuses
# the Exa Search API (same direct-provider contract as ``exa_search``): the
# x-api-key header, a short timeout, and bounded retries on 5xx / timeout.
EXA_SEARCH_URL = "https://api.exa.ai/search"
_EXA_TIMEOUT_SECONDS = 20.0
_EXA_MAX_RETRIES = 2
_EXA_RETRY_BASE_DELAY = 0.5
_DEFAULT_NUM_RESULTS = 8

# Google Ads Transparency Center host — the only domain live discovery scopes to.
_TRANSPARENCY_DOMAIN = "adstransparency.google.com"

# The normalized public-ad metadata keys shared by all three adapters (§37.12).
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


class GoogleAdsTool(BaseTool):
    """Google Ads Transparency Center — best-effort public creative discovery.

    Live path attempts an Exa discovery of ``adstransparency.google.com``
    references for the advertiser. No Exa key configured -> ``unsupported``.
    Never infers bid keywords or performance from the transparency corpus.
    """

    name: ClassVar[str] = "google_ads"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "google_ads"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_google_ads",)

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
                "Historical first/last-observed dates are not recovered by "
                "discovery; only the observed reference is recorded.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

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

        region = str(action.parameters.get("region") or "").strip() or None
        num_results = int(action.parameters.get("num_results") or _DEFAULT_NUM_RESULTS)
        exa_query = f"{advertiser} ads"
        payload: dict[str, Any] = {
            "query": exa_query,
            "numResults": num_results,
            "contents": {"text": True},
            "includeDomains": [_TRANSPARENCY_DOMAIN],
        }
        # Provenance recorded on every artifact: the EXACT query + filters, incl.
        # the requested region (a provenance note, not an Exa API parameter).
        provenance: dict[str, Any] = {
            "exa_query": exa_query,
            "exa_filters": {"includeDomains": [_TRANSPARENCY_DOMAIN], "numResults": num_results},
            "region": region,
            "discovery_via": "exa_search",
        }

        response = await self._exa_post(payload, api_key)

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
        except Exception as exc:  # noqa: BLE001 - typed failure, never propagate
            return self._result(
                action,
                status="failed_terminal",
                error_type="invalid_response",
                error_message=f"Exa response was not valid JSON: {type(exc).__name__}",
            )

        return self._map_discovery(action, advertiser, provenance, data)

    async def _exa_post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        # Direct provider call by design (matches exa_search): api.exa.ai needs
        # the x-api-key header and is not a public-URL fetch. Key never logged.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_EXA_TIMEOUT_SECONDS),
            headers={"x-api-key": api_key},
        ) as client:
            return await retry_async(
                lambda: client.post(EXA_SEARCH_URL, json=payload),
                retries=_EXA_MAX_RETRIES,
                base_delay=_EXA_RETRY_BASE_DELAY,
            )

    def _map_discovery(
        self,
        action: ResearchAction,
        advertiser: str,
        provenance: dict[str, Any],
        data: dict[str, Any],
    ) -> ToolResult:
        results = data.get("results") or []
        artifacts: list[RawArtifact] = []
        skipped = 0
        for item in results:
            if not isinstance(item, dict) or not str(item.get("url") or "").strip():
                skipped += 1
                continue
            url = str(item.get("url"))
            page_text = str(item.get("text") or "")
            artifacts.append(
                _public_ad_artifact(
                    action,
                    source_type=self.name,
                    source_name=self.name,
                    collection_method="exa_discovery",
                    url=url,
                    advertiser=advertiser,
                    platform_or_surface="google_ads_transparency",
                    # A discovery pointer, not a parsed creative: creative_body,
                    # cta, format and first/last observed are unknown -> None.
                    creative_body=None,
                    headline=item.get("title") or None,
                    region=provenance["region"],
                    title=item.get("title") or None,
                    raw_text=page_text,
                    extra_metadata={
                        **provenance,
                        "exa_id": item.get("id"),
                        "is_discovery_pointer": True,
                    },
                    # Discovery pointer: page text is a proxy, not the creative.
                    is_partial=True,
                )
            )

        if not artifacts:
            return self._result(
                action,
                status="empty",
                negative_observations=[
                    f"Exa discovery returned no adstransparency.google.com references "
                    f"for advertiser '{advertiser}' (query='{provenance['exa_query']}') — "
                    "a coverage gap, not evidence of no advertising."
                ],
            )

        negative_observations: list[str] = []
        if skipped:
            negative_observations.append(
                f"{skipped} Exa discovery result(s) without a URL were dropped for "
                f"advertiser '{advertiser}'."
            )
        return self._result(
            action,
            status="partial" if skipped else "success",
            artifacts=artifacts,
            negative_observations=negative_observations,
        )


class _InterfaceOnlyAdsTool(BaseTool):
    """Shared base for interface-only ad adapters (Meta, LinkedIn).

    Live mode is deliberately not implemented against a general commercial-ad
    API — none exists (§39.7). Live collection therefore degrades to
    ``unsupported`` with an honest message; observed creatives come from
    recorded fixtures (fixture mode is dispatched by the boundary before
    ``_execute_live`` is ever called).
    """

    #: message returned by the live path explaining why it is unsupported.
    UNSUPPORTED_MESSAGE: ClassVar[str] = "interface-only adapter"
    ACTION_TYPES: ClassVar[tuple[str, ...]] = ()

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

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


class MetaAdsTool(_InterfaceOnlyAdsTool):
    """Meta / Instagram Ad Library — interface-only.

    Live mode returns ``unsupported``: the Meta API covers worldwide
    political/social-issue ads and UK/EU commercial ads within a supported
    window only, and US commercial *active* ads are UI-only (§39.7). Only
    observed publisher-platform labels are captured — never spend or delivery.
    """

    name: ClassVar[str] = "meta_ads"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "meta_ads"

    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("search_meta_ads",)
    UNSUPPORTED_MESSAGE: ClassVar[str] = (
        "US commercial active ads are exposed only through the public Meta Ad "
        "Library UI; the API covers worldwide political/social-issue ads and "
        "UK/EU commercial windows only — no general commercial-ad API exists "
        "(§39.7). Use a recorded fixture or compliant UI capture."
    )

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=False,
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
                "Interface-only: live mode returns 'unsupported'; observed "
                "creatives come from recorded fixtures / compliant UI capture, "
                "never a general commercial-ad API.",
                "Platform fields (e.g. Instagram presence) show observed publisher "
                "platforms, not spend or delivery allocation (§39.7).",
                "No spend, delivery, reach, ROAS, CPA, conversion, or revenue "
                "figures are available or claimed.",
            ],
        )


class LinkedInAdsTool(_InterfaceOnlyAdsTool):
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
