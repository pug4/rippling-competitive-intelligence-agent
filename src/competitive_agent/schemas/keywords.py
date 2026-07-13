"""Keyword-intelligence schemas (KEYWORDS contract + Gemini SERP addendum).

Two record shapes behind the same seam:

- :class:`KeywordMetric` — one row per keyword from a REAL keyword-metrics
  (volume/CPC/competition) provider. Semrush today; Ahrefs / Google Keyword
  Planner drop in later. Values are always provider-reported — a missing
  field stays ``None`` and is never estimated or synthesized (accuracy
  invariant #1).
- :class:`SerpIntel` — one row per keyword of OBSERVED live-SERP intelligence
  from Gemini with Google Search grounding: real People-Also-Ask questions,
  related searches, what formats rank, and which SERP features occupy the
  page. It carries NO volume/CPC/difficulty (Gemini does not return them).
  ``sources`` holds the grounding-chunk URIs and MUST be non-empty for a row
  to be kept — an ungrounded answer is model recall, not a SERP observation,
  and is discarded by the provider before it ever becomes a row.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import VersionedModel


class KeywordMetric(VersionedModel):
    keyword: str
    # Provider-reported monthly search volume; None when the provider did not
    # return one (never estimated).
    volume: int | None = None
    # Provider-reported average CPC in USD; None when not returned.
    cpc_usd: float | None = None
    # Provider-reported competition density (0-1); None when not returned.
    competition: float | None = None
    # Which provider reported these numbers (e.g. "semrush").
    source: str
    retrieved_at: datetime


class SerpIntel(VersionedModel):
    """Observed live-SERP intelligence for one keyword (Gemini + grounding)."""

    keyword: str
    # Real People-Also-Ask questions observed on the results page.
    paa_questions: list[str] = Field(default_factory=list)
    # Related searches observed on the results page.
    related_searches: list[str] = Field(default_factory=list)
    # Content types in the top ~5 organic results (listicle, how-to, ...).
    ranking_formats: list[str] = Field(default_factory=list)
    # SERP features occupying the page (featured_snippet, ai_overview, ...).
    serp_features: list[str] = Field(default_factory=list)
    # One sentence on the dominant intent, as observed.
    intent_note: str = ""
    # Grounding URIs (groundingMetadata.groundingChunks[*].web.uri) — REQUIRED
    # non-empty for the row to be kept; providers discard ungrounded answers.
    sources: list[str] = Field(default_factory=list)
    retrieved_at: datetime
