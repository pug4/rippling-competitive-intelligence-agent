"""Independent labeling harness (blueprint anti-contamination rule).

This produces ground-truth-candidate labels with a prompt written FROM SCRATCH
here — it deliberately does not import or reference the production classifier
prompts (``prompts/classifier_*_v1.md``), and it never sees the production
classification for the artifact it is labeling. Its output is a starting point
for HUMAN adjudication, not a substitute for it: machine-vs-machine agreement
measures consistency between two models, not correctness. The eval report labels
these numbers accordingly and the adjudication guide governs the final labels.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Small controlled vocabularies for the categorical fields. Kept intentionally
# independent from the production taxonomy file so the two paths can disagree.
Segment = Literal["smb", "mid_market", "enterprise", "developer", "mixed", "not_observed"]
Stance = Literal[
    "ignores", "implicit_contrast", "named_comparison", "direct_attack", "not_observed"
]
ClaimType = Literal[
    "capability", "outcome", "status", "fear", "identity", "cost", "risk", "category", "not_observed"
]
Salience = Literal["low", "medium", "high"]
Funnel = Literal["awareness", "consideration", "evaluation", "decision", "retention_expansion"]


class ArtifactLabel(BaseModel):
    """One hand-guide-aligned label for a single artifact."""

    primary_message: str = Field(description="The single argument given the most prominence")
    secondary_messages: list[str] = Field(default_factory=list)
    salience_band: Salience = "medium"
    segment: Segment = "not_observed"
    persona: str = Field(default="not_observed", description="Organizational role targeted")
    category_entry_point: str = Field(default="not_observed", description="The buying trigger/situation")
    funnel_stage: Funnel = "awareness"
    claim_type: ClaimType = "not_observed"
    proof_type: str = Field(default="not_observed", description="Strongest proof offered")
    proof_types: list[str] = Field(default_factory=list)
    competitive_stance: Stance = "not_observed"
    villain_exact_wording: str = Field(default="not_observed")
    exact_supporting_excerpt: str = Field(
        default="", description="Verbatim text (copied exactly) backing the primary message"
    )


_LABELING_SYSTEM = """You are an independent marketing analyst building an evaluation \
answer key. You are NOT the system under test. Read ONLY the page text provided and \
label what is literally present. Rules:
- Label the single most prominent argument as primary_message; do not invent themes.
- For every categorical field, choose the closest option; if the page does not \
support it, use not_observed. Never infer a capability from silence.
- exact_supporting_excerpt and villain_exact_wording MUST be copied verbatim from the \
page text (exact substring), or left empty / not_observed.
- salience_band reflects how prominently the primary_message is featured (headline/hero \
= high; body = medium; footnote = low).
Return only the structured label."""


def _band(salience: float | None) -> Salience:
    if salience is None:
        return "medium"
    if salience >= 0.7:
        return "high"
    if salience >= 0.4:
        return "medium"
    return "low"


async def label_artifact(text: str, gateway: Any, *, max_chars: int = 12000) -> ArtifactLabel:
    """Produce an independent label. Raises if no model is available."""
    if gateway is None:
        raise RuntimeError("labeling requires a model gateway (set ANTHROPIC_API_KEY)")
    result = await gateway.generate_structured(
        task_name="eval_labeling",
        system=_LABELING_SYSTEM,
        user_content=f"PAGE TEXT:\n{text[:max_chars]}",
        output_model=ArtifactLabel,
        prompt_name="eval_labeling",
        prompt_version="v1",
    )
    return result.output


def label_to_gold(label: ArtifactLabel) -> dict[str, Any]:
    """Project an independent label into the scorer's field shape."""
    return {
        "primary_message": label.primary_message,
        "secondary_messages": label.secondary_messages,
        "salience_band": label.salience_band,
        "segment": label.segment,
        "persona": label.persona,
        "category_entry_point": label.category_entry_point,
        "funnel_stage": label.funnel_stage,
        "claim_type": label.claim_type,
        "proof_type": label.proof_type,
        "proof_types": label.proof_types,
        "competitive_stance": label.competitive_stance,
        "exact_supporting_excerpt": label.exact_supporting_excerpt or None,
    }


def classification_to_pred(mc: Any) -> dict[str, Any]:
    """Project a production MarketingClassification into the scorer's field shape.

    Production emits list-valued fields (a page can target several segments); the
    eval compares the single most prominent, so we take the first element.
    """

    def first(seq: list[Any]) -> Any:
        return seq[0] if seq else None

    return {
        "primary_message": mc.primary_message or mc.primary_theme,
        "secondary_messages": list(mc.secondary_messages or mc.supporting_themes or []),
        "salience_band": _band(mc.message_salience),
        "segment": first(mc.segments),
        "persona": first(mc.personas),
        "category_entry_point": first(mc.category_entry_points),
        "funnel_stage": first(mc.funnel_stages),
        "claim_type": first(mc.claim_types),
        "proof_type": first(mc.proof_types),
        "proof_types": list(mc.proof_types or []),
        "competitive_stance": mc.competitive_stance,
    }
