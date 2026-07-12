"""Minimal temporal engine: explicit period comparison, not free-form trend
finding (§37.21, §36.4 Loop 4).

Classifications are assigned to windows by their artifact's real dates (a
Wayback capture lands in the comparison window by its actual capture time, a
live fetch in the current window). A candidate change requires BOTH windows to
have a value for the dimension — never a trend from one period (Rule 8). Each
candidate is put to an independent temporal judge before it becomes a
ChangeEvent, and ``possibly_abandoned`` is gated deterministically on sustained
prior presence.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from pydantic import BaseModel, Field

from ..schemas.change import ChangeEvent
from ..schemas.common import ConfidenceLevel, Lifecycle, new_id, utcnow

logger = logging.getLogger(__name__)

JUDGE_TASK_NAME = "judge_temporal"
JUDGE_PROMPT_NAME = "temporal_judge"

JUDGE_SYSTEM = (
    "You are the temporal verification judge. Decide only from the supplied "
    "prior- and current-period evidence; use no outside knowledge. Never equate "
    "disappearance with failure. Respond only via the structured tool."
)

# Scalar dimensions compared as prior-vs-current single values. Message change
# is compared on the NORMALIZED theme (free-form messages are unique per page).
_SCALAR_DIMENSIONS = ("primary_theme", "pricing_disclosure_level", "competitive_stance")

_VALID_LIFECYCLES = {
    "emerging",
    "expanding",
    "stable",
    "declining",
    "not_recently_observed",
    "possibly_abandoned",
    "reintroduced",
    "repositioned",
}


class TemporalJudgeVerdict(BaseModel):
    verdict: str  # verified | rejected | downgraded_to_hypothesis
    lifecycle: str = "repositioned"
    alternative_explanations: list[str] = Field(default_factory=list)
    confidence: str = "low"
    coverage: str = "low"
    reason: str = ""


def _window_of(artifact: Any, windows: list) -> str | None:
    """Assign an artifact to a window by its real dates."""
    date = artifact.archive_capture_at or artifact.published_at or artifact.retrieved_at
    if date is None:
        return None
    current = next((w for w in windows if w.purpose == "current"), None)
    comparison = next((w for w in windows if w.purpose == "comparison"), None)
    if current and date >= current.start_at:
        return "current"
    if comparison and date >= comparison.start_at:
        return "comparison"
    return "comparison" if comparison and date < comparison.end_at else None


def detect_candidate_changes(
    by_window: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """Prior-vs-current scalar differences with BOTH periods populated."""
    prior = by_window.get("comparison") or []
    current = by_window.get("current") or []
    if not prior or not current:
        return []

    def dominant(items: list[Any], attr: str) -> tuple[str | None, list[str]]:
        counts: dict[str, int] = {}
        artifacts: dict[str, list[str]] = {}
        for c in items:
            value = getattr(c, attr, None)
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
            artifacts.setdefault(value, []).append(c.artifact_id)
        if not counts:
            return None, []
        top = max(counts, key=lambda k: counts[k])
        return top, artifacts[top]

    candidates = []
    for dimension in _SCALAR_DIMENSIONS:
        prior_val, prior_arts = dominant(prior, dimension)
        cur_val, cur_arts = dominant(current, dimension)
        if prior_val is None or cur_val is None:
            continue
        if str(prior_val).strip().lower() == str(cur_val).strip().lower():
            continue
        candidates.append(
            {
                "dimension": dimension,
                "prior_state": prior_val,
                "current_state": cur_val,
                "prior_artifact_ids": prior_arts,
                "current_artifact_ids": cur_arts,
                "prior_count": len(prior_arts),
            }
        )

    # Theme EMERGENCE / retreat: a theme present in one window and absent in the
    # other is a candidate change even when the DOMINANT theme is unchanged
    # (reviewer R4 — the AI/automation thread emerging in the current window was
    # being washed out by the modal-only comparison). These are inherently
    # lower-confidence and the judge weights them by sample depth.
    def theme_set(items: list[Any]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for c in items:
            for t in [
                getattr(c, "primary_theme", None),
                *(getattr(c, "supporting_themes", []) or []),
            ]:
                if t:
                    out.setdefault(t, []).append(c.artifact_id)
        return out

    prior_themes = theme_set(prior)
    current_themes = theme_set(current)
    for theme, arts in current_themes.items():
        if theme not in prior_themes and len(arts) >= 2:
            candidates.append(
                {
                    "dimension": "theme_emergence",
                    "prior_state": f"“{theme}” not observed in the prior-window sample",
                    "current_state": f"“{theme}” present in {len(arts)} current-window artifacts",
                    "prior_artifact_ids": [c.artifact_id for c in prior][:5],
                    "current_artifact_ids": arts,
                    "prior_count": len(prior),
                    "emergence": True,
                }
            )
    return candidates


async def build_change_events(run_id: str, state: Any, ctx: Any) -> list[ChangeEvent]:
    repository = ctx.repository
    gateway = ctx.gateway
    windows = state.time_windows

    classifications = [
        c
        for c in repository.list_classifications(run_id, family="merged")
        if c.__class__.__name__ == "MarketingClassification"
    ]
    if len(classifications) < 2:
        return []

    artifacts = {a.artifact_id: a for a in repository.list_artifacts(run_id=run_id)}
    by_window: dict[str, list[Any]] = {"current": [], "comparison": []}
    for c in classifications:
        artifact = artifacts.get(c.artifact_id)
        if artifact is None:
            continue
        window = _window_of(artifact, windows)
        if window in by_window:
            by_window[window].append(c)

    candidates = detect_candidate_changes(by_window)
    if not candidates:
        if ctx.trace:
            ctx.trace.append(
                "temporal_change_rejected",
                {"reason": "no candidate with both prior and current evidence"},
            )
        return []

    from ..prompt_registry import PromptRegistry

    prompts = ctx.scratch.get("_prompt_registry") or PromptRegistry()

    coverage_context = (
        f"windows: current + comparison; artifacts observed: current="
        f"{len(by_window['current'])}, comparison={len(by_window['comparison'])}; "
        f"historical source coverage: {state.coverage.get('historical_website', 'low')}"
    )

    events: list[ChangeEvent] = []
    for candidate in candidates:
        if ctx.trace:
            ctx.trace.append(
                "temporal_change_candidate",
                {
                    "dimension": candidate["dimension"],
                    "prior": candidate["prior_state"],
                    "current": candidate["current_state"],
                },
            )
        prompt = prompts.get(JUDGE_PROMPT_NAME)
        rendered = prompt.render(
            candidate_change=f"{candidate['dimension']}: '{candidate['prior_state']}' -> '{candidate['current_state']}'",
            prior_evidence_block="\n".join(f"- {aid}" for aid in candidate["prior_artifact_ids"]),
            current_evidence_block="\n".join(
                f"- {aid}" for aid in candidate["current_artifact_ids"]
            ),
            coverage_context=coverage_context,
        )
        try:
            result = await gateway.generate_structured(
                JUDGE_TASK_NAME,
                system=JUDGE_SYSTEM,
                user_content=rendered,
                output_model=TemporalJudgeVerdict,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
            )
            verdict: TemporalJudgeVerdict = result.output
        except Exception as exc:  # noqa: BLE001
            logger.warning("temporal judge failed for %s: %s", candidate["dimension"], exc)
            if ctx.trace:
                ctx.trace.append(
                    "temporal_change_rejected",
                    {"dimension": candidate["dimension"], "error": str(exc)[:200]},
                )
            continue

        v = (verdict.verdict or "").strip().lower()
        if v == "rejected":
            if ctx.trace:
                ctx.trace.append(
                    "temporal_change_rejected",
                    {"dimension": candidate["dimension"], "reason": verdict.reason[:200]},
                )
            continue

        lifecycle = verdict.lifecycle if verdict.lifecycle in _VALID_LIFECYCLES else "repositioned"
        # Deterministic guard: possibly_abandoned needs sustained prior presence.
        if lifecycle == "possibly_abandoned" and candidate["prior_count"] < 2:
            lifecycle = "declining"
            verdict.alternative_explanations.append(
                "downgraded from possibly_abandoned: prior presence not sustained across >=2 artifacts"
            )
        confidence = (
            "low"
            if v == "downgraded_to_hypothesis"
            else (verdict.confidence if verdict.confidence in ("high", "medium", "low") else "low")
        )
        # Emergence is inherently confounded by collection asymmetry (reviewer
        # R4 watch-point): a theme "appearing now" can just mean we collected
        # current news but no comparable prior surface. Force emergence to
        # lifecycle=emerging, cap confidence at low, and ALWAYS carry the
        # coverage-asymmetry alternative explanation.
        if candidate.get("emergence"):
            lifecycle = "emerging"
            confidence = "low"
            asym = (
                "this may be a collection/archive coverage asymmetry (current-window news collected "
                "without a comparable prior-window surface) rather than a real messaging change"
            )
            if asym not in verdict.alternative_explanations:
                verdict.alternative_explanations.insert(0, asym)

        coverage = verdict.coverage if verdict.coverage in ("high", "medium", "low") else "low"
        events.append(
            ChangeEvent(
                change_id=new_id("CHG"),
                company_id=state.company.company_id if state.company else "unresolved",
                dimension=candidate["dimension"],
                prior_state=str(candidate["prior_state"]),
                current_state=str(candidate["current_state"]),
                lifecycle=cast(Lifecycle, lifecycle),
                prior_evidence_ids=candidate["prior_artifact_ids"],
                current_evidence_ids=candidate["current_artifact_ids"],
                first_detected_at=utcnow(),
                apparent_change_at=None,
                confidence=cast(ConfidenceLevel, confidence),
                coverage=cast(ConfidenceLevel, coverage),
                alternative_explanations=verdict.alternative_explanations
                or ["archive coverage gaps could partially explain the observed difference"],
            )
        )
    return events
