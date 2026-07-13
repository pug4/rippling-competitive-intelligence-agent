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
import re
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
    """Assign an artifact to a window — delegates to the ONE shared predicate
    (synthesis.assign_window) so change detection and the temporal baseline can
    never disagree on the same artifact (red-team #1 root cause: two divergent
    membership rules)."""
    from ..synthesis import assign_window

    window = assign_window(artifact, windows)
    if window == "prior":
        return "comparison"
    return None if window == "outside" else window


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
    n_prior_arts = len({c.artifact_id for c in prior})
    for theme, arts in current_themes.items():
        if len(arts) < 2:
            continue
        prior_ids = list(dict.fromkeys(prior_themes.get(theme, [])))
        if not prior_ids:
            # True emergence: the theme has ZERO prior occurrences. The prior
            # evidence is the window SAMPLE (grounding gate requires non-empty
            # ids for both periods) — marked as such, never presented as pages
            # that contain the theme.
            candidates.append(
                {
                    "dimension": "theme_emergence",
                    "prior_state": (
                        f"“{theme}” not observed in any of the {n_prior_arts} "
                        "dated prior-window artifacts"
                    ),
                    "current_state": f"“{theme}” present in {len(arts)} current-window artifacts",
                    "prior_artifact_ids": [c.artifact_id for c in prior][:5],
                    "current_artifact_ids": arts,
                    "prior_count": 0,
                    "prior_window_n": n_prior_arts,
                    "prior_evidence_role": "window_sample",
                    "theme": theme,
                    "emergence": True,
                }
            )
        elif len(arts) >= max(3, 2 * len(prior_ids)):
            # Rare-prior expansion: present then, materially more present now.
            # Never phrased as "not observed" (red-team: that wording
            # contradicted the full-corpus baseline).
            candidates.append(
                {
                    "dimension": "theme_emergence",
                    "prior_state": (
                        f"“{theme}” present but rare in the prior window "
                        f"({len(prior_ids)} of {n_prior_arts} dated artifacts, "
                        "incl. supporting-theme mentions)"
                    ),
                    "current_state": f"“{theme}” present in {len(arts)} current-window artifacts",
                    "prior_artifact_ids": prior_ids[:5],
                    "current_artifact_ids": arts,
                    "prior_count": len(prior_ids),
                    "prior_window_n": n_prior_arts,
                    "prior_evidence_role": "theme_occurrences",
                    "theme": theme,
                    "emergence": True,
                    "expansion": True,
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
            lifecycle = "expanding" if candidate.get("expansion") else "emerging"
            confidence = "low"
            if candidate.get("expansion"):
                asym = (
                    f"prior-window sample is small (n={candidate.get('prior_window_n', 0)}); "
                    "the growth ratio may reflect collection depth rather than a real shift"
                )
            else:
                asym = (
                    "this may be a collection/archive coverage asymmetry (current-window news "
                    "collected without a comparable prior-window surface) rather than a real "
                    "messaging change"
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
                theme=candidate.get("theme"),
                prior_theme_count=candidate.get("prior_count")
                if candidate.get("emergence")
                else None,
                current_theme_count=len(candidate["current_artifact_ids"]),
                prior_window_n=candidate.get("prior_window_n"),
                prior_evidence_role=candidate.get("prior_evidence_role"),
            )
        )
    return events


_THEME_IN_STATE_RX = re.compile(r"[“\"']([a-z_]+)[”\"']")


def reconcile_change_events(
    events: list[dict[str, Any]],
    classifications: list[Any],
    artifacts: list[Any],
    time_windows: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reconcile persisted change events against the FINAL full corpus.

    Change events are detected mid-run on whatever partial corpus existed at
    that iteration (red-team #1: an event said a theme was "not observed in the
    prior-window sample" while the final baseline counted it in BOTH windows).
    This recomputes each theme's real prior/current occurrences under the same
    window predicate the baseline uses (synthesis.assign_window) and rewrites
    the event deterministically — no render-time LLM:

    - prior count 0  -> keep as emerging; prior ids = window sample (marked).
    - prior count >0 -> relabel lifecycle=expanding, prose "present but rare";
      prior ids = the artifacts that actually contain the theme.
    - current occurrences < 2 -> drop (the mid-run signal didn't survive).
    - scalar dimensions -> keep only if the final-corpus dominant values still
      match the persisted prior/current states; else drop.

    Returns (reconciled_events, notes). Never empties evidence-id lists (the
    grounding gate requires both periods non-empty).
    """
    from ..synthesis import assign_window

    art_by_id = {a.artifact_id: a for a in artifacts}
    window_of: dict[str, str] = {a.artifact_id: assign_window(a, time_windows) for a in artifacts}
    prior_cls = [c for c in classifications if window_of.get(c.artifact_id) == "prior"]
    current_cls = [c for c in classifications if window_of.get(c.artifact_id) == "current"]

    def theme_ids(items: list[Any]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for c in items:
            for t in [
                getattr(c, "primary_theme", None),
                *(getattr(c, "supporting_themes", []) or []),
            ]:
                if t and c.artifact_id not in out.setdefault(t, []):
                    out[t].append(c.artifact_id)
        return out

    prior_by_theme = theme_ids(prior_cls)
    current_by_theme = theme_ids(current_cls)
    prior_sample = list(dict.fromkeys(c.artifact_id for c in prior_cls))
    n_prior = len(prior_sample)

    def dominant(items: list[Any], attr: str) -> str | None:
        counts: dict[str, int] = {}
        for c in items:
            value = getattr(c, attr, None)
            if value:
                counts[value] = counts.get(value, 0) + 1
        return max(counts, key=lambda k: counts[k]) if counts else None

    n_current = len({c.artifact_id for c in current_cls})
    prior_id_set = set(prior_sample)

    def _salvage_alts(ev: dict[str, Any]) -> list[str]:
        """Persisted alternative_explanations were written mid-run against a
        PARTIAL corpus — any that state sample sizes/counts or assert absence
        are stale by construction (verifier: 'only 6 artifacts' shipped next to
        a reconciled 'n of 14' prior_state). Keep only number-free, non-absence
        qualitative alts; the correct quantitative caveat is prepended fresh."""
        kept = []
        for a in ev.get("alternative_explanations", []):
            text = str(a)
            if re.search(r"\d", text):
                continue
            if re.search(r"absen|not observed|missed|coverage asymmetry", text, re.IGNORECASE):
                continue
            kept.append(text)
        return kept

    reconciled: list[dict[str, Any]] = []
    notes: list[str] = []
    for ev in events:
        dim = str(ev.get("dimension") or "")
        if dim == "theme_emergence":
            theme = ev.get("theme")
            if not theme:
                m = _THEME_IN_STATE_RX.search(str(ev.get("current_state", "")))
                theme = m.group(1) if m else None
            if not theme:
                notes.append(f"{ev.get('change_id')}: theme unparseable — kept unreconciled")
                reconciled.append(ev)
                continue
            cur_ids = current_by_theme.get(theme, [])
            prior_ids = prior_by_theme.get(theme, [])
            if len(cur_ids) < 2:
                notes.append(
                    f"{ev.get('change_id')} ({theme}): dropped — only "
                    f"{len(cur_ids)} current-window occurrence(s) in the final corpus"
                )
                continue
            out = dict(ev)
            out["theme"] = theme
            out["prior_theme_count"] = len(prior_ids)
            out["current_theme_count"] = len(cur_ids)
            out["prior_window_n"] = n_prior
            out["current_evidence_ids"] = cur_ids
            out["current_state"] = f"“{theme}” present in {len(cur_ids)} current-window artifacts"
            if prior_ids:
                out["lifecycle"] = "expanding"
                out["prior_state"] = (
                    f"“{theme}” present but rare in the prior window "
                    f"({len(prior_ids)} of {n_prior} dated artifacts, "
                    "incl. supporting-theme mentions)"
                )
                out["prior_evidence_ids"] = prior_ids[:5]
                out["prior_evidence_role"] = "theme_occurrences"
                out["alternative_explanations"] = [
                    f"prior-window sample is small ({n_prior} dated artifacts vs "
                    f"{n_current} current); the growth ratio may reflect collection "
                    "depth rather than a real shift"
                ] + _salvage_alts(ev)
                if str(ev.get("prior_state", "")).find("not observed") >= 0:
                    notes.append(
                        f"{ev.get('change_id')} ({theme}): relabeled emerging→expanding — "
                        f"final corpus has {len(prior_ids)} prior-window occurrence(s)"
                    )
            else:
                out["lifecycle"] = "emerging"
                out["prior_state"] = (
                    f"“{theme}” not observed in any of the {n_prior} dated prior-window artifacts"
                )
                # Grounding gate: prior ids stay non-empty — the window sample,
                # explicitly marked as such. Persisted mid-run ids are only
                # trusted if the FINAL predicate still puts them in the prior
                # window (they were exactly the stale-id failure mode).
                stale_ids = [a for a in (ev.get("prior_evidence_ids") or []) if a in prior_id_set]
                out["prior_evidence_ids"] = stale_ids or prior_sample[:5]
                out["prior_evidence_role"] = "window_sample"
                out["alternative_explanations"] = [
                    "this may be a collection/archive coverage asymmetry "
                    f"({n_prior} dated prior-window artifacts vs {n_current} current) "
                    "rather than a real messaging change"
                ] + _salvage_alts(ev)
            reconciled.append(out)
        elif dim in _SCALAR_DIMENSIONS:
            prior_val = dominant(prior_cls, dim)
            cur_val = dominant(current_cls, dim)
            same = (
                prior_val is not None
                and cur_val is not None
                and str(prior_val).strip().lower() == str(ev.get("prior_state", "")).strip().lower()
                and str(cur_val).strip().lower() == str(ev.get("current_state", "")).strip().lower()
            )
            if same:
                out = dict(ev)
                out["prior_window_n"] = n_prior
                reconciled.append(out)
            else:
                notes.append(
                    f"{ev.get('change_id')} ({dim}): dropped — mid-run verdict not "
                    f"reproducible on the final corpus (now {prior_val!r} -> {cur_val!r})"
                )
        else:
            reconciled.append(ev)
    del art_by_id
    return reconciled, notes
