"""The HTML dashboard renders the data + gaps as self-contained charts (no
external hosts) from the same validated package the brief/UI use."""

from __future__ import annotations

from competitive_agent.visualize import build_dashboard

_PKG = {
    "run": {
        "run_id": "RUN-x",
        "mode": "comparative",
        "execution_mode": "fixture",
        "generated_at": "2026-07-12T00:00:00",
    },
    "scope": {"company_input": "deel.com"},
    "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
    "source_distribution": {"webpage": 6, "comparison pages": 3, "wayback snapshots": 2},
    "classifications": [
        {
            "primary_theme": "consolidation",
            "competitive_stance": "implicit_contrast",
            "personas": ["hr_leader"],
            "funnel_stages": ["awareness"],
            "villain_normalized": ["point_solution_sprawl"],
        },
        {
            "primary_theme": "consolidation",
            "competitive_stance": "named_comparison",
            "personas": ["it_leader"],
            "funnel_stages": ["consideration"],
        },
        {
            "primary_theme": "compliance",
            "competitive_stance": "implicit_contrast",
            "personas": ["hr_leader"],
            "funnel_stages": ["evaluation"],
            "villain_normalized": ["compliance_risk"],
        },
    ],
    "proof_gaps": [
        {
            "claim_id": "C1",
            "short_label": "compliance",
            "attackability": "medium",
            "proof_strength": "weak",
            "focal_proof_strength": "strong",
            "claim_specificity": "high",
            "missing_proof": ["independent_validation"],
        },
    ],
    "category_entry_points": [
        {
            "cep": "consolidating_hr_tools",
            "competitor_pages": 29,
            "focal_pages": 48,
            "ownership": "contested",
        },
        {
            "cep": "opening_new_country",
            "competitor_pages": 39,
            "focal_pages": 13,
            "ownership": "competitor_advantage",
        },
    ],
    "linkedin_posts": [
        {
            "artifact_id": "ART-p1",
            "author": "Dana Lee",
            "author_role": "VP Product",
            "post_url": "https://www.linkedin.com/posts/dana_x",
            "theme": "consolidation",
            "competitive_stance": "category_attack",
            "personas": ["hr_leader"],
            "excerpt": "Stop juggling a dozen HR tools.",
        },
    ],
    "similarweb": {
        "domain": "deel.com",
        "data_source": "similarweb",
        "estimated": True,
        "metrics": {"estimated_monthly_visits": {"value": 5000000, "estimated": True}},
    },
    "opportunities": [{"title": "x"}],
    "change_events": [
        {
            "change_id": "CH1",
            "dimension": "theme_emergence",
            "confidence": "low",
            "lifecycle": "emerging",
            "prior_state": "“cost_reduction” not observed",
            "current_state": "“cost_reduction” present in 9 current-window artifacts",
            "prior_evidence_ids": ["a", "b", "c", "d"],
            "current_evidence_ids": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
            "alternative_explanations": ["may be coverage asymmetry"],
        },
    ],
    "persona_channel_matrix": {
        "personas": ["hr_leader", "it_leader"],
        "channels": ["website", "events"],
        "cells": {"hr_leader": {"website": 20, "events": 2}, "it_leader": {"website": 5}},
    },
    "eval_summary": {
        "n_artifacts": 11,
        "n_classifications": 3,
        "n_proof_gaps": 1,
        "n_opportunities": 1,
        "n_change_events": 0,
    },
}


def test_dashboard_is_self_contained_html():
    out = build_dashboard(_PKG)
    assert "<style>" in out and "<h1>" in out
    # Self-containment = no LOADED external resources. Hyperlinks (<a href> to a
    # LinkedIn post) are fine — they're clicked, not fetched at render time.
    assert "<script" not in out and " src=" not in out
    assert "stylesheet" not in out and "@import" not in out


def test_dashboard_showcases_linkedin_posts_and_similarweb():
    out = build_dashboard(_PKG)
    assert "LinkedIn employee posts" in out
    assert "Dana Lee" in out and "consolidation" in out
    assert (
        'href="https://www.linkedin.com/posts/dana_x"' in out or "linkedin.com/posts/dana_x" in out
    )
    assert "Traffic" in out and "5000000" in out


def test_dashboard_renders_all_chart_sections():
    out = build_dashboard(_PKG)
    for section in (
        "Source mix",
        "message themes",
        "Competitive stance",
        "Message–proof gaps",
        "Persona × channel",
        "Strategy over time",
        "attack / defend",
        "content marketing",
        "category-entry-point ownership",
    ):
        assert section in out
    # Data actually rendered.
    assert "Deel" in out and "Rippling" in out
    assert "consolidation" in out  # top theme
    assert "compliance" in out  # gap label


def test_role_graphs_render_their_data():
    out = build_dashboard(_PKG)
    assert "ATTACK" in out and "BUILD PROOF" in out  # PM matrix quadrants
    assert "cost_reduction" in out or "cost reduction" in out  # timeline emergence theme
    assert "consolidating hr tools" in out  # SEO CEP label
    assert "point_solution_sprawl" in out  # content narrative hook
    # IC vs exec framing present
    assert "IC:" in out and "Exec:" in out


def test_dashboard_handles_empty_package():
    minimal = {
        "run": {},
        "scope": {},
        "companies": [],
        "classifications": [],
        "proof_gaps": [],
        "source_distribution": {},
        "persona_channel_matrix": {},
        "eval_summary": {},
    }
    out = build_dashboard(minimal)  # must not raise
    assert "<h1>" in out
