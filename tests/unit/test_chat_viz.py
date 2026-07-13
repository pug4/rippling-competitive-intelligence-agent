"""Unit tests for the on-demand chat visualization builders.

Every builder is a PURE function over a crafted package: the tests hand-tally
the expected counts/shares and assert the rendered spec matches EXACTLY. The
honesty contract is tested too — unknown chart types, non-whitelisted group_by
fields, and empty data return a typed error and NEVER a fabricated row.
"""

from __future__ import annotations

from competitive_agent.chat_viz import (
    GROUP_BY_FIELDS,
    SUPPORTED_CHART_TYPES,
    VIZ_BUILDERS,
    build_visualization,
)

# --------------------------------------------------------------------------- #
# registry / whitelist                                                         #
# --------------------------------------------------------------------------- #


def test_registry_advertises_all_required_chart_types():
    required = {
        "theme_distribution",
        "cep_ownership",
        "proof_gaps",
        "temporal_changes",
        "persona_channel",
        "product_verticals",
        "ad_creatives",
        "group_by",
    }
    assert required <= set(VIZ_BUILDERS)
    assert set(SUPPORTED_CHART_TYPES) == set(VIZ_BUILDERS)


# --------------------------------------------------------------------------- #
# theme_distribution — counts + shares match a hand tally                      #
# --------------------------------------------------------------------------- #


def test_theme_distribution_counts_match_hand_tally_and_scopes_to_competitor():
    pkg = {
        "companies": [
            {"canonical_name": "Deel", "company_id": "CO-1"},
            {"canonical_name": "Rippling", "company_id": "CO-2"},
        ],
        "artifacts": [{"artifact_id": "ART-1", "url": "https://deel.com/a"}],
        "classifications": [
            {"company_id": "CO-1", "artifact_id": "ART-1", "primary_theme": "consolidation"},
            {"company_id": "CO-1", "artifact_id": "ART-1", "primary_theme": "consolidation"},
            {"company_id": "CO-1", "artifact_id": "ART-1", "primary_theme": "consolidation"},
            {"company_id": "CO-1", "artifact_id": "ART-1", "primary_theme": "automation"},
            {"company_id": "CO-1", "artifact_id": "ART-1", "primary_theme": "automation"},
            {"company_id": "CO-1", "artifact_id": "ART-1", "primary_theme": "compliance"},
            # A focal (Rippling) classification must NOT be counted.
            {"company_id": "CO-2", "artifact_id": "ART-1", "primary_theme": "consolidation"},
        ],
    }
    spec = build_visualization(pkg, "theme_distribution", {})
    assert spec["type"] == "bar"
    # Sorted by count descending (most_common).
    assert [d["label"] for d in spec["data"]] == ["consolidation", "automation", "compliance"]
    by_label = {d["label"]: d for d in spec["data"]}
    assert by_label["consolidation"]["value"] == 3 and by_label["consolidation"]["share"] == 0.5
    assert by_label["automation"]["value"] == 2 and by_label["automation"]["share"] == 0.3333
    assert by_label["compliance"]["value"] == 1 and by_label["compliance"]["share"] == 0.1667
    # Total (6) excludes the focal classification — proves competitor scoping.
    assert "6 classified pages" in spec["caption"]
    assert spec["citations"] == [{"artifact_id": "ART-1", "url": "https://deel.com/a"}]


# --------------------------------------------------------------------------- #
# cep_ownership — shares match                                                 #
# --------------------------------------------------------------------------- #


def test_cep_ownership_shares_match():
    pkg = {
        "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
        "category_entry_points": [
            {
                "cep": "opening_new_country",
                "competitor_share": 0.6423,
                "focal_share": 0.1404,
                "competitor_pages": 79,
                "focal_pages": 16,
                "ownership": "competitor_advantage",
            },
            {
                "cep": "consolidating_hr_tools",
                "competitor_share": 0.4878,
                "focal_share": 0.7105,
                "competitor_pages": 60,
                "focal_pages": 81,
                "ownership": "contested",
            },
        ],
    }
    spec = build_visualization(pkg, "cep_ownership", {})
    assert spec["type"] == "grouped_bar"
    assert spec["data"]["groups"] == ["opening new country", "consolidating hr tools"]
    series = {s["name"]: s["values"] for s in spec["data"]["series"]}
    assert series["Deel"] == [0.6423, 0.4878]
    assert series["Rippling"] == [0.1404, 0.7105]


