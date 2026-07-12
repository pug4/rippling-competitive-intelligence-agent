"""Focal-company mirror and matched comparison (§20, §37.25, Rule 7).

The focal company (default Rippling) runs through the SAME graph as the
competitor, in an isolated run (own run_id, state, trace, artifacts). No
opportunity is finalized until its dimension has been checked against the
focal package. Phase 1 ships message–proof gaps + already-saying-it status;
Phase 5 widens to matrices, vulnerabilities, and the full critic chain.
"""

from __future__ import annotations

from typing import Any, cast

from .schemas.classification import MarketingClassification
from .schemas.common import new_id, utcnow
from .schemas.opportunity import AttackabilityAssessment, MessageProofGap, ProofStrength
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
    remaining = max(
        120.0, state.max_runtime_seconds - (utcnow() - state.started_at).total_seconds()
    )
    focal_state.max_runtime_seconds = int(min(focal_state.max_runtime_seconds, remaining * 0.55))
    focal_state.max_iterations = min(focal_state.max_iterations, 22)
    focal_state.max_tool_calls = min(focal_state.max_tool_calls, 70)
    if ctx.trace:
        ctx.trace.append(
            "company_pipeline_created",
            {"purpose": "focal_mirror", "focal_run_id": focal_state.run_id},
        )
    focal_state = await drive(focal_state, focal_ctx)
    ctx.scratch[reuse_key] = focal_state.run_id
    if ctx.trace:
        ctx.trace.append(
            "focal_mirror_completed",
            {"focal_run_id": focal_state.run_id, "stop_reason": focal_state.stop_reason},
        )
    return focal_state.run_id


def _classifications(repository: Any, run_id: str) -> list[MarketingClassification]:
    out = []
    for model in repository.list_classifications(run_id=run_id, family="merged"):
        if isinstance(model, MarketingClassification):
            out.append(model)
    return out


def _artifact_meta(repository: Any, run_id: str) -> dict[str, tuple[str, float]]:
    """artifact_id -> (source_type, authority) for representative-message and
    news-only checks (reviewer R3/R6)."""
    from .synthesis import artifact_authority

    out: dict[str, tuple[str, float]] = {}
    try:
        for a in repository.list_artifacts(run_id=run_id):
            out[a.artifact_id] = (a.source_type, artifact_authority(a))
    except Exception:
        pass
    return out


def _proof_strength(proof_types: set[str]) -> ProofStrength:
    if proof_types & _STRONG:
        return "strong"
    if proof_types & _MODERATE:
        return "moderate"
    if proof_types:
        return "weak"
    return "none"


def _message_index(
    classifications: list[MarketingClassification],
    artifact_meta: dict[str, tuple[str, float]] | None = None,
) -> dict[str, dict[str, Any]]:
    """theme -> aggregate for the theme. The representative ``message`` comes
    from the page with the highest AUTHORITY × salience (R6) — not the single
    most-salient page — so a stablecoin blog post can't represent
    ``data_unification``. ``source_types`` supports the news-only attack guard.
    """
    meta = artifact_meta or {}
    index: dict[str, dict[str, Any]] = {}
    for c in classifications:
        theme = (c.primary_theme or c.primary_message or "").strip().lower()
        if not theme:
            continue
        label = c.primary_message or c.primary_theme or theme
        source_type, authority = meta.get(c.artifact_id, ("unknown", 0.4))
        slot = index.setdefault(
            theme,
            {
                "message": label,
                "best_label_score": -1.0,
                "artifact_ids": set(),
                "proof_types": set(),
                "per_page_strength": [],
                "source_types": set(),
                "primary_count": 0,
                "count": 0,
                "classification_ids": [],
            },
        )
        sal = c.message_salience if c.message_salience is not None else 0.4
        label_score = authority * (0.4 + sal)
        if label_score > slot["best_label_score"]:
            slot["best_label_score"] = label_score
            slot["message"] = label
        slot["artifact_ids"].add(c.artifact_id)
        slot["source_types"].add(source_type)
        slot["proof_types"] |= set(c.proof_types)
        slot["per_page_strength"].append(_proof_strength(set(c.proof_types)))
        slot.setdefault("per_page_proof", []).append(list(c.proof_types))
        slot.setdefault("specificities", []).append(c.claim_specificity)
        slot["count"] += 1
        slot["primary_count"] += 1
        slot["classification_ids"].append(c.classification_id)
    return index


