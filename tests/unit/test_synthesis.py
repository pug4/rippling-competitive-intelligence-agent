"""Tests for the synthesis-quality helpers (feedback #1, #16, #36) and the
opportunity critics (#39, #40)."""

from __future__ import annotations

from datetime import UTC, datetime

from competitive_agent.opportunity_engine import (
    ComparabilityDraft,
    OpportunityDraft,
    _is_generic,
    _superiority_violations,
)
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.classification import MarketingClassification
from competitive_agent.synthesis import (
    artifact_authority,
    corpus_skew,
    dominant_message,
    proof_distribution,
)

# Realistic URL per surface, since authority is now URL-path-driven (reviewer R2).
_URL_BY_CATEGORY = {
    "home": "https://x/",
    "platform": "https://x/platform",
    "product": "https://x/products/thing",
    "customers": "https://x/customers/acme",
    "pricing": "https://x/pricing",
    "other": "https://x/blog/a-post",
    "": "https://x/blog/a-post",
}


def _art(aid, source_type, category="", url=None):
    if url is None:
        url = _URL_BY_CATEGORY.get(category, "https://x/blog/a-post")
    return RawArtifact(
        artifact_id=aid,
        company_id="c1",
        source_type=source_type,
        source_name="s",
        url=url,
        final_url=url,
        retrieved_at=datetime.now(UTC),
        raw_text="x",
        normalized_text="x",
        content_hash=aid,
        collection_method="m",
        metadata={"page_category": category} if category else {},
    )


def _cls(aid, theme, salience=0.8):
    return MarketingClassification(
        classification_id="cls-" + aid,
        artifact_id=aid,
        company_id="c1",
        primary_theme=theme,
        message_salience=salience,
    )


def test_authority_homepage_beats_blog():
    assert artifact_authority(_art("a", "webpage", "home")) > artifact_authority(
        _art("b", "webpage", "other")
    )
    assert artifact_authority(_art("c", "sitemap")) == 0.0


def test_dominant_message_requires_home_or_platform_and_multi_source():
    # A niche theme only on blog/exa pages is NOT company-level (no home/platform).
    arts = [_art("a1", "exa_web", "other"), _art("a2", "exa_web", "other")]
    cls = [_cls("a1", "compliance"), _cls("a2", "compliance")]
    dom = dominant_message(cls, arts)
    assert dom["theme"] == "compliance"
    assert dom["is_company_level"] is False

    # Product pages alone (no home/platform) must NOT certify company-level (R2).
    arts_p = [_art("p1", "webpage", "product"), _art("p2", "wayback", "product")]
    cls_p = [_cls("p1", "consolidation"), _cls("p2", "consolidation")]
    assert dominant_message(cls_p, arts_p)["is_company_level"] is False

    # Homepage across two source classes IS company-level.
    arts2 = [_art("h", "webpage", "home"), _art("w", "wayback", "home")]
    cls2 = [_cls("h", "consolidation"), _cls("w", "consolidation")]
    dom2 = dominant_message(cls2, arts2)
    assert dom2["is_company_level"] is True


def test_hr_platform_slug_recognized_as_platform_surface():
    # QA finding #2: /hr-platform (and similar) must be recognized as a platform
    # page, else corpus_skew falsely reports "no platform page captured" and the
    # page is under-weighted (0.15 instead of ~0.95).
    from competitive_agent.synthesis import _path_surface, corpus_skew

    assert _path_surface("/hr-platform/") == "platform"
    assert _path_surface("/workforce-platform") == "platform"
    assert _path_surface("/legal/platform-terms-of-service") != "platform"  # not first segment
    home = _art("h", "webpage", url="https://deel.com/")
    plat = _art("p", "webpage", url="https://deel.com/hr-platform/")
    warnings = corpus_skew([home, plat])
    assert not any("no platform page" in w for w in warnings)


