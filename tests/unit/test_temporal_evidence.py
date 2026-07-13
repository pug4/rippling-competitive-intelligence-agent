"""Temporal judge evidence-rendering + candidate/event dedup (P0 item 4).

The temporal judge must see the ACTUAL stored artifact text, not bare
``- ART-xxx`` id lines (which it can't verify anything from, forcing every
verdict to low confidence with boilerplate alternatives). It must also never
score — or emit — the same candidate change twice (red-team: 5 literal
duplicate temporal bullets from a dedup gap).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import competitive_agent.processing.temporal as temporal
from competitive_agent.processing.temporal import (
    _dedup_candidates,
    _render_evidence_block,
    build_change_events,
)
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.change import ChangeEvent
from competitive_agent.schemas.company import TimeWindow

# --- fakes -----------------------------------------------------------------


class MarketingClassification:
    """Stand-in whose class NAME matches the merged-family filter in
    ``build_change_events``. Only the attributes the engine reads are set."""

    def __init__(
        self,
        artifact_id: str,
        primary_theme: str,
        supporting_themes: list[str] | None = None,
    ) -> None:
        self.artifact_id = artifact_id
        self.primary_theme = primary_theme
        self.supporting_themes = supporting_themes or []
        self.pricing_disclosure_level = None
        self.competitive_stance = None


class SpyGateway:
    """Captures every rendered user_content and returns a fixed verdict."""

    def __init__(self, verdict: temporal.TemporalJudgeVerdict) -> None:
        self._verdict = verdict
        self.calls: list[str] = []

    async def generate_structured(self, *_a, user_content: str = "", **_k):
        self.calls.append(user_content)
        return SimpleNamespace(output=self._verdict)


class FakeRepo:
    def __init__(self, classifications: list, artifacts: list) -> None:
        self._classifications = classifications
        self._artifacts = artifacts

    def list_classifications(self, _run_id: str, *, family: str | None = None) -> list:
        return list(self._classifications)

    def list_artifacts(self, *, run_id: str | None = None, company_id: str | None = None) -> list:
        return list(self._artifacts)


def _artifact(
    artifact_id: str,
    *,
    title: str,
    source_type: str,
    normalized_text: str,
    archive_capture_at: datetime | None = None,
    published_at: datetime | None = None,
) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        company_id="c1",
        source_type=source_type,
        source_name="Deel",
        url=f"https://deel.com/{artifact_id}",
        final_url=f"https://deel.com/{artifact_id}",
        title=title,
        published_at=published_at,
        retrieved_at=datetime(2025, 7, 1, tzinfo=UTC),
        raw_text="",
        normalized_text=normalized_text,
        content_hash=f"hash-{artifact_id}",
        collection_method="fixture",
        archive_capture_at=archive_capture_at,
    )


def _windows() -> list[TimeWindow]:
    return [
        TimeWindow(
            window_id="w-comp",
            label="comparison",
            start_at=datetime(2025, 1, 1, tzinfo=UTC),
            end_at=datetime(2025, 3, 1, tzinfo=UTC),
            purpose="comparison",
        ),
        TimeWindow(
            window_id="w-cur",
            label="current",
            start_at=datetime(2025, 6, 1, tzinfo=UTC),
            end_at=datetime(2025, 7, 11, tzinfo=UTC),
            purpose="current",
        ),
    ]


def _ctx(repo: FakeRepo, gateway: SpyGateway) -> SimpleNamespace:
    return SimpleNamespace(repository=repo, gateway=gateway, trace=None, scratch={})


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        time_windows=_windows(),
        coverage={"historical_website": "medium"},
        company=SimpleNamespace(company_id="c1"),
    )


# --- unit: evidence block --------------------------------------------------


def test_evidence_block_renders_titled_dated_excerpt_not_bare_id():
    art = _artifact(
        "ART-PRIOR-1",
        title="Deel Homepage",
        source_type="wayback",
        normalized_text="Deel is the all-in-one global hiring and payroll platform.",
        archive_capture_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    block = _render_evidence_block(["ART-PRIOR-1"], {"ART-PRIOR-1": art})
    # Real evidence text is present...
    assert "all-in-one global hiring and payroll platform" in block
    assert "Deel Homepage" in block
    assert "wayback" in block
    assert "2025-02-01" in block
    # ...and NO bare id-only line survives.
    assert "- ART-PRIOR-1" not in block
    assert "- ART-PRIOR-1\n" not in block


def test_evidence_block_bounds_artifacts_and_truncates_honestly():
    long_text = "Consolidation is the new all-in-one message for enterprise buyers. " * 20
    arts = {}
    ids = []
    for i in range(11):
        aid = f"ART-{i}"
        ids.append(aid)
        arts[aid] = _artifact(
            aid,
            title=f"Page {i}",
            source_type="webpage",
            normalized_text=long_text,
            published_at=datetime(2025, 6, 15, tzinfo=UTC),
        )
    block = _render_evidence_block(ids, arts, max_artifacts=8, excerpt_chars=300)
    lines = [ln for ln in block.splitlines() if ln.startswith("- [")]
    assert len(lines) == 8  # bounded
    assert "[…truncated]" in block  # long text truncated honestly
    assert "+3 more artifact(s)" in block  # omission stated honestly


def test_evidence_block_marks_missing_and_empty_text_honestly():
    art = _artifact("ART-EMPTY", title="Blank", source_type="news", normalized_text="")
    block = _render_evidence_block(["ART-EMPTY", "ART-GONE"], {"ART-EMPTY": art})
    assert "(no stored text for this artifact)" in block
    assert "(artifact text not available in the store)" in block


# --- unit: dedup -----------------------------------------------------------


def test_dedup_candidates_collapses_identical():
    c = {
        "dimension": "primary_theme",
        "theme": None,
        "prior_state": "global_hiring",
        "current_state": "consolidation",
    }
    out = _dedup_candidates([c, dict(c), dict(c)])
    assert len(out) == 1


def test_dedup_candidates_keeps_distinct():
    a = {"dimension": "primary_theme", "prior_state": "x", "current_state": "y"}
    b = {"dimension": "competitive_stance", "prior_state": "x", "current_state": "y"}
    assert len(_dedup_candidates([a, b])) == 2


# --- integration: build_change_events end-to-end ---------------------------


def _build_setup():
    prior_art = _artifact(
        "ART-PRIOR-1",
        title="Deel Homepage (archived)",
        source_type="wayback",
        normalized_text=(
            "Deel makes global hiring effortless — hire contractors and employees "
            "in 150 countries with localized compliance."
        ),
        archive_capture_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    current_art = _artifact(
        "ART-CUR-1",
        title="Deel Homepage (live)",
        source_type="webpage",
        normalized_text=(
            "One platform to consolidate HR, payroll, and IT — replace your "
            "fragmented stack with Deel's all-in-one workforce system."
        ),
        published_at=datetime(2025, 6, 15, tzinfo=UTC),
    )
    classifications = [
        MarketingClassification("ART-PRIOR-1", "global_hiring"),
        MarketingClassification("ART-CUR-1", "consolidation"),
    ]
    repo = FakeRepo(classifications, [prior_art, current_art])
    gateway = SpyGateway(
        temporal.TemporalJudgeVerdict(
            verdict="verified",
            lifecycle="repositioned",
            alternative_explanations=["archive coverage gaps could partially explain it"],
            confidence="medium",
            coverage="medium",
            reason="distinct primary messages across windows",
        )
    )
    return repo, gateway


async def test_build_change_events_feeds_real_evidence_to_judge():
    repo, gateway = _build_setup()
    events = await build_change_events("run-1", _state(), _ctx(repo, gateway))

    assert len(events) == 1
    assert isinstance(events[0], ChangeEvent)
    assert gateway.calls, "judge should have been invoked"
    rendered = gateway.calls[0]
    # Real prior + current excerpts reached the judge...
    assert "global hiring effortless" in rendered
    assert "consolidate HR, payroll, and IT" in rendered
    assert "Deel Homepage (archived)" in rendered
    # ...and the old bare id-only lines are gone.
    assert "- ART-PRIOR-1\n" not in rendered
    assert "- ART-CUR-1\n" not in rendered
    # The evidence sits under the promised prompt headers.
    assert "PRIOR-PERIOD EVIDENCE" in rendered
    assert "CURRENT-PERIOD EVIDENCE" in rendered


def test_reconcile_dedups_identical_rendered_events():
    from competitive_agent.processing.temporal import reconcile_change_events

    prior_art = _artifact(
        "ART-PRIOR-1",
        title="prior",
        source_type="wayback",
        normalized_text="x",
        archive_capture_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    current_art = _artifact(
        "ART-CUR-1",
        title="cur",
        source_type="webpage",
        normalized_text="y",
        published_at=datetime(2025, 6, 15, tzinfo=UTC),
    )
    classifications = [
        MarketingClassification("ART-PRIOR-1", "global_hiring"),
        MarketingClassification("ART-CUR-1", "consolidation"),
    ]
    ev = {
        "change_id": "CHG1",
        "dimension": "primary_theme",
        "prior_state": "global_hiring",
        "current_state": "consolidation",
        "alternative_explanations": [],
    }
    ev2 = dict(ev, change_id="CHG2")  # a literal duplicate persisted separately
    reconciled, notes = reconcile_change_events(
        [ev, ev2], classifications, [prior_art, current_art], _windows()
    )
    assert len(reconciled) == 1  # identical change rendered only once
    assert any("duplicate" in n for n in notes)


async def test_build_change_events_dedups_duplicate_candidates(monkeypatch):
    repo, gateway = _build_setup()
    dup = {
        "dimension": "primary_theme",
        "prior_state": "global_hiring",
        "current_state": "consolidation",
        "prior_artifact_ids": ["ART-PRIOR-1"],
        "current_artifact_ids": ["ART-CUR-1"],
        "prior_count": 1,
    }
    monkeypatch.setattr(
        temporal, "detect_candidate_changes", lambda _by_window: [dict(dup), dict(dup)]
    )
    events = await build_change_events("run-1", _state(), _ctx(repo, gateway))
    # Two identical candidates in -> exactly one event out, judged once.
    assert len(events) == 1
    assert len(gateway.calls) == 1
