"""Focal-claims verification gate: the guard that would have caught the
false-premise recommendations ("Rippling has no goals product",
"Rippling names Deel on 0 pages") by checking the FOCAL corpus.

Pure/deterministic — no network, no model; the optional live_check is injected.
"""

from __future__ import annotations

from typing import Any

from competitive_agent.focal_gate import verify_focal_claims


def _focal_corpus_with_goals() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    classifications = [
        {
            "artifact_id": "ART-goals",
            "company_id": "rippling",
            "products": ["Goals"],
            "primary_theme": "performance management",
            "proof_types": ["product_demonstration"],
        }
    ]
    artifacts = [
        {
            "artifact_id": "ART-goals",
            "company_id": "rippling",
            "url": "https://www.rippling.com/goals",
            "title": "Rippling Goals",
        }
    ]
    return classifications, artifacts


# ---- contradicted: the focal corpus shows Rippling HAS X ---------------------


def test_goals_opportunity_contradicted_by_focal_corpus() -> None:
    classifications, artifacts = _focal_corpus_with_goals()
    pkg = {
        "focal_company": "Rippling",
        "opportunities": [
            {
                "opportunity_id": "OPP-1",
                "title": "Attack the missing goals story",
                "why_this_could_backfire": (
                    "Rippling has no goals product, so this angle is safe."
                ),
            }
        ],
    }
    results = verify_focal_claims(pkg, classifications, artifacts)
    assert len(results) == 1
    verdict = results[0]
    assert verdict["source"] == "opportunity"
    assert verdict["id"] == "OPP-1"
    assert verdict["verdict"] == "contradicted"
    assert "goals" in verdict["x_phrase"].lower()
    assert verdict["focal_evidence"]  # non-empty
    for ev in verdict["focal_evidence"]:
        assert set(ev) == {"url", "why"}
    # At least one evidence row cites the real focal Goals page.
    assert any("rippling.com/goals" in ev["url"] for ev in verdict["focal_evidence"])


def test_deel_comparison_proof_gap_contradicted_by_focal_url() -> None:
    classifications = [
        {"artifact_id": "ART-cmp", "company_id": "rippling", "primary_theme": "comparison"}
    ]
    artifacts = [
        {
            "artifact_id": "ART-cmp",
            "company_id": "rippling",
            "url": "https://www.rippling.com/compare/rippling-vs-deel",
            "title": "Rippling vs Deel",
        }
    ]
    pkg = {
        "focal_company": "Rippling",
        "proof_gaps": [
            {
                "claim_id": "CLM-9",
                "actionable_interpretation": (
                    "Rippling names Deel on 0 pages, so we can own the comparison."
                ),
            }
        ],
    }
    results = verify_focal_claims(pkg, classifications, artifacts)
    assert len(results) == 1
    verdict = results[0]
    assert verdict["source"] == "proof_gap"
    assert verdict["id"] == "CLM-9"
    assert verdict["verdict"] == "contradicted"
    assert verdict["x_phrase"].lower() == "deel"
    assert any("rippling-vs-deel" in ev["url"] for ev in verdict["focal_evidence"])


# ---- confirmed_absent: substantive corpus, X genuinely not present -----------


def test_genuinely_absent_claim_confirmed_absent() -> None:
    classifications, artifacts = _focal_corpus_with_goals()
    pkg = {
        "focal_company": "Rippling",
        "opportunities": [
            {
                "opportunity_id": "OPP-2",
                "why_this_could_backfire": "Rippling has no SOC 2 attestation to show.",
            }
        ],
    }
    results = verify_focal_claims(pkg, classifications, artifacts)
    assert len(results) == 1
    assert results[0]["verdict"] == "confirmed_absent"
    assert results[0]["focal_evidence"] == []


# ---- no-lack / competitor-lack claims are ignored ----------------------------


def test_no_lack_and_competitor_lack_claims_ignored() -> None:
    classifications, artifacts = _focal_corpus_with_goals()
    pkg = {
        "focal_company": "Rippling",
        "opportunities": [
            {
                "opportunity_id": "OPP-3",
                "message_angle": "Rippling should lead with named customer proof.",
                "title": "Proof-forward campaign",
            },
            {
                # A COMPETITOR lack (not the focal) must not fire the focal gate.
                "opportunity_id": "OPP-4",
                "competitor_proof_gap": "The competitor has no named customer stories.",
            },
        ],
    }
    results = verify_focal_claims(pkg, classifications, artifacts)
    assert results == []


# ---- injected live_check path (corpus silent) --------------------------------


def test_live_check_found_marks_contradicted() -> None:
    classifications, artifacts = _focal_corpus_with_goals()  # no 'deel' anywhere
    pkg = {
        "focal_company": "Rippling",
        "focal_domain": "rippling.com",
        "opportunities": [
            {
                "opportunity_id": "OPP-5",
                "why_this_could_backfire": "Rippling lacks a Deel comparison page.",
            }
        ],
    }

    calls: list[tuple[str, str]] = []

    def live_check(domain: str, phrase: str) -> bool:
        calls.append((domain, phrase))
        return "deel" in phrase.lower()

    results = verify_focal_claims(pkg, classifications, artifacts, live_check=live_check)
    assert len(results) == 1
    assert results[0]["verdict"] == "contradicted"
    assert results[0]["focal_evidence"][0]["url"] == "rippling.com"
    # The live check was actually consulted, with the focal domain + X phrase.
    assert calls and calls[0][0] == "rippling.com"
    assert "deel" in calls[0][1].lower()


def test_live_check_not_found_marks_confirmed_absent() -> None:
    classifications, artifacts = _focal_corpus_with_goals()
    pkg = {
        "focal_company": "Rippling",
        "focal_domain": "rippling.com",
        "opportunities": [
            {
                "opportunity_id": "OPP-6",
                "why_this_could_backfire": "Rippling lacks a Workday comparison page.",
            }
        ],
    }
    results = verify_focal_claims(
        pkg, classifications, artifacts, live_check=lambda domain, phrase: False
    )
    assert len(results) == 1
    assert results[0]["verdict"] == "confirmed_absent"
    assert results[0]["focal_evidence"] == []


# ---- unverified: empty corpus, no live check ---------------------------------


def test_empty_corpus_without_live_check_is_unverified() -> None:
    pkg = {
        "focal_company": "Rippling",
        "opportunities": [
            {
                "opportunity_id": "OPP-7",
                "why_this_could_backfire": "Rippling has no analytics dashboard product.",
            }
        ],
    }
    results = verify_focal_claims(pkg, [], [])
    assert len(results) == 1
    assert results[0]["verdict"] == "unverified"
    assert results[0]["focal_evidence"] == []
