"""The HTML dashboard renders the data + gaps as self-contained charts (no
external hosts) from the same validated package the brief/UI use."""

from __future__ import annotations

from competitive_agent.visualize import build_dashboard

_PKG = {
    "run": {"run_id": "RUN-x", "mode": "comparative", "execution_mode": "fixture", "generated_at": "2026-07-12T00:00:00"},
    "scope": {"company_input": "deel.com"},
    "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
    "source_distribution": {"webpage": 6, "comparison pages": 3, "wayback snapshots": 2},
    "classifications": [
        {"primary_theme": "consolidation", "competitive_stance": "implicit_contrast"},
        {"primary_theme": "consolidation", "competitive_stance": "named_comparison"},
        {"primary_theme": "compliance", "competitive_stance": "implicit_contrast"},
    ],
    "proof_gaps": [
        {"claim_id": "C1", "short_label": "compliance", "attackability": "medium",
         "proof_strength": "weak", "focal_proof_strength": "strong", "claim_specificity": "high",
         "missing_proof": ["independent_validation"]},
    ],
    "opportunities": [{"title": "x"}],
    "change_events": [],
    "persona_channel_matrix": {
        "personas": ["hr_leader", "it_leader"],
        "channels": ["website", "events"],
        "cells": {"hr_leader": {"website": 20, "events": 2}, "it_leader": {"website": 5}},
    },
    "eval_summary": {"n_artifacts": 11, "n_classifications": 3, "n_proof_gaps": 1, "n_opportunities": 1, "n_change_events": 0},
}


def test_dashboard_is_self_contained_html():
    out = build_dashboard(_PKG)
    assert "<style>" in out and "<h1>" in out
    # No external hosts / scripts (strict self-containment).
    assert "http://" not in out and "https://" not in out
    assert "<script" not in out and "src=" not in out


def test_dashboard_renders_all_chart_sections():
    out = build_dashboard(_PKG)
    for section in ("Source mix", "message themes", "Competitive stance", "Message–proof gaps", "Persona × channel"):
        assert section in out
    # Data actually rendered.
    assert "Deel" in out and "Rippling" in out
    assert "consolidation" in out  # top theme
    assert "compliance" in out  # gap label


def test_dashboard_handles_empty_package():
    minimal = {"run": {}, "scope": {}, "companies": [], "classifications": [], "proof_gaps": [],
               "source_distribution": {}, "persona_channel_matrix": {}, "eval_summary": {}}
    out = build_dashboard(minimal)  # must not raise
    assert "<h1>" in out
