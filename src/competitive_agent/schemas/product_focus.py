"""Product-focus report schema (on-demand, per-run, per-vertical).

A product-vs-product read for ONE product category (vertical) — "Vanta
competes with Rippling's compliance product, so analyze that category" — never
whole-company noise. Every item carries a ``supporting_quote`` (smallest exact
verbatim excerpt from the supplied in-category evidence, containment-verified
by the caller) plus a ``basis`` explaining the grounding. Market share,
revenue, company size, and win rates are never observable in a public corpus,
so the schema carries no field for them.

All fields default so a partially-filled model output still validates
(missing-field tolerant); the caller's guards then mark what is unverified.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import VersionedModel


class FocusSection(VersionedModel):
    """One narrative section, grounded in a verbatim quote where possible."""

    text: str = ""
    # Smallest exact verbatim excerpt from the supplied evidence; None when the
    # section is an inference over counts. Containment-verified by the caller.
    supporting_quote: str | None = None
    basis: str = ""


class MessagingGap(VersionedModel):
    """Something one side says in-category that the other side does not."""

    # Whose message it is (the OTHER side is the one missing it).
    said_by: Literal["competitor", "focal"] = "competitor"
    gap: str = ""
    supporting_quote: str | None = None
    basis: str = ""


class FocusOpportunity(VersionedModel):
    """One concrete in-category product-marketing move."""

    title: str = ""
    angle: str = ""
    # The proof the focal product must actually have (or build) to land it.
    proof_required: str = ""
    funnel_placement: Literal["awareness", "consideration", "decision"] = "consideration"
    first_asset_to_ship: str = ""
    supporting_quote: str | None = None
    basis: str = ""


class ClaimToAvoid(VersionedModel):
    """An in-category claim the focal product cannot support / would backfire."""

    claim: str = ""
    reason: str = ""
    supporting_quote: str | None = None
    basis: str = ""


class ProductFocusReport(VersionedModel):
    # How the competitor frames this category, grounded in observed pages.
    category_narrative: FocusSection = Field(default_factory=FocusSection)
    # Personas / ICP read from the in-category evidence.
    their_target_buyer: FocusSection = Field(default_factory=FocusSection)
    # Positioning angle for the focal PRODUCT (not the company).
    how_focal_should_counter: FocusSection = Field(default_factory=FocusSection)
    messaging_gaps: list[MessagingGap] = Field(default_factory=list)
    detailed_opportunities: list[FocusOpportunity] = Field(default_factory=list)
    what_not_to_claim: list[ClaimToAvoid] = Field(default_factory=list)