def test_dominant_label_comes_from_company_surface_even_at_low_salience():
    # QA finding #5: the on-theme message lives on the home/platform page but at
    # low classifier salience; a higher-salience off-theme message must NOT steal
    # the label. The company-surface message wins.
    home = _art("h", "webpage", url="https://deel.com/")
    plat = _art("p", "webpage", url="https://deel.com/hr-platform/")
    pricing = _art("pr", "webpage", url="https://deel.com/pricing/")
    cls = [
        MarketingClassification(
            classification_id="cls-p",
            artifact_id="p",
            company_id="c1",
            primary_theme="consolidation",
            message_salience=0.18,
            primary_message="Deel Platform brings everything together",
        ),
        MarketingClassification(
            classification_id="cls-h",
            artifact_id="h",
            company_id="c1",
            primary_theme="consolidation",
            message_salience=0.24,
            primary_message="Deel is an all-in-one people platform",
        ),
        MarketingClassification(
            classification_id="cls-pr",
            artifact_id="pr",
            company_id="c1",
            primary_theme="consolidation",
            message_salience=0.9,
            primary_message="Transparent flexible pricing, no hidden fees",
        ),
    ]
    dom = dominant_message(cls, [home, plat, pricing])
    assert dom["theme"] == "consolidation"
    assert "pricing" not in dom["label"].lower()
    assert "platform" in dom["label"].lower() or "all-in-one" in dom["label"].lower()


def test_corpus_skew_flags_single_source_dominance():
    arts = [_art(f"a{i}", "exa_web") for i in range(8)] + [_art("h", "webpage", "home")]
    warnings = corpus_skew(arts)
    assert any("one source class dominates" in w for w in warnings)


def test_proof_distribution_not_inflated_by_one_strong_page():
    # 1 strong page, 4 feature-only pages -> overall weak-to-moderate, not strong.
    pages = [["quantified_customer_outcome"]] + [["feature_assertion"]] * 4
    dist = proof_distribution(pages)
    assert dist.quantified_outcomes == 1
    assert dist.overall_assessment != "strong"
    assert dist.n_pages == 5


def test_superiority_critic_rejects_unsupported_claims():
    draft = OpportunityDraft(
        title="Rippling is a true unified system",
        message_angle="x",
        why_this_could_backfire="y",
    )
    assert _superiority_violations(draft)
    clean = OpportunityDraft(
        title="Prove the workflow",
        message_angle="Show the end-to-end flow with a named customer",
        why_this_could_backfire="y",
        focal_product_focus=["Data Cloud"],
        target_personas=["hr_leader"],
    )
    assert not _superiority_violations(clean)


def test_genericness_critic_rejects_vague_actions():
    generic = OpportunityDraft(
        title="Create a customer story",
        message_angle="Build a customer story.",
        why_this_could_backfire="y",
    )
    assert _is_generic(generic)
    specific = OpportunityDraft(
        title="Policy-engine proof",
        message_angle="Document automatic access enforcement on org change with logs",
        why_this_could_backfire="y",
        focal_product_focus=["Policy Engine"],
        target_personas=["it_leader"],
        product_comparability=ComparabilityDraft(comparability="partial"),
    )
    assert not _is_generic(specific)


def test_product_positioning_groups_by_product():
    from competitive_agent.synthesis import product_positioning

    cls = [
        _cls_full("a", ["Payroll"], theme="consolidation", personas=["hr_leader"]),
        _cls_full("b", ["Payroll"], theme="compliance", personas=["finance_leader"]),
        _cls_full("c", ["IT Cloud"], theme="automation", personas=["it_leader"]),
    ]
    pos = product_positioning(cls)
    payroll = next(p for p in pos if p["product"] == "Payroll")
    assert payroll["pages"] == 2
    assert "consolidation" in payroll["themes"]


def test_cep_ownership_single_page_rows_are_insufficient_sample():
    # One page on either side is an anecdote — the old truthiness rule called
    # 1v1 "contested" and 1v0 an "advantage" (red-team: magnitude-blind).
    from competitive_agent.synthesis import category_entry_points

    comp = [_cls_full("a", [], ceps=["replacing_a_peo", "reducing_payroll_errors"])]
    focal = [_cls_full("b", [], ceps=["replacing_a_peo"])]
    rows = {r["cep"]: r["ownership"] for r in category_entry_points(comp, focal)}
    assert rows["replacing_a_peo"] == "insufficient_sample"
    assert rows["reducing_payroll_errors"] == "insufficient_sample"


