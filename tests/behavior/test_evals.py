"""Phase 6: eval-harness unit behavior — split determinism, field projection,
grounding gates, and the honest-report provisional banner. The full benchmark
(classification vs. independent labeler) needs a live model and is exercised
via the CLI against a real run, not here."""

from __future__ import annotations

import pytest


def test_split_is_deterministic_and_order_independent():
    from competitive_agent.evals.dataset import _split_for

    a = _split_for("ART-abc123")
    b = _split_for("ART-abc123")
    assert a == b
    assert a in ("dev", "heldout")
    # Different ids can land in different splits; the mapping is stable per id.
    assert _split_for("ART-zzz999") == _split_for("ART-zzz999")


def test_classification_projection_takes_first_of_list_fields():
    from competitive_agent.evals.labeling import classification_to_pred

    class MC:
        primary_message = "Run payroll in every country"
        primary_theme = None
        secondary_messages = ["compliance", "speed"]
        supporting_themes: list = []
        message_salience = 0.8
        segments = ["enterprise", "mid_market"]
        personas = ["CFO"]
        category_entry_points = ["expanding internationally"]
        funnel_stages = ["consideration"]
        claim_types = ["outcome"]
        proof_types = ["customer_logo", "stat"]
        competitive_stance = "named_comparison"

    pred = classification_to_pred(MC())
    assert pred["segment"] == "enterprise"  # first of the list
    assert pred["persona"] == "CFO"
    assert pred["salience_band"] == "high"  # 0.8 -> high
    assert pred["proof_types"] == ["customer_logo", "stat"]
    assert pred["primary_message"] == "Run payroll in every country"


def test_label_projection_maps_to_scorer_fields():
    from competitive_agent.evals.labeling import ArtifactLabel, label_to_gold

    label = ArtifactLabel(primary_message="All-in-one HR", salience_band="low", segment="smb")
    gold = label_to_gold(label)
    assert gold["primary_message"] == "All-in-one HR"
    assert gold["salience_band"] == "low"
    assert gold["segment"] == "smb"
    # not_observed excerpts become None so the scorer skips them.
    assert gold["exact_supporting_excerpt"] is None


def test_grounding_flags_unsupported_opportunity_and_broken_claim():
    from competitive_agent.evals.grounding import check_package

    pkg = {
        "claims": [
            {"claim_id": "C1", "status": "accepted", "evidence_ids": ["E1"]},
            {"claim_id": "C2", "status": "accepted", "evidence_ids": []},  # broken
            {"claim_id": "C3", "status": "rejected", "evidence_ids": []},  # skipped
        ],
        "change_events": [
            {"change_id": "CH1", "prior_evidence_ids": ["E1"], "current_evidence_ids": ["E2"]},
            {
                "change_id": "CH2",
                "prior_evidence_ids": [],
                "current_evidence_ids": ["E2"],
            },  # missing period
        ],
        "opportunities": [
            {"opportunity_id": "O1", "supporting_claim_ids": ["C1"]},
            {"opportunity_id": "O2", "supporting_claim_ids": []},  # unsupported
        ],
    }
    g = check_package(pkg)
    assert g.ok is False
    assert g.broken_evidence_refs == ["C2"]
    assert g.changes_missing_period == ["CH2"]
    assert g.opportunities_missing_support == ["O2"]
    assert g.material_claims == 2  # rejected excluded
    assert g.citation_coverage() == pytest.approx(0.5)


def test_report_carries_provisional_banner():
    from competitive_agent.evals.report import render_report

    result = {
        "composition": {
            "total": 10,
            "by_company": {"deel.com": 10},
            "by_source_type": {},
            "by_split": {},
        },
        "scored_split": "heldout",
        "n_scored": 5,
        "n_failed": 0,
        "layer_a_schema_validity": 1.0,
        "layer_b_excerpt_validity": 1.0,
        "layer_c_grounding": {
            "ok": True,
            "citation_coverage": 1.0,
            "material_claims": 3,
            "grounded_claims": 3,
            "broken_evidence_refs": [],
            "excerpts_checked": 3,
            "excerpts_unverified": [],
            "opportunities_missing_support": [],
            "changes_missing_period": [],
        },
        "layer_d_classification": {
            "n_artifacts": 5,
            "single_field_agreement": {"segment": 0.8},
            "ordinal_field_agreement": {},
            "multi_field_prf": {},
            "excerpt_validity": 1.0,
            "unsupported_inference_rate": 0.0,
            "note": "inter-model agreement",
        },
    }
    md = render_report(result)
    assert "PROVISIONAL" in md
    assert "not human-adjudicated" in md.lower() or "not* human-adjudicated" in md.lower()
    assert "Layer C" in md and "PASS" in md