def _theme_strength(slot: dict[str, Any]) -> ProofStrength:
    """A theme's proof strength = the modal per-page strength (a single strongly
    proven page does not make a repeated theme uniformly strong)."""
    from collections import Counter

    strengths = slot.get("per_page_strength") or []
    if not strengths:
        return _proof_strength(slot["proof_types"])
    # Most common; ties break toward the weaker rating (more honest / cautious).
    order = {"strong": 3, "moderate": 2, "weak": 1, "none": 0}
    counts = Counter(strengths)
    top = max(counts.values())
    return min((s for s, n in counts.items() if n == top), key=lambda s: order[s])  # type: ignore[return-value]


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


MAX_GAPS = 6


def build_message_proof_gaps(
    competitor_run_id: str,
    focal_run_id: str | None,
    repository: Any,
    competitor_name: str,
    focal_name: str,
) -> list[MessageProofGap]:
    """Join each repeated competitor theme to its observed proof and the focal
    company's proof on the same theme, producing a comparison (§19, §22.2).

    A gap is NOT only "competitor proof is weak". Per §19, the opportunity comes
    from the comparison: a weakly-proven repeated claim is directly attackable;
    a strongly-proven dominant theme is still an opportunity to out-prove or
    reframe where the focal company has a structural angle. So every repeated
    theme is surfaced, ranked by attackability then prominence, and the critic /
    opportunity stage decides what to do with it.
    """
    from .synthesis import proof_distribution

    comp = _message_index(
        _classifications(repository, competitor_run_id),
        _artifact_meta(repository, competitor_run_id),
    )
    focal = (
        _message_index(
            _classifications(repository, focal_run_id), _artifact_meta(repository, focal_run_id)
        )
        if focal_run_id
        else {}
    )

    gaps: list[MessageProofGap] = []
    for theme, slot in comp.items():
        if slot["count"] < 2:  # a gap needs a REPEATED claim (§19.1)
            continue
        strength = _theme_strength(slot)
        focal_slot = focal.get(theme)
        focal_strength = _theme_strength(focal_slot) if focal_slot else "none"
        missing = [p for p in _PROOF_STRENGTH_ORDER[:4] if p not in slot["proof_types"]]
        dist = proof_distribution(slot.get("per_page_proof", []))
        specificity = _modal(slot.get("specificities", []))
        n_pages = slot["count"]
        # Whether ANY page carries strong proof — a 2-page theme where one page
        # is strong must not be declared "attackable/weak" off a modal tie (R3).
        has_strong_page = any(s == "strong" for s in slot.get("per_page_strength", []))
        source_types = set(slot.get("source_types", []))
        news_only = bool(source_types) and source_types.issubset({"news", "exa_web", "comparison"})

        # Attackability rubric (feedback #17): distinct dimensions, not one label.
        overall, attack_level, interpretation = _stance(
            competitor_name,
            focal_name,
            theme,
            strength,
            focal_strength,
            n_pages=n_pages,
            has_strong_page=has_strong_page,
            news_only=news_only,
        )
        detail = AttackabilityAssessment(
            proof_gap="high" if strength in ("weak", "none") else "low",
            focal_proof="high" if focal_strength in ("strong", "moderate") else "low",
            product_comparability="partial",  # refined by the opportunity-stage gate
            structural_defensibility="medium",
            counterattack_risk="high" if strength == "strong" else "medium",
            overall=cast(Any, overall),
            rationale=f"competitor proof={strength}; {focal_name} proof={focal_strength}",
        )

        gaps.append(
            MessageProofGap(
                claim_id=new_id("GAP"),
                claim_text=slot["message"],
                short_label=theme.replace("_", " "),
                claim_type="category"
                if theme in ("consolidation", "native_platform_breadth", "data_unification")
                else "capability",
                claim_specificity=specificity,
                repetition=f"“{theme}” theme observed in {len(slot['artifact_ids'])} collected artifacts",
                lifecycle="stable",
                proof_types_observed=sorted(slot["proof_types"]),
                proof_distribution=dist,
                strongest_proof_id=None,
                proof_strength=strength,
                missing_proof=missing,
                focal_equivalent_claim=(focal_slot["message"] if focal_slot else None),
                focal_proof_ids=(focal_slot["classification_ids"][:5] if focal_slot else []),
                focal_proof_strength=focal_strength,
                actionable_interpretation=interpretation,
                attackability=attack_level,  # type: ignore[arg-type]
                attackability_detail=detail,
                why_attack_might_backfire=(
                    f"If {competitor_name} holds unpublished proof, a comparative claim invites a "
                    "documented counterattack; comparative copy also requires legal substantiation."
                ),
            )
        )
    order = {"high": 0, "medium": 1, "low": 2}
    count_by_message = {slot["message"]: slot["count"] for slot in comp.values()}
    gaps.sort(key=lambda g: (order.get(g.attackability, 3), -count_by_message.get(g.claim_text, 0)))
    return gaps[:MAX_GAPS]


