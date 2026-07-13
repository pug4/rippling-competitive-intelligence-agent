"""Buyer-voice signals mined from third-party review pages (REVIEWS contract).

Output contract for ``prompts/reviews_mining_v1.md`` (task ``mine_reviews``):
per-signal VERBATIM quotes that are containment-verified against the review
artifact text at extraction time (``processing/buyer_voice.py``, same
drop-and-log pattern as the classifier excerpt checks). Reviews are a
selection-biased sample — these records carry buyer LANGUAGE and direction,
never representative sentiment or market statistics.

Every field defaults so validation is missing-field tolerant: fields the
prompt does not emit (``artifact_id``, ``company_id``, ``source_url``,
drop notes) are stamped by the pipeline after validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel

AlternativeDirection = Literal["evaluated", "switched_from", "switched_to", "unclear"]
MessageRealityRelation = Literal["contradicts", "confirms", "unclear"]


class QuotedSignal(VersionedModel):
    """Base shape shared by every buyer-voice signal: one verbatim quote.

    ``quote`` must appear verbatim in the review artifact text; signals whose
    quote fails containment verification are dropped (and logged), never kept.
    """

    quote: str = ""
    confidence: ConfidenceLevel = "low"
    # Basis for the confidence, e.g. "2 independent reviewer quotes on this page".
    reason: str = ""


class BuyerVoiceTheme(QuotedSignal):
    """One objection/pain or praise theme grounded in a verbatim quote."""

    # Normalized lowercase snake_case theme (e.g. "implementation_pain").
    theme: str = ""


class BuyerVoiceAlternative(QuotedSignal):
    """A competing product the review text actually names."""

    alternative: str = ""
    direction: AlternativeDirection = "unclear"


class BuyerVoiceContext(QuotedSignal):
    """Reviewer context ONLY when the text states it — never inferred."""

    reviewer_role: str = "not_observed"
    segment: str = "not_observed"
    industry: str = "not_observed"
    job_to_be_done: str = "not_observed"


class MessageRealitySignal(QuotedSignal):
    """Buyer language that contradicts or confirms a marketing claim theme."""

    claim_theme: str = ""
    relation: MessageRealityRelation = "unclear"


class BuyerVoiceSignals(VersionedModel):
    """Full buyer-voice record for ONE review artifact (family "buyer_voice")."""

    # Stamped by the pipeline from the artifact (never trusted from the model).
    artifact_id: str = ""
    company_id: str = ""
    source_url: str = ""

    objections: list[BuyerVoiceTheme] = Field(default_factory=list)
    praise: list[BuyerVoiceTheme] = Field(default_factory=list)
    alternatives: list[BuyerVoiceAlternative] = Field(default_factory=list)
    buyer_contexts: list[BuyerVoiceContext] = Field(default_factory=list)
    message_reality_signals: list[MessageRealitySignal] = Field(default_factory=list)

    # Drop notes (unverified quotes) + anything the model could not place.
    unclassified_signals: list[str] = Field(default_factory=list)
    classifier_confidence: ConfidenceLevel = "low"
