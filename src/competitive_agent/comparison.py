"""Focal-company mirror and matched comparison (§20, §37.25, Rule 7).

The focal company (default Rippling) runs through the SAME graph as the
competitor, in an isolated run (own run_id, state, trace, artifacts). No
opportunity is finalized until its dimension has been checked against the
focal package. Phase 1 ships message–proof gaps + already-saying-it status;
Phase 5 widens to matrices, vulnerabilities, and the full critic chain.
"""

from __future__ import annotations

from typing import Any

from .schemas.classification import MarketingClassification
from .schemas.common import new_id, utcnow
from .schemas.opportunity import MessageProofGap, ProofStrength
from .state import DirectorState

# Proof types ordered strongest-first (§19.2).
_PROOF_STRENGTH_ORDER = [
    "quantified_customer_outcome",
    "independent_validation",
    "product_demonstration",
    "named_customer_story",
    "certification_or_compliance_record",
    "customer_quotation",
    "customer_logo",
    "feature_assertion",
    "unsupported_assertion",
]

_STRONG = set(_PROOF_STRENGTH_ORDER[:5])
_MODERATE = set(_PROOF_STRENGTH_ORDER[5:7])


async def run_focal_mirror(state: DirectorState, ctx: Any) -> str | None:
    """Run the isolated focal-company pipeline; returns its run_id.

    Reuse rule (§38.25): an existing completed focal run is reused only when
    execution mode, lookback, and taxonomy version match; otherwise a fresh
    isolated run is created.
    """
    if state.focal_company is None:
        return None

    from .runner import create_run, drive  # lazy: avoid circular import

    reuse_key = (
        f"focal:{state.focal_company.primary_domain}:{state.execution_mode}:{state.lookback_days}"
    )
    cached_run_id = ctx.scratch.get(reuse_key)
    if cached_run_id:
        return cached_run_id

    focal_state, focal_ctx = create_run(
        state.focal_company.primary_domain,
        mode="snapshot",
        execution_mode=state.execution_mode,
        compare_to=None,  # the mirror never mirrors itself
        lookback_days=state.lookback_days,
    )
    # The focal mirror is a bounded baseline, not the star of the run: cap its
    # budget to a fraction of the parent so it can never starve the competitor
    # analysis (§38.27 bounded concurrency, §39.7 bounded runs).
    remaining = max(60.0, state.max_runtime_seconds - (utcnow() - state.started_at).total_seconds())
    focal_state.max_runtime_seconds = int(min(focal_state.max_runtime_seconds, remaining * 0.5))
    focal_state.max_iterations = min(focal_state.max_iterations, 12)
    focal_state.max_tool_calls = min(focal_state.max_tool_calls, 40)
    if ctx.trace:
        ctx.trace.append(
            "company_pipeline_created",
            {"purpose": "focal_mirror", "focal_run_id": focal_state.run_id},
        )
    focal_state = await drive(focal_state, focal_ctx)
    ctx.scratch[reuse_key] = focal_state.run_id
    if ctx.trace:
        ctx.trace.append(
            "rippling_mirror_completed",
            {"focal_run_id": focal_state.run_id, "stop_reason": focal_state.stop_reason},
        )
    return focal_state.run_id


def _classifications(repository: Any, run_id: str) -> list[MarketingClassification]:
    out = []
    for model in repository.list_classifications(run_id=run_id, family="merged"):
        if isinstance(model, MarketingClassification):
            out.append(model)
    return out


def _proof_strength(proof_types: set[str]) -> ProofStrength:
    if proof_types & _STRONG:
        return "strong"
    if proof_types & _MODERATE:
        return "moderate"
    if proof_types:
        return "weak"
    return "none"