def test_cep_ownership_is_share_normalized():
    from competitive_agent.synthesis import category_entry_points

    # Big competitor corpus (20 classified): 8 pages on opening_new_country,
    # 3 on audit. Small focal corpus (10 classified): 1 page opening_new_country
    # (8/20=40% vs 1/10=10% -> 4x ratio, dominant 8 pages -> competitor_advantage);
    # audit: 3/20=15% vs 2/10=20% -> ratio 1.33 <2x, both >=2 -> contested.
    comp = [
        _cls_full(f"a{i}", [], ceps=(["opening_new_country"] if i < 8 else [])) for i in range(20)
    ]
    for i in range(3):
        comp[i].category_entry_points.append("preparing_for_audit")
    focal = [
        _cls_full(f"b{i}", [], ceps=(["opening_new_country"] if i < 1 else [])) for i in range(10)
    ]
    for i in range(1, 3):
        focal[i].category_entry_points.append("preparing_for_audit")
    rows = {r["cep"]: r for r in category_entry_points(comp, focal)}
    onc = rows["opening_new_country"]
    assert onc["ownership"] == "competitor_advantage"
    assert onc["competitor_share"] == 0.4 and onc["focal_share"] == 0.1
    assert onc["share_ratio"] == 4.0
    audit = rows["preparing_for_audit"]
    assert audit["ownership"] == "contested"
    assert audit["share_ratio"] is not None and audit["share_ratio"] < 2.0


def test_cep_ownership_niche_corpus_not_steamrolled():
    # THE niche scenario: 2 CEP pages on a 12-page niche site (16.7% share) vs
    # 15 of 110 focal (13.6%) — raw counts read a 15-vs-2 blowout; shares say
    # contested. Normalization must protect the small corpus from false verdicts.
    from competitive_agent.synthesis import category_entry_points

    comp = [_cls_full(f"a{i}", [], ceps=(["global_payroll"] if i < 2 else [])) for i in range(12)]
    focal = [
        _cls_full(f"b{i}", [], ceps=(["global_payroll"] if i < 15 else [])) for i in range(110)
    ]
    rows = {r["cep"]: r for r in category_entry_points(comp, focal)}
    assert rows["global_payroll"]["ownership"] == "contested"


def test_cep_placeholder_rows_filtered_and_labels_normalized():
    from competitive_agent.synthesis import category_entry_points

    comp = [
        _cls_full("a1", [], ceps=["not_observed", "Growing remote and international teams"]),
        _cls_full("a2", [], ceps=["growing_remote_and_international_teams"]),
    ]
    focal = [_cls_full("b1", [], ceps=["(unspecified)"])]
    rows = category_entry_points(comp, focal)
    ceps = {r["cep"] for r in rows}
    assert "not_observed" not in ceps and "(unspecified)" not in ceps
    # Both spellings merged into one normalized key with count 2.
    assert rows and rows[0]["cep"] == "growing_remote_and_international_teams"
    assert rows[0]["competitor_pages"] == 2


def test_cep_rows_carry_example_artifact_ids_and_sort_by_ownership_group():
    from competitive_agent.synthesis import category_entry_points

    comp = [_cls_full(f"a{i}", [], ceps=["opening_new_country"]) for i in range(4)] + [
        _cls_full(f"c{i}", [], ceps=["shared_trigger"]) for i in range(2)
    ]
    focal = [_cls_full(f"b{i}", [], ceps=["shared_trigger"]) for i in range(2)] + [
        _cls_full(f"d{i}", [], ceps=[]) for i in range(4)
    ]
    rows = category_entry_points(comp, focal)
    # competitor_advantage group sorts before contested.
    assert rows[0]["cep"] == "opening_new_country"
    assert rows[0]["competitor_example_artifact_ids"] == ["a0", "a1", "a2", "a3"]
    assert rows[1]["cep"] == "shared_trigger" and rows[1]["ownership"] == "contested"


def test_commercial_motion_infers_sales_led_from_demos_and_gating():
    from competitive_agent.synthesis import commercial_motion

    cls = [
        _cls_full("a", [], cta="Book a demo", pricing="sales_gated"),
        _cls_full("b", [], cta="Talk to sales", pricing="sales_gated"),
        _cls_full("c", [], cta="Get a demo", pricing="sales_gated"),
        _cls_full("d", [], cta="Contact sales", pricing="hidden"),
    ]
    m = commercial_motion(cls)
    assert m["primary_motion"] in ("sales_led", "hybrid_sales_led")
    assert m["pricing_disclosure"] in ("sales_gated", "hidden")


def _cls_full(aid, products, theme=None, personas=None, ceps=None, cta=None, pricing=None):
    return MarketingClassification(
        classification_id="c-" + aid,
        artifact_id=aid,
        company_id="c1",
        primary_theme=theme,
        products=products or [],
        personas=personas or [],
        category_entry_points=ceps or [],
        cta=cta,
        pricing_disclosure_level=pricing or "unknown",
    )
