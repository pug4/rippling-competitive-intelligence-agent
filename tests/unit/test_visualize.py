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
    # estimated_monthly_visits is human-formatted now (5,000,000 not 5000000).
    assert "Traffic" in out and "5,000,000" in out


def test_dashboard_action_board_and_no_dict_repr():
    pkg = dict(_PKG)
    pkg["opportunities"] = [
        {
            "opportunity_id": "OPP-1",
            "title": "Own the audit-trail story",
            "deliverable_type": "comparison_page",
            "structural_defensibility": "medium",
            "message_angle": "show the receipts",
            "experiment_hypothesis": "audit-ready messaging lifts demo requests",
            "primary_metric": "demo_requests",
            "guardrail_metrics": ["brand_search"],
            "minimum_sample_rule": "n>=200 sessions",
            "kill_rule": "kill if CTR < 0.5% after 2 weeks",
            "why_this_could_backfire": "counter-proof risk",
            "staged_plan": [
                {
                    "stage": "build_proof",
                    "objective": "collect quantified outcomes",
                    "proceed_if": ["2 customers agree"],
                    "stop_or_reframe_if": ["no customer consents"],
                }
            ],
        }
    ]
    pkg["similarweb"] = {
        "domain": "deel.com",
        "data_source": "similarweb",
        "estimated": True,
        "metrics": {
            "digital_competitors": {
                "value": [{"domain": "rippling.com", "affinity": 0.88}],
                "estimated": True,
            }
        },
    }
    out = build_dashboard(pkg)
    # Exec surface: the Action Board with the full experiment apparatus.
    assert "Action Board" in out and "Own the audit-trail story" in out
    assert "Kill rule" in out and "demo_requests" in out
    assert "proceed if" in out and "stop/reframe if" in out
    # Arrays of dicts render as domain (affinity ...), never a dict repr.
    assert "{'domain'" not in out and "[object Object]" not in out
    assert "rippling.com (affinity 0.88)" in out
    assert "affinity index" in out


def test_dashboard_banner_only_when_package_says_so():
    pkg = dict(_PKG)
    pkg["corpus_normalization"] = {
        "competitor": {"name": "NicheCo", "n_classified": 12},
        "focal": {"name": "Rippling", "n_classified": 110},
        "asymmetry_ratio": 9.17,
        "show_banner": True,
        "normalization_note": "counts shown as share-of-corpus",
    }
    out = build_dashboard(pkg)
    assert "Corpus-size asymmetry" in out and "9.17" in out
    pkg["corpus_normalization"] = {"show_banner": False}
    assert "Corpus-size asymmetry" not in build_dashboard(pkg)


def test_dashboard_timeline_uses_reconciled_prior_counts():
    pkg = dict(_PKG)
    pkg["change_events"] = [
        {
            **_PKG["change_events"][0],
            "lifecycle": "expanding",
            "prior_theme_count": 4,
            "prior_window_n": 14,
        }
    ]
    out = build_dashboard(pkg)
    assert "prior: 4 of 14" in out
    assert "prior: not observed</div>" not in out


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