def _message_index(classifications: list[MarketingClassification]) -> dict[str, dict[str, Any]]:
    """theme -> {message, artifact_ids, proof_types, count, classification_ids}.

    Keyed on the NORMALIZED theme so a theme recurs across pages (free-form
    primary messages are unique per page and would never form a repeated
    claim). The human-readable ``message`` keeps the most salient descriptive
    sentence seen for the theme.
    """
    index: dict[str, dict[str, Any]] = {}
    for c in classifications:
        theme = (c.primary_theme or c.primary_message or "").strip().lower()
        if not theme:
            continue
        label = c.primary_message or c.primary_theme or theme
        slot = index.setdefault(
            theme,
            {
                "message": label,
                "best_salience": -1.0,
                "artifact_ids": set(),
                "proof_types": set(),
                "primary_count": 0,
                "count": 0,
                "classification_ids": [],
            },
        )
        # Keep the descriptive message from the most salient artifact.
        sal = c.message_salience if c.message_salience is not None else 0.0
        if sal > slot["best_salience"]:
            slot["best_salience"] = sal
            slot["message"] = label
        slot["artifact_ids"].add(c.artifact_id)
        slot["proof_types"] |= set(c.proof_types)
        slot["count"] += 1
        slot["primary_count"] += 1
        slot["classification_ids"].append(c.classification_id)
    return index


def already_saying_it(message: str, focal_index: dict[str, dict[str, Any]]) -> str:
    """Minimal §20.4 filter (Phase 1: corpus-presence based)."""
    key = message.strip().lower()
    hit = focal_index.get(key)
    if hit is None:
        # token-overlap soft match to catch near-identical phrasings
        tokens = set(key.split())
        for fkey, slot in focal_index.items():
            if tokens and len(tokens & set(fkey.split())) / len(tokens) >= 0.7:
                hit = slot
                break
    if hit is None:
        return "net_new"
    if hit["primary_count"] >= 2:
        return "already_saturated"
    return "underweighted"


def build_message_proof_gaps(
    competitor_run_id: str,
    focal_run_id: str | None,
    repository: Any,
    competitor_name: str,
    focal_name: str,
) -> list[MessageProofGap]:
    """Repeated competitor claims joined to observed proof, matched against
    the focal company's proof inventory (§19, §22.2)."""
    comp = _message_index(_classifications(repository, competitor_run_id))
    focal = _message_index(_classifications(repository, focal_run_id)) if focal_run_id else {}

    gaps: list[MessageProofGap] = []
    for key, slot in comp.items():
        if slot["count"] < 2:  # a gap needs a REPEATED claim (§19.1)
            continue
        strength = _proof_strength(slot["proof_types"])
        if strength in ("strong",):
            continue  # no gap when proof is strong
        focal_slot = focal.get(key)
        focal_strength = _proof_strength(focal_slot["proof_types"]) if focal_slot else "none"
        missing = [p for p in _PROOF_STRENGTH_ORDER[:4] if p not in slot["proof_types"]]
        gaps.append(
            MessageProofGap(
                claim_id=new_id("GAP"),
                claim_text=slot["message"],
                claim_type="category" if "platform" in key else "capability",
                repetition=f"observed in {len(slot['artifact_ids'])} of the collected artifacts",
                lifecycle="stable",
                proof_types_observed=sorted(slot["proof_types"]),
                strongest_proof_id=None,
                proof_strength=strength,
                missing_proof=missing,
                rippling_equivalent_claim=(focal_slot["message"] if focal_slot else None),
                rippling_proof_ids=(focal_slot["classification_ids"][:5] if focal_slot else []),
                actionable_interpretation=(
                    f"{competitor_name} repeats “{slot['message']}” with {strength} public proof; "
                    f"{focal_name}'s observed proof on the equivalent message is {focal_strength}. "
                    + (
                        "Opportunity: out-prove rather than out-claim."
                        if focal_strength in ("strong", "moderate")
                        else f"{focal_name} proof was not observed either — treat as a proof-building "
                        "hypothesis, not a ready-to-launch attack."
                    )
                ),
                attackability=(
                    "high"
                    if strength in ("weak", "none") and focal_strength in ("strong", "moderate")
                    else "medium"
                    if strength in ("weak", "none")
                    else "low"
                ),
                why_attack_might_backfire=(
                    f"If {competitor_name} holds unpublished proof, a comparative claim invites a "
                    "documented counterattack; comparative copy also requires legal substantiation."
                ),
            )
        )
    order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: order.get(g.attackability, 3))
    return gaps


def utc_stamp() -> str:
    return utcnow().isoformat()
