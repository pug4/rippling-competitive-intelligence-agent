"""Buyer-voice mining (REVIEWS contract): schema, containment drops, rollup.

No network, no live providers: the gateway is either the FixtureGateway over
tests/fixtures or a local fake. Rollup tests drive the pure counting function
directly on crafted classification records.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from competitive_agent.config import Settings
from competitive_agent.model_gateway import FixtureGateway
from competitive_agent.processing.buyer_voice import (
    mine_review_artifact,
    render_competitor_claims,
    sanitize_signals,
)
from competitive_agent.processing.normalize import content_hash, normalize_text
from competitive_agent.prompt_registry import PromptRegistry
from competitive_agent.report import _BUYER_VOICE_NOTE, buyer_voice_rollup
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.buyer_voice import (
    BuyerVoiceAlternative,
    BuyerVoiceSignals,
    BuyerVoiceTheme,
    MessageRealitySignal,
)
from competitive_agent.schemas.common import utcnow

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

# Same text as tests/fixtures/tools/reviews/search_reviews.json artifact 1, so
# the mine_reviews model fixture quotes verify against it.
REVIEW_TEXT = (
    "Overall a solid HR platform, but the onboarding is painful. Cons: setup "
    "took weeks, support was slow to respond, and reporting is limited. Pros: "
    "contractor payments are easy and the UI is clean."
)


def _artifact(text: str = REVIEW_TEXT, artifact_id: str = "ART-rev-1") -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        company_id="CO-test",
        source_type="reviews",
        source_name="reviews",
        url="https://www.g2.com/products/example-hr/reviews",
        final_url="https://www.g2.com/products/example-hr/reviews",
        title="Example HR Reviews",
        retrieved_at=utcnow(),
        raw_text=text,
        normalized_text=normalize_text(text),
        content_hash=content_hash(text),
        collection_method="exa_reviews",
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_is_missing_field_tolerant_and_round_trips() -> None:
    # The prompt emits only the signal lists; provenance fields are stamped
    # later, so validation must succeed with almost everything missing.
    signals = BuyerVoiceSignals.model_validate(
        {"objections": [{"theme": "implementation_pain", "quote": "setup took weeks"}]}
    )
    assert signals.artifact_id == ""
    assert signals.company_id == ""
    assert signals.source_url == ""
    assert signals.objections[0].confidence == "low"
    assert signals.praise == []
    assert signals.buyer_contexts == []
    assert signals.classifier_confidence == "low"

    # Persistence round-trip (what save_payload/load_payload do).
    restored = BuyerVoiceSignals.model_validate_json(signals.model_dump_json())
    assert restored == signals


def test_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BuyerVoiceSignals.model_validate({"sentiment_percentage": 90})


# ---------------------------------------------------------------------------
# Quote containment (drop-and-log)
# ---------------------------------------------------------------------------


def test_sanitize_drops_unverified_quotes_and_stamps_provenance() -> None:
    artifact = _artifact()
    raw = BuyerVoiceSignals(
        artifact_id="ART-forged",
        company_id="CO-forged",
        source_url="https://forged.example",
        objections=[
            BuyerVoiceTheme(theme="implementation_pain", quote="the onboarding is painful"),
            BuyerVoiceTheme(theme="data_loss", quote="it deleted all our payroll data"),
        ],
        praise=[
            BuyerVoiceTheme(
                theme="easy_contractor_payments",
                quote="contractor payments are easy and the UI is clean",
            )
        ],
        alternatives=[
            BuyerVoiceAlternative(
                alternative="OtherHR", direction="switched_to", quote="we moved to OtherHR"
            )
        ],
        message_reality_signals=[
            MessageRealitySignal(
                claim_theme="ease_of_use", relation="contradicts", quote="setup took weeks"
            )
        ],
    )
    clean = sanitize_signals(raw, artifact)

    # Provenance is stamped from the artifact, never trusted from the model.
    assert clean.artifact_id == artifact.artifact_id
    assert clean.company_id == artifact.company_id
    assert clean.source_url == artifact.final_url

    # Verified quotes survive; fabricated ones are dropped, never rewritten.
    assert [s.theme for s in clean.objections] == ["implementation_pain"]
    assert [s.theme for s in clean.praise] == ["easy_contractor_payments"]
    assert clean.alternatives == []
    assert [s.claim_theme for s in clean.message_reality_signals] == ["ease_of_use"]

    # Every drop is logged in unclassified_signals (drop-and-log pattern).
    notes = [n for n in clean.unclassified_signals if n.startswith("unverified_")]
    assert len(notes) == 2
    assert any(n.startswith("unverified_objection_quote_dropped:") for n in notes)
    assert any(n.startswith("unverified_alternative_quote_dropped:") for n in notes)


def test_sanitize_drops_empty_quote_signals() -> None:
    # A signal with no quote is ungrounded by definition -> dropped.
    artifact = _artifact()
    raw = BuyerVoiceSignals(objections=[BuyerVoiceTheme(theme="support_pain", quote="")])
    clean = sanitize_signals(raw, artifact)
    assert clean.objections == []
    assert any(
        n.startswith("unverified_objection_quote_dropped:") for n in clean.unclassified_signals
    )


# ---------------------------------------------------------------------------
# Fixture-mode mining (zero API keys)
# ---------------------------------------------------------------------------


def test_mine_review_artifact_with_fixture_gateway() -> None:
    gateway = FixtureGateway(Settings(fixtures_dir=FIXTURES_DIR))
    result = asyncio.run(
        mine_review_artifact(
            _artifact(),
            gateway,
            PromptRegistry(),
            competitor_name="Example-HR",
            competitor_claims="- ease_of_use · Set up in minutes",
        )
    )
    assert result is not None
    assert result.artifact_id == "ART-rev-1"
    # Quotes present in THIS artifact's text survive...
    assert "implementation_pain" in [s.theme for s in result.objections]
    assert "easy_contractor_payments" in [s.theme for s in result.praise]
    assert [s.relation for s in result.message_reality_signals] == ["contradicts"]
    # ...while the fixture's quotes from the OTHER review page are dropped.
    assert "pricing_at_renewal" not in [s.theme for s in result.objections]
    assert result.alternatives == []
    assert any(n.startswith("unverified_") for n in result.unclassified_signals)


def test_mine_review_artifact_missing_fixture_degrades_to_none(tmp_path: Path) -> None:
    gateway = FixtureGateway(Settings(fixtures_dir=tmp_path))
    result = asyncio.run(
        mine_review_artifact(_artifact(), gateway, PromptRegistry(), competitor_name="Example-HR")
    )
    assert result is None  # typed honest degrade: nothing persisted, nothing counted


def test_render_competitor_claims_dedupes_and_defaults() -> None:
    class _Cls:
        def __init__(self, theme: str | None, message: str | None) -> None:
            self.primary_theme = theme
            self.primary_message = message

    text = render_competitor_claims(
        [
            _Cls("ease_of_use", "Set up in minutes"),
            _Cls("ease_of_use", "Set up in minutes"),
            _Cls(None, "Global payroll everywhere"),
            _Cls("compliance", None),
        ]
    )
    assert text == "- ease_of_use · Set up in minutes\n- unthemed · Global payroll everywhere"
    assert render_competitor_claims([]) == "none provided"


# ---------------------------------------------------------------------------
# Deterministic rollup (render-time counting only)
# ---------------------------------------------------------------------------


def _record(**kwargs: Any) -> dict[str, Any]:
    return json.loads(BuyerVoiceSignals(**kwargs).model_dump_json())


def test_rollup_counts_exactly() -> None:
    rec1 = _record(
        artifact_id="ART-1",
        company_id="CO-1",
        source_url="https://g2.example/r1",
        objections=[
            BuyerVoiceTheme(theme="implementation_pain", quote="setup took weeks"),
        ],
        praise=[BuyerVoiceTheme(theme="clean_ui", quote="the UI is clean")],
        alternatives=[
            BuyerVoiceAlternative(
                alternative="LegacySuite",
                direction="switched_from",
                quote="we switched from LegacySuite",
            ),
            # 'evaluated' is not a switching trigger -> excluded from that list.
            BuyerVoiceAlternative(
                alternative="OtherHR", direction="evaluated", quote="we also looked at OtherHR"
            ),
        ],
        message_reality_signals=[
            MessageRealitySignal(
                claim_theme="easy_setup", relation="contradicts", quote="onboarding was painful"
            )
        ],
    )
    rec2 = _record(
        artifact_id="ART-2",
        company_id="CO-1",
        source_url="https://capterra.example/r2",
        objections=[
            BuyerVoiceTheme(theme="implementation_pain", quote="rollout dragged on for months"),
        ],
        praise=[BuyerVoiceTheme(theme="global_payroll", quote="great for global payroll")],
    )

    assert buyer_voice_rollup([rec1, rec2]) == {
        "n_reviews": 2,
        "themes": [
            {
                "theme": "implementation_pain",
                "sentiment": "negative",
                "n": 2,
                "example_quote": "setup took weeks",
                "source_url": "https://g2.example/r1",
            },
            {
                "theme": "clean_ui",
                "sentiment": "positive",
                "n": 1,
                "example_quote": "the UI is clean",
                "source_url": "https://g2.example/r1",
            },
            {
                "theme": "global_payroll",
                "sentiment": "positive",
                "n": 1,
                "example_quote": "great for global payroll",
                "source_url": "https://capterra.example/r2",
            },
        ],
        "switching_triggers": [
            {
                "theme": "LegacySuite",
                "direction": "switched_from",
                "n": 1,
                "example_quote": "we switched from LegacySuite",
                "source_url": "https://g2.example/r1",
            }
        ],
        "objections": [
            {
                "theme": "implementation_pain",
                "sentiment": "negative",
                "n": 2,
                "example_quote": "setup took weeks",
                "source_url": "https://g2.example/r1",
            }
        ],
        "message_reality": [
            {
                "theme": "easy_setup",
                "relation": "contradicts",
                "n": 1,
                "example_quote": "onboarding was painful",
                "source_url": "https://g2.example/r1",
            }
        ],
        "note": _BUYER_VOICE_NOTE,
    }


def test_rollup_empty_is_honest() -> None:
    assert buyer_voice_rollup([]) == {
        "n_reviews": 0,
        "themes": [],
        "switching_triggers": [],
        "objections": [],
        "message_reality": [],
        "note": _BUYER_VOICE_NOTE,
    }