def test_cep_ownership_counts_mode_and_missing_focal_mirror():
    # No shares -> counts mode; a None focal_pages is shown as 0 AND disclosed
    # (never a fabricated real share).
    pkg = {
        "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
        "category_entry_points": [
            {"cep": "reducing_payroll_errors", "competitor_pages": 8, "focal_pages": None},
        ],
    }
    spec = build_visualization(pkg, "cep_ownership", {})
    series = {s["name"]: s["values"] for s in spec["data"]["series"]}
    assert series["Deel"] == [8]  # raw page count (counts mode)
    assert series["Rippling"] == [0]  # no mirror -> 0
    assert "no Rippling mirror" in spec["caption"]


# --------------------------------------------------------------------------- #
# proof_gaps — table with both sides                                          #
# --------------------------------------------------------------------------- #


def test_proof_gaps_table_lists_both_sides():
    pkg = {
        "companies": [{"canonical_name": "Deel"}, {"canonical_name": "Rippling"}],
        "proof_gaps": [
            {
                "short_label": "native breadth",
                "attackability": "medium",
                "proof_strength": "weak",
                "focal_proof_strength": "strong",
                "missing_proof": ["quantified_customer_outcome", "independent_validation"],
            }
        ],
    }
    spec = build_visualization(pkg, "proof_gaps", {})
    assert spec["type"] == "table"
    assert spec["data"]["columns"] == [
        "Their claim",
        "Attackability",
        "Deel proof",
        "Rippling proof",
        "Missing proof",
    ]
    assert spec["data"]["rows"][0] == [
        "native breadth",
        "medium",
        "weak",
        "strong",
        "quantified_customer_outcome, independent_validation",
    ]


# --------------------------------------------------------------------------- #
# temporal_changes — table with confidence                                    #
# --------------------------------------------------------------------------- #


def test_temporal_changes_table_prior_current_confidence():
    pkg = {
        "companies": [{"canonical_name": "Deel"}],
        "artifacts": [{"artifact_id": "ART-9", "url": "https://deel.com/x"}],
        "change_events": [
            {
                "dimension": "theme_emergence",
                "prior_state": "not observed",
                "current_state": "present in 30 current-window artifacts",
                "confidence": "low",
                "lifecycle": "emerging",
                "current_evidence_ids": ["ART-9"],
            }
        ],
    }
    spec = build_visualization(pkg, "temporal_changes", {})
    assert spec["data"]["columns"] == [
        "Dimension",
        "Prior state",
        "Current state",
        "Confidence",
        "Lifecycle",
    ]
    assert spec["data"]["rows"][0] == [
        "theme emergence",
        "not observed",
        "present in 30 current-window artifacts",
        "low",
        "emerging",
    ]
    assert spec["citations"] == [{"artifact_id": "ART-9", "url": "https://deel.com/x"}]


# --------------------------------------------------------------------------- #
# persona_channel — heatmap                                                    #
# --------------------------------------------------------------------------- #


def test_persona_channel_heatmap_shape_and_cells():
    pkg = {
        "companies": [{"canonical_name": "Deel"}],
        "persona_channel_matrix": {
            "personas": ["hr_leader"],
            "channels": ["website", "events"],
            "cells": {"hr_leader": {"website": 5, "events": 2}},
        },
    }
    spec = build_visualization(pkg, "persona_channel", {})
    assert spec["type"] == "heatmap"
    assert spec["data"] == {
        "rows": ["hr_leader"],
        "cols": ["website", "events"],
        "cells": {"hr_leader": {"website": 5, "events": 2}},
    }
    assert "7 page-tags" in spec["caption"]  # 5 + 2, computed not invented


# --------------------------------------------------------------------------- #
# product_verticals — bar (pages per vertical)                                #
# --------------------------------------------------------------------------- #


def test_product_verticals_bar_pages_and_share():
    pkg = {
        "companies": [{"canonical_name": "Deel"}],
        "product_vertical_analysis": {
            "verticals": [
                {"vertical": "payroll", "n_artifacts": 68},
                {"vertical": "hris_core_hr", "n_artifacts": 32},
            ]
        },
    }
    spec = build_visualization(pkg, "product_verticals", {})
    by_label = {d["label"]: d for d in spec["data"]}
    assert by_label["payroll"]["value"] == 68 and by_label["payroll"]["share"] == 0.68
    assert by_label["hris core hr"]["value"] == 32 and by_label["hris core hr"]["share"] == 0.32


