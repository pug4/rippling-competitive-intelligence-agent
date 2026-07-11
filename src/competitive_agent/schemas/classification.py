"""Staged classification families and the merged full-width record.

Per blueprint §40.2, annotation width is never reduced for implementation
convenience: classification is staged into four narrower structured calls
(message, audience, product, competitive) while retaining the complete
logical schema. A family may fail independently and retry without
discarding valid results from the others. ``MarketingClassification`` is
the merged full-width record (§37.9) composed via ``from_families``.
"""

from __future__ import annotations

from pydantic import Field

from .common import ConfidenceLevel, VersionedModel, new_id

_CONFIDENCE_RANK: dict[str, int] = {"high": 2, "medium": 1, "low": 0}


def _min_confidence(*levels: ConfidenceLevel) -> ConfidenceLevel:
    return min(levels, key=lambda level: _CONFIDENCE_RANK[level])


class MessageSalienceEvidence(VersionedModel):
    """Raw salience observations (§37.19).

    The application converts these into a salience score; the classifier
    must not invent pixel-level visual weights when no screenshot is
    available. Taxonomy-style string fields use ``unknown`` /
    ``not_observed`` rather than omission.
    """

    headline_prominence: str = "unknown"  # e.g. headline | subhead | body_only | absent | unknown
    repetition_count: int = 0
    cta_proximity: str = (
        "unknown"  # e.g. adjacent | same_section | distant | none_observed | unknown
    )
    structural_prominence: str = (
        "not_observed"  # e.g. hero | above_fold | body | footer | not_observed
    )


class MessageFamily(VersionedModel):
    """Family 1: message and argument structure (§40.2)."""

    artifact_id: str
    company_id: str
    primary_message: str | None = None
    secondary_messages: list[str] = Field(default_factory=list)
    salience_evidence: MessageSalienceEvidence = Field(default_factory=MessageSalienceEvidence)
    # Computed by the application from salience_evidence (§37.19); never
    # invented by the model.
    message_salience: float | None = None
    claim_types: list[str] = Field(default_factory=list)
    rhetorical_moves: list[str] = Field(default_factory=list)
    promised_transformation_from: str | None = None
    promised_transformation_to: str | None = None
    supporting_excerpts: list[str] = Field(default_factory=list)
    unclassified_signals: list[str] = Field(default_factory=list)
    classifier_confidence: ConfidenceLevel = "low"


class AudienceFamily(VersionedModel):
    """Family 2: audience, buyer problem, funnel, and category entry point (§40.2)."""

    artifact_id: str
    company_id: str
    segments: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    buyer_jobs: list[str] = Field(default_factory=list)
    pains: list[str] = Field(default_factory=list)
    category_entry_points: list[str] = Field(default_factory=list)
    funnel_stages: list[str] = Field(default_factory=list)
    supporting_excerpts: list[str] = Field(default_factory=list)
    unclassified_signals: list[str] = Field(default_factory=list)
    classifier_confidence: ConfidenceLevel = "low"


class ProductFamily(VersionedModel):
    """Family 3: product, portfolio, pricing, packaging, and launch (§40.2)."""

    artifact_id: str
    company_id: str
    products: list[str] = Field(default_factory=list)
    pricing_disclosure_level: str = "unknown"
    packaging_signals: list[str] = Field(default_factory=list)
    commercial_motion_signals: list[str] = Field(default_factory=list)
    cta: str | None = None
    secondary_ctas: list[str] = Field(default_factory=list)
    launch_type: str = "not_observed"  # e.g. new_product | feature | beta | ga | not_observed
    availability_status: str = "unknown"  # e.g. beta | ga | waitlist | deprecated | unknown
    launch_signals: list[str] = Field(default_factory=list)
    supporting_excerpts: list[str] = Field(default_factory=list)
    unclassified_signals: list[str] = Field(default_factory=list)
    classifier_confidence: ConfidenceLevel = "low"


class ProofObservation(VersionedModel):
    """One observed proof type with its exact supporting excerpt."""

    proof_type: str
    exact_excerpt: str


