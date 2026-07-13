"""PRODUCT FOCUS lens tests (user story: "I put in Vanta; scope the analysis
to the ONE product it competes with — compliance — not whole-company noise").

No network anywhere: model calls run in fixture mode with zero API keys, the
focal mirror is a crafted data.json on disk, and every degrade path is pinned
to stay typed and honest (mirror missing -> focal side None/UNKNOWN, never a
fabricated zero; a quote absent from the supplied evidence -> flagged
unverified, never silently kept as fact).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    # Present-but-EMPTY (never delenv): a deleted variable would fall back to
    # the .env-backed Settings field carrying the developer's REAL keys.
    monkeypatch.setenv("SEMRUSH_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


_RUN_ID = "RUN-pftest"
_MIRROR_RUN_ID = "RUN-pfmirror"


def _competitor_pkg(mirror_run_id: str = _MIRROR_RUN_ID) -> dict[str, Any]:
    """Vanta-vs-Rippling shaped package: compliance dominates the competitor's
    mapped corpus (5 of 7 pages); payroll is the noise vertical whose pages
    must NEVER leak into a compliance-scoped block."""
    return {
        "run": {"run_id": _RUN_ID},
        "companies": [
            {"company_id": "CO-vanta", "canonical_name": "Vanta", "primary_domain": "vanta.com"},
            {
                "company_id": "CO-rippling",
                "canonical_name": "Rippling",
                "primary_domain": "rippling.com",
            },
        ],
        "focal_evidence": {"run_id": mirror_run_id},
        "product_vertical_analysis": {
            "verticals": [
                {
                    "vertical": "compliance_legal",
                    "n_artifacts": 5,
                    "n_linkedin_posts": 0,
                    "top_themes": ["compliance_automation"],
                    "theme_counts": {"compliance_automation": 4, "trust": 1},
                    "stance_mix": {"category_attack": 5},
                    "personas": ["security_leader"],
                    "example_urls": ["https://vanta.com/soc2"],
                    "sample_message": None,
                },
                {
                    "vertical": "payroll",
                    "n_artifacts": 2,
                    "n_linkedin_posts": 0,
                    "top_themes": ["payroll_speed"],
                    "theme_counts": {"payroll_speed": 2},
                    "stance_mix": {"capability_claim": 2},
                    "personas": ["finance_leader"],
                    "example_urls": ["https://vanta.com/payroll"],
                    "sample_message": None,
                },
            ],
            "by_artifact": {
                "ART-c1": ["compliance_legal"],
                "ART-c2": ["compliance_legal"],
                "ART-c3": ["compliance_legal"],
                "ART-c4": ["compliance_legal"],
                "ART-c5": ["compliance_legal"],
                "ART-p1": ["payroll"],
                "ART-p2": ["payroll"],
            },
            "method": "test mapping",
        },
        "classifications": [
            {
                "artifact_id": "ART-c1",
                "company_id": "CO-vanta",
                "primary_message": (
                    "Automate SOC 2 compliance with the all-in-one workforce platform"
                ),
                "message_salience": 0.9,
                "category_entry_points": ["preparing_for_soc2_audit"],
                "proof_types": ["customer_logo"],
                "personas": ["security_leader"],
            },
            {
                "artifact_id": "ART-c2",
                "company_id": "CO-vanta",
                "primary_message": "Continuous compliance monitoring, always on",
                "message_salience": 0.7,
                "category_entry_points": ["preparing_for_soc2_audit"],
                "proof_types": ["feature_assertion"],
                "personas": [],
            },
            # OUT-OF-VERTICAL artifact: its message, CEP, and proof type must
            # never appear in a compliance-scoped block.
            {
                "artifact_id": "ART-p1",
                "company_id": "CO-vanta",
                "primary_message": "OUTSIDER payroll message never in compliance",
                "message_salience": 0.99,
                "category_entry_points": ["running_global_payroll"],
                "proof_types": ["pricing_transparency"],
                "personas": ["finance_leader"],
            },
        ],
        "artifacts": [
            {"artifact_id": "ART-c1", "url": "https://vanta.com/soc2"},
            {"artifact_id": "ART-c2", "url": "https://vanta.com/monitoring"},
            {"artifact_id": "ART-p1", "url": "https://vanta.com/payroll"},
        ],
        "category_entry_points": [
            {
                "cep": "preparing_for_soc2_audit",
                "ownership": "contested",
                "ownership_basis": "both sides >=2 pages, share ratio < 2x",
                "competitor_pages": 5,
                "focal_pages": 3,
            },
            # Observed ONLY on payroll pages — must not enter the compliance block.
            {
                "cep": "running_global_payroll",
                "ownership": "focal_owns",
                "ownership_basis": "share ratio >= 2x",
                "competitor_pages": 2,
                "focal_pages": 9,
            },
        ],
    }


def _mirror_pkg() -> dict[str, Any]:
    return {
        "run": {"run_id": _MIRROR_RUN_ID},
        "companies": [
            {
                "company_id": "CO-rippling",
                "canonical_name": "Rippling",
                "primary_domain": "rippling.com",
            }
        ],
        "product_vertical_analysis": {
            "verticals": [
                {
                    "vertical": "compliance_legal",
                    "n_artifacts": 3,
                    "n_linkedin_posts": 0,
                    "top_themes": ["compliance_automation"],
                    "theme_counts": {"compliance_automation": 3},
                    "stance_mix": {"capability_claim": 3},
                    "personas": ["hr_leader"],
                    "example_urls": ["https://rippling.com/compliance"],
                    "sample_message": None,
                }
            ],
            "by_artifact": {
                "ART-f1": ["compliance_legal"],
                "ART-f2": ["compliance_legal"],
                "ART-f3": ["compliance_legal"],
                "ART-f4": ["hris_core_hr"],
            },
            "method": "test mapping",
        },
        "classifications": [
            {
                "artifact_id": "ART-f1",
                "company_id": "CO-rippling",
                "primary_message": "Compliance built into your HR system of record",
                "message_salience": 0.8,
                "category_entry_points": ["preparing_for_soc2_audit"],
                "proof_types": ["named_customer_story"],
                "personas": ["hr_leader"],
            },
            {
                "artifact_id": "ART-f4",
                "company_id": "CO-rippling",
                "primary_message": "FOCAL OUTSIDER hris message never in compliance",
                "message_salience": 0.95,
                "category_entry_points": ["consolidating_hr_tools"],
                "proof_types": ["customer_quotation"],
                "personas": [],
            },
        ],
        "artifacts": [
            {"artifact_id": "ART-f1", "url": "https://rippling.com/compliance"},
            {"artifact_id": "ART-f4", "url": "https://rippling.com/hris"},
        ],
        "product_positioning": [
            {
                "product": "Rippling Compliance",
                "pages": 3,
                "themes": ["compliance_automation"],
                "personas": ["hr_leader"],
                "proof_types": ["named_customer_story"],
                "category_entry_points": ["preparing_for_soc2_audit"],
            },
            # Themes do NOT intersect the compliance vertical's top themes —
            # this product must not be claimed for the category.
            {
                "product": "Rippling Payroll",
                "pages": 5,
                "themes": ["payroll_speed"],
                "personas": ["finance_leader"],
                "proof_types": ["feature_assertion"],
                "category_entry_points": ["running_global_payroll"],
            },
        ],
    }


def _write_pkg(tmp_path: Path, run_id: str, pkg: dict[str, Any]) -> None:
    run_dir = tmp_path / "outputs" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "data.json").write_text(json.dumps(pkg), encoding="utf-8")


def _write_both(tmp_path: Path) -> dict[str, Any]:
    pkg = _competitor_pkg()
    _write_pkg(tmp_path, _RUN_ID, pkg)
    _write_pkg(tmp_path, _MIRROR_RUN_ID, _mirror_pkg())
    return pkg


# ---------------------------------------------------------------------------
# candidate resolution
# ---------------------------------------------------------------------------


def test_candidates_rank_by_competitor_pages_and_carry_focal_side(isolated_env: Path) -> None:
    from competitive_agent.product_focus import resolve_focus_candidates

    pkg = _write_both(isolated_env)
    candidates = resolve_focus_candidates(pkg)
    assert [c["vertical"] for c in candidates] == ["compliance_legal", "payroll"]

    top = candidates[0]  # the "Vanta -> compliance_legal" auto-resolution
    assert top["competitor_pages"] == 5
    assert top["competitor_share"] == round(5 / 7, 4)  # of 7 mapped artifacts
    assert top["focal_pages"] == 3  # from the MIRROR's own vertical row
    # Product names from the mirror's product_positioning, theme-intersected:
    # the payroll product never gets claimed for the compliance category.
    assert top["focal_products"] == ["Rippling Compliance"]

    payroll = candidates[1]
    assert payroll["competitor_pages"] == 2
    # Mirror rendered but maps no payroll pages: an observed 0, not None.
    assert payroll["focal_pages"] == 0
    assert payroll["focal_products"] == []


# ---------------------------------------------------------------------------
# vertical filtering (strict by_artifact scoping)
# ---------------------------------------------------------------------------


def test_vertical_filtering_uses_by_artifact_strictly(isolated_env: Path) -> None:
    from competitive_agent.product_focus import build_focus_inputs

    pkg = _write_both(isolated_env)
    inputs = build_focus_inputs(_RUN_ID, pkg, "compliance_legal")

    comp = inputs["competitor_in_category"]
    assert "5 page(s) mapped to this category" in comp
    assert "compliance_automation: 4 page(s) (80%)" in comp  # counts + shares
    assert "category_attack: 5" in comp  # stance mix
    assert "security_leader" in comp  # personas
    # Verbatim messages carry their source URLs, highest salience first.
    assert (
        '"Automate SOC 2 compliance with the all-in-one workforce platform" '
        "(source: https://vanta.com/soc2)" in comp
    )
    assert "Continuous compliance monitoring, always on" in comp
    # STRICT scoping: the out-of-vertical artifact never leaks in — not its
    # message (despite the highest salience), not its CEP, not its proof type.
    assert "OUTSIDER payroll message" not in comp
    assert "running_global_payroll" not in comp
    assert "preparing_for_soc2_audit" in comp

    ceps = inputs["category_entry_points_in_category"]
    assert "preparing_for_soc2_audit: ownership=contested" in ceps
    assert "running_global_payroll" not in ceps  # payroll-only trigger excluded

    focal = inputs["focal_in_category"]
    assert "3 page(s) mapped to this category" in focal
    assert "Rippling Compliance" in focal and "Rippling Payroll" not in focal
    assert "Compliance built into your HR system of record" in focal
    assert "FOCAL OUTSIDER hris message" not in focal  # mirror scoping is strict too

    proof = inputs["proof_comparison"]
    assert "customer_logo" in proof and "named_customer_story" in proof
    assert "pricing_transparency" not in proof  # payroll-only proof excluded
    assert "customer_quotation" not in proof  # focal out-of-vertical proof excluded

    note = inputs["corpus_note"]
    assert "5 of 7" in note and "3 of 4" in note
    assert "not directly comparable" in note  # share-normalization caveat


# ---------------------------------------------------------------------------
# focal mirror missing -> honest degrade
# ---------------------------------------------------------------------------


def test_focal_mirror_missing_degrades_honestly(isolated_env: Path) -> None:
    from competitive_agent.product_focus import (
        build_focus_inputs,
        generate_product_focus,
        resolve_focus_candidates,
    )

    pkg = _competitor_pkg(mirror_run_id="RUN-never-rendered")
    _write_pkg(isolated_env, _RUN_ID, pkg)

    # Candidates: focal side None (unknown), never a fabricated zero.
    for candidate in resolve_focus_candidates(pkg):
        assert candidate["focal_pages"] is None
        assert candidate["focal_products"] == []

    inputs = build_focus_inputs(_RUN_ID, pkg, "compliance_legal")
    assert "not rendered" in inputs["focal_in_category"]
    assert "UNKNOWN, not zero" in inputs["focal_in_category"]
    assert "mirror not rendered" in inputs["focal_products"]
    assert "unknown" in inputs["corpus_note"]
    assert "absence of the mirror, not absence of proof" in inputs["proof_comparison"]

    res = asyncio.run(generate_product_focus(_RUN_ID, execution_mode="fixture"))
    assert res["focal_stats"] is None
    assert "not rendered" in res["method_note"]


# ---------------------------------------------------------------------------
# quote guard + envelope (fixture mode, keyless)
# ---------------------------------------------------------------------------


def test_quote_guard_flags_fabricated_quotes_and_envelope_shape(isolated_env: Path) -> None:
    from competitive_agent.product_focus import generate_product_focus

    _write_both(isolated_env)
    res = asyncio.run(generate_product_focus(_RUN_ID, execution_mode="fixture"))

    assert res["run_id"] == _RUN_ID
    assert res["vertical"] == "compliance_legal"  # auto-resolved top candidate
    assert res["resolved_automatically"] is True
    assert res["candidates"][0]["vertical"] == "compliance_legal"
    assert res["competitor_stats"]["n_pages"] == 5
    assert res["focal_stats"]["n_pages"] == 3
    assert res["prompt_version"] == "1.0.0"
    assert res["model_id"] == "fixture"
    assert res["generated_at"]
    assert "not a whole-company comparison" in res["method_note"]

    report = res["report"]
    # Verified quote: verbatim from the supplied competitor evidence.
    narrative = report["category_narrative"]
    assert narrative["quote_verified"] is True
    assert "treat as unverified" not in narrative["basis"]

    by_title = {o["title"]: o for o in report["detailed_opportunities"]}
    verified = by_title["Category proof page"]
    assert verified["quote_verified"] is True
    assert "treat as unverified" not in verified["basis"]

    # Fabricated quote: flagged, never silently kept as fact.
    fabricated = by_title["Buying-trigger comparison asset"]
    assert fabricated["quote_verified"] is False
    assert fabricated["basis"].startswith(
        "treat as unverified — quote not found in the observed evidence"
    )

    # A null quote is honest absence — not verified, but not flagged either.
    inferred = by_title["Persona-specific nurture"]
    assert inferred["quote_verified"] is False
    assert "treat as unverified" not in inferred["basis"]

    # Cached on disk under the vertical-specific name.
    cache = isolated_env / "outputs" / "runs" / _RUN_ID / "product_focus_compliance_legal.json"
    assert cache.exists()


def test_unknown_vertical_raises_value_error_listing_candidates(isolated_env: Path) -> None:
    from competitive_agent.product_focus import generate_product_focus

    _write_both(isolated_env)
    with pytest.raises(ValueError, match=r"unknown vertical 'sales_crm'") as excinfo:
        asyncio.run(generate_product_focus(_RUN_ID, vertical="sales_crm", execution_mode="fixture"))
    assert "compliance_legal" in str(excinfo.value)
    assert "payroll" in str(excinfo.value)


def test_cache_hit_returns_without_regeneration(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from competitive_agent import model_gateway as gateway_module
    from competitive_agent.product_focus import generate_product_focus

    _write_both(isolated_env)
    calls = {"n": 0}
    real_build = gateway_module.build_gateway

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(gateway_module, "build_gateway", counting_build)

    first = asyncio.run(generate_product_focus(_RUN_ID, execution_mode="fixture"))
    assert calls["n"] == 1
    again = asyncio.run(generate_product_focus(_RUN_ID, execution_mode="fixture"))
    assert calls["n"] == 1  # served from disk — no gateway construction at all
    assert again == first
    forced = asyncio.run(generate_product_focus(_RUN_ID, execution_mode="fixture", force=True))
    assert calls["n"] == 2  # force regenerates
    assert forced["vertical"] == first["vertical"]


def test_unknown_run_raises_key_error(isolated_env: Path) -> None:
    from competitive_agent.product_focus import generate_product_focus

    with pytest.raises(KeyError):
        asyncio.run(generate_product_focus("RUN-nope", execution_mode="fixture"))


# ---------------------------------------------------------------------------
# fixture-mode e2e over a real fixture run (mirror rendered by the pipeline)
# ---------------------------------------------------------------------------


def test_fixture_run_e2e_auto_resolves_and_verifies_quotes(isolated_env: Path) -> None:
    """A real comparative fixture run maps product verticals (payroll et al.)
    and renders the focal mirror; the lens auto-resolves to the top candidate,
    carries both sides' deterministic stats, and containment-verifies the
    fixture report's quotes against the real evidence blocks."""
    from competitive_agent.product_focus import generate_product_focus
    from competitive_agent.runner import run_analysis

    state = run_analysis(
        "deel.com", mode="comparative", execution_mode="fixture", compare_to="rippling.com"
    )
    res = asyncio.run(generate_product_focus(state.run_id, execution_mode="fixture"))

    assert res["resolved_automatically"] is True
    candidates = res["candidates"]
    assert candidates and res["vertical"] == candidates[0]["vertical"]
    assert candidates[0]["competitor_pages"] == max(c["competitor_pages"] for c in candidates)
    # The mirror was rendered by the pipeline: the focal side is real numbers.
    assert res["focal_stats"] is not None
    assert candidates[0]["focal_pages"] is not None and candidates[0]["focal_pages"] > 0
    # Fixture pages carry "all-in-one workforce platform" verbatim -> verified;
    # the fabricated fixture quote stays flagged.
    report = res["report"]
    assert report["category_narrative"]["quote_verified"] is True
    by_title = {o["title"]: o for o in report["detailed_opportunities"]}
    assert by_title["Buying-trigger comparison asset"]["quote_verified"] is False
    assert "treat as unverified" in by_title["Buying-trigger comparison asset"]["basis"]