# --------------------------------------------------------------------------- #
# ad_creatives — real Google ad creative row                                  #
# --------------------------------------------------------------------------- #


def test_ad_creatives_lists_crafted_google_ads_artifact_with_video_headline():
    pkg = {
        "companies": [{"canonical_name": "Example HR"}],
        "artifacts": [
            {
                "artifact_id": "ART-AD1",
                "source_type": "google_ads",
                "url": "https://adstransparency.google.com/creative/CR22",
                "author": None,
                "metadata": {
                    "advertiser": "Example HR",
                    "format": "video_ad",
                    "headline": "Pay global contractors in 150+ countries",
                    "cta": "Start free",
                    "first_observed": "2026-05-11",
                    "last_observed": "2026-07-01",
                    "landing_url": "https://www.example-hr.com/contractors",
                },
            },
            # A non-ad artifact must be excluded from the table.
            {"artifact_id": "ART-WEB", "source_type": "webpage", "url": "https://example.com"},
        ],
    }
    spec = build_visualization(pkg, "ad_creatives", {})
    assert spec["type"] == "table"
    assert spec["data"]["columns"] == [
        "Advertiser",
        "Format",
        "Headline",
        "CTA",
        "Run dates",
        "Landing URL",
    ]
    assert len(spec["data"]["rows"]) == 1  # only the google_ads artifact
    assert spec["data"]["rows"][0] == [
        "Example HR",
        "video ad",
        "Pay global contractors in 150+ countries",  # the video-ad headline
        "Start free",
        "2026-05-11 → 2026-07-01",
        "https://www.example-hr.com/contractors",
    ]
    assert spec["citations"] == [
        {"artifact_id": "ART-AD1", "url": "https://adstransparency.google.com/creative/CR22"}
    ]


# --------------------------------------------------------------------------- #
# group_by — whitelist + counting                                             #
# --------------------------------------------------------------------------- #


def test_group_by_counts_list_valued_field():
    pkg = {
        "companies": [{"canonical_name": "Deel"}],
        "classifications": [
            {"personas": ["hr_leader", "finance_leader"]},
            {"personas": ["hr_leader"]},
            {"personas": ["it_leader"]},
        ],
    }
    spec = build_visualization(pkg, "group_by", {"field": "personas"})
    assert spec["type"] == "bar"
    by_label = {d["label"]: d for d in spec["data"]}
    assert by_label["hr leader"]["value"] == 2
    assert by_label["finance leader"]["value"] == 1
    assert by_label["it leader"]["value"] == 1
    # 4 total tags -> hr_leader share is 2/4.
    assert by_label["hr leader"]["share"] == 0.5


def test_group_by_rejects_non_whitelisted_field():
    pkg = {
        "companies": [{"canonical_name": "Deel"}],
        "classifications": [{"villain_normalized": ["spreadsheets"]}],
    }
    err = build_visualization(pkg, "group_by", {"field": "villain_normalized"})
    assert err["error_type"] == "invalid_group_by_field"
    assert "villain_normalized" in err["error"]
    # Honest error — never a fabricated bar.
    assert "data" not in err
    # The field is genuinely not in the whitelist.
    assert "villain_normalized" not in GROUP_BY_FIELDS


# --------------------------------------------------------------------------- #
# honesty: unknown chart type / empty data never fabricate                    #
# --------------------------------------------------------------------------- #


def test_unknown_chart_type_returns_typed_error():
    err = build_visualization({"companies": [{"canonical_name": "Deel"}]}, "pie_of_everything", {})
    assert err["error_type"] == "unknown_chart_type"
    assert "pie_of_everything" in err["error"]
    assert "data" not in err


def test_empty_data_returns_honest_error_not_a_fake_row():
    # theme_distribution with no themed classifications
    err = build_visualization(
        {"companies": [{"canonical_name": "Deel"}], "classifications": []},
        "theme_distribution",
        {},
    )
    assert err["error_type"] == "empty_data"
    assert "data" not in err

    # ad_creatives with no ad-library artifacts
    err2 = build_visualization(
        {"companies": [{"canonical_name": "Deel"}], "artifacts": [{"source_type": "webpage"}]},
        "ad_creatives",
        {},
    )
    assert err2["error_type"] == "empty_data"
    assert "rows" not in err2 and "data" not in err2

    # cep_ownership with no category entry points
    err3 = build_visualization({"companies": [{"canonical_name": "Deel"}]}, "cep_ownership", {})
    assert err3["error_type"] == "empty_data"
    assert "data" not in err3