class CompetitiveFamily(VersionedModel):
    """Family 4: competitive stance, villain, proof, and source status (§40.2)."""

    artifact_id: str
    company_id: str
    villain_exact_wording: list[str] = Field(default_factory=list)
    villain_normalized: list[str] = Field(default_factory=list)
    named_competitors: list[str] = Field(default_factory=list)
    implied_competitor_classes: list[str] = Field(default_factory=list)
    competitive_stance: str | None = None
    proof_observations: list[ProofObservation] = Field(default_factory=list)
    alternative_target_interpretations: list[str] = Field(default_factory=list)
    unclassified_signals: list[str] = Field(default_factory=list)
    classifier_confidence: ConfidenceLevel = "low"


class MarketingClassification(VersionedModel):
    """Merged full-width classification record (§37.9, lines 4169–4206)."""

    classification_id: str
    artifact_id: str
    company_id: str

    primary_message: str | None = None
    secondary_messages: list[str] = Field(default_factory=list)
    message_salience: float | None = None

    products: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    buyer_jobs: list[str] = Field(default_factory=list)
    pains: list[str] = Field(default_factory=list)
    category_entry_points: list[str] = Field(default_factory=list)
    funnel_stages: list[str] = Field(default_factory=list)

    claim_types: list[str] = Field(default_factory=list)
    proof_types: list[str] = Field(default_factory=list)
    rhetorical_moves: list[str] = Field(default_factory=list)

    villain_exact_wording: list[str] = Field(default_factory=list)
    villain_normalized: list[str] = Field(default_factory=list)
    named_competitors: list[str] = Field(default_factory=list)
    implied_competitor_classes: list[str] = Field(default_factory=list)
    competitive_stance: str | None = None
    promised_transformation_from: str | None = None
    promised_transformation_to: str | None = None

    cta: str | None = None
    pricing_disclosure_level: str | None = None
    commercial_motion_signals: list[str] = Field(default_factory=list)

    unclassified_signals: list[str] = Field(default_factory=list)
    classifier_confidence: ConfidenceLevel = "low"

    @classmethod
    def from_families(
        cls,
        message: MessageFamily,
        audience: AudienceFamily,
        product: ProductFamily,
        competitive: CompetitiveFamily,
        classification_id: str | None = None,
    ) -> MarketingClassification:
        """Compose the full-width record from the four staged families.

        All families must describe the same artifact and company. The merged
        confidence is the weakest family confidence; unclassified signals are
        concatenated so nothing is silently dropped.
        """
        families = (message, audience, product, competitive)
        artifact_ids = {f.artifact_id for f in families}
        company_ids = {f.company_id for f in families}
        if len(artifact_ids) != 1 or len(company_ids) != 1:
            raise ValueError(
                "from_families requires all families to share one artifact_id "
                f"and company_id; got artifact_ids={sorted(artifact_ids)}, "
                f"company_ids={sorted(company_ids)}"
            )

        proof_types: list[str] = []
        for observation in competitive.proof_observations:
            if observation.proof_type not in proof_types:
                proof_types.append(observation.proof_type)

        unclassified: list[str] = []
        for family in families:
            unclassified.extend(family.unclassified_signals)

        return cls(
            classification_id=classification_id or new_id("cls"),
            artifact_id=message.artifact_id,
            company_id=message.company_id,
            primary_message=message.primary_message,
            secondary_messages=list(message.secondary_messages),
            message_salience=message.message_salience,
            products=list(product.products),
            segments=list(audience.segments),
            personas=list(audience.personas),
            buyer_jobs=list(audience.buyer_jobs),
            pains=list(audience.pains),
            category_entry_points=list(audience.category_entry_points),
            funnel_stages=list(audience.funnel_stages),
            claim_types=list(message.claim_types),
            proof_types=proof_types,
            rhetorical_moves=list(message.rhetorical_moves),
            villain_exact_wording=list(competitive.villain_exact_wording),
            villain_normalized=list(competitive.villain_normalized),
            named_competitors=list(competitive.named_competitors),
            implied_competitor_classes=list(competitive.implied_competitor_classes),
            competitive_stance=competitive.competitive_stance,
            promised_transformation_from=message.promised_transformation_from,
            promised_transformation_to=message.promised_transformation_to,
            cta=product.cta,
            pricing_disclosure_level=product.pricing_disclosure_level,
            commercial_motion_signals=list(product.commercial_motion_signals),
            unclassified_signals=unclassified,
            classifier_confidence=_min_confidence(
                message.classifier_confidence,
                audience.classifier_confidence,
                product.classifier_confidence,
                competitive.classifier_confidence,
            ),
        )
