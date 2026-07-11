from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel

PrimaryMotion = Literal[
    "product_led",
    "self_serve_transactional",
    "sales_led",
    "enterprise_sales_led",
    "partner_led",
    "hybrid",
    "unclear",
]

PricingDisclosure = Literal[
    "fully_public",
    "partially_public",
    "calculator",
    "starting_price_only",
    "sales_gated",
    "hidden",
    "mixed_by_product",
    "unknown",
]


class CommercialMotionProfile(VersionedModel):
    """How a company appears to sell during a period (§9.2)."""

    company_id: str
    period_start: date
    period_end: date

    primary_motion: PrimaryMotion = "unclear"
    secondary_motions: list[str] = Field(default_factory=list)
    pricing_disclosure: PricingDisclosure = "unknown"

    dominant_ctas: dict[str, float] = Field(default_factory=dict)
    free_entry_points: list[str] = Field(default_factory=list)
    sales_entry_points: list[str] = Field(default_factory=list)
    implementation_signals: list[str] = Field(default_factory=list)
    procurement_signals: list[str] = Field(default_factory=list)
    partner_signals: list[str] = Field(default_factory=list)
    sales_hiring_signals: list[str] = Field(default_factory=list)
    segment_emphasis: dict[str, float] = Field(default_factory=dict)
    product_land_and_expand_signals: list[str] = Field(default_factory=list)

    evidence_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
    alternative_interpretations: list[str] = Field(default_factory=list)
