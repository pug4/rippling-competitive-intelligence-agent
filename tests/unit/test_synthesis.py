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


def _art(aid, source_type, category="", url="https://x/"):
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


def test_dominant_message_requires_top_surface_and_multi_source():
    # A niche theme repeated only on low-authority pages is NOT company-level.
    arts = [_art("a1", "exa_web"), _art("a2", "exa_web")]
    cls = [_cls("a1", "compliance"), _cls("a2", "compliance")]
    dom = dominant_message(cls, arts)
    assert dom["theme"] == "compliance"
    assert dom["is_company_level"] is False  # no top-level surface, one source class

    # Same theme on a homepage across two source classes IS company-level.
    arts2 = [_art("h", "webpage", "home"), _art("w", "wayback")]
    cls2 = [_cls("h", "consolidation"), _cls("w", "consolidation")]
    dom2 = dominant_message(cls2, arts2)
    assert dom2["is_company_level"] is True


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