def _modal(values: list[str]) -> str:
    from collections import Counter

    vals = [v for v in values if v and v != "unknown"]
    if not vals:
        return "unknown"
    return Counter(vals).most_common(1)[0][0]


def _stance(
    competitor: str,
    focal: str,
    theme: str,
    strength: str,
    focal_strength: str,
    n_pages: int = 2,
    has_strong_page: bool = False,
    news_only: bool = False,
) -> tuple[str, str, str]:
    """(overall_stance, attackability_level, interpretation) — states what the
    evidence SHOWS, never converting a proof gap into a capability claim (#4).

    R3: an "attack" verdict must not rest on a thin (< 3-page) theme, on a modal
    tie where one page is actually strong, or on news/blog coverage only (which
    isn't the competitor's own marketing surface)."""
    thin = n_pages < 3 or has_strong_page or news_only
    if strength in ("weak", "none") and focal_strength in ("strong", "moderate") and not thin:
        return (
            "attack",
            "high",
            f"{competitor} repeats the “{theme}” message but the observed public proof is "
            f"{strength}; {focal} shows {focal_strength} proof on the same theme. This is a direct "
            "out-prove opening (the evidence shows a proof gap, not that the capability is absent).",
        )
    if strength in ("weak", "none") and focal_strength in ("strong", "moderate") and thin:
        why = (
            "only " + str(n_pages) + " page(s)"
            if n_pages < 3
            else "one page already carries strong proof"
            if has_strong_page
            else "grounded only in third-party news/blog coverage, not the competitor's own pages"
        )
        return (
            "investigate",
            "medium",
            f"“{theme}” LOOKS attackable ({competitor} {strength} vs {focal} {focal_strength}), but "
            f"the read is thin ({why}). Fetch {competitor}'s own product page for this theme before "
            "committing to a comparative attack.",
        )
    if strength in ("weak", "none"):
        return (
            "investigate",
            "medium",
            f"{competitor} repeats “{theme}” with {strength} observed proof and {focal}'s proof is "
            f"{focal_strength}. Treat as a proof-building play — the public evidence does not yet "
            "establish who can prove it better.",
        )
    return (
        "reframe",
        "low",
        f"{competitor} proves “{theme}” strongly and {focal}'s observed proof is {focal_strength}. "
        "Don't attack the claim head-on — reframe around a structural advantage, or concede this "
        "ground. (Strong competitor proof here is what the evidence shows; it is not a claim about "
        f"{focal}'s underlying capability.)",
    )


def utc_stamp() -> str:
    return utcnow().isoformat()
