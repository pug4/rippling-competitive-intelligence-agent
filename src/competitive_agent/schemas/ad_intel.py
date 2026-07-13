"""Ad-intelligence extraction schemas (ADS contract).

``AdRecord`` is one observed public ad creative, extracted either from an
ad-library page (Google Ads Transparency Center, Meta Ad Library) by the
``ad_intelligence`` prompt or mapped from the Meta ``ads_archive`` API.
``AdIntelligence`` is the structured output of one extraction call: per-ad
records plus bounded follow-up queries the TOOL (never the model) may loop.

Honesty boundary (§37.12, §39.7, ADS contract): creatives, formats, regions,
run dates, active status, and impression BUCKETS are claimable when the
library visibly shows them. Exact bid keywords, CPC, commercial spend, and
CTR/CVR/ROAS are never claimable — this schema deliberately has no fields
for them, so they cannot be persisted even by a misbehaving model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import VersionedModel


class AdRecord(VersionedModel):
    """One observed public ad creative from an ad-library surface."""

    advertiser: str
    platform: Literal["google", "meta", "linkedin", "other"]
    # Verbatim creative body. Containment-verified by the caller against the
    # fetched page text (processing/normalize.contains_excerpt); a record
    # whose creative_text is not found verbatim is dropped, never kept.
    creative_text: str
    headline: str | None = None
    cta: str | None = None
    format: str | None = None
    regions: list[str] = Field(default_factory=list)
    # Library-shown run dates, as displayed (ISO date strings when shown).
    first_seen: str | None = None
    last_seen: str | None = None
    active: bool | None = None
    # Bucket/range EXACTLY as the library displays it (e.g. "10K-15K" for EU
    # transparency) — never a precise impression count.
    impression_bucket: str | None = None
    landing_url: str | None = None
    source_url: str
    extraction_confidence: Literal["high", "medium", "low"]


class AdIntelligence(VersionedModel):
    """Structured output of one ad_intelligence extraction call."""

    ads: list[AdRecord] = Field(default_factory=list)
    campaign_themes: list[str] = Field(default_factory=list)
    # Buyer search intents implied by ad WORDING only — never presented or
    # persisted as actual bid keywords (not publicly knowable).
    implied_search_intents: list[str] = Field(default_factory=list)
    # Bounded follow-up queries; the TOOL loops over at most 3 of these.
    next_queries: list[str] = Field(default_factory=list)
    notes: str = ""
