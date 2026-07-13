"""Red-team remediation tests: unified window predicate, change-event
reconciliation, corpus-evidence coverage floors, honest limitations/negatives,
ads-junk filtering, LinkedIn excerpt cleaning, claim-judge banned-term recheck,
and the asymmetric-corpus normalization guards in comparison."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.classification import MarketingClassification
from competitive_agent.schemas.company import TimeWindow


def _win(purpose: str, start: str, end: str) -> TimeWindow:
    return TimeWindow(
        window_id=f"W-{purpose}",
        label=purpose,
        start_at=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end_at=datetime.fromisoformat(end).replace(tzinfo=UTC),
        purpose=purpose,
    )


WINDOWS = [
    _win("comparison", "2025-07-12", "2026-04-13"),
    _win("current", "2026-04-13", "2026-07-12"),
]


def _art(aid: str, published: str | None = None, source_type: str = "webpage") -> RawArtifact:
    return RawArtifact(
        artifact_id=aid,
        company_id="c1",
        source_type=source_type,
        source_name="s",
        url=f"https://x/{aid}",
        final_url=f"https://x/{aid}",
        retrieved_at=datetime.now(UTC),
        published_at=(datetime.fromisoformat(published).replace(tzinfo=UTC) if published else None),
        raw_text="x",
        normalized_text="x",
        content_hash=aid,
        collection_method="m",
    )


def _cls(aid: str, theme: str | None, supporting: list[str] | None = None):
    return MarketingClassification(
        classification_id="cls-" + aid,
        artifact_id=aid,
        company_id="c1",
        primary_theme=theme,
        supporting_themes=supporting or [],
    )


# ---------------------------------------------------------------------------
# assign_window — THE single membership predicate
# ---------------------------------------------------------------------------


def test_assign_window_buckets_prior_current_outside_and_undated():
    from competitive_agent.synthesis import assign_window

    assert assign_window(_art("a", "2025-09-01"), WINDOWS) == "prior"
    assert assign_window(_art("b", "2026-05-01"), WINDOWS) == "current"
    # PRE-window date must be OUTSIDE, not silently dumped into current
    # (red-team: a 2025-04-08 post was counted as current).
    assert assign_window(_art("c", "2025-04-08"), WINDOWS) == "outside"
    # Undated live content = retrieved now = current.
    assert assign_window(_art("d", None), WINDOWS) == "current"
    # Boundary: comparison end date belongs to prior (deterministic tie).
    assert assign_window(_art("e", "2026-04-13"), WINDOWS) == "prior"


def test_temporal_baseline_excludes_pre_window_from_current():
    from competitive_agent.synthesis import temporal_baseline

    arts = [_art("pre", "2025-04-08"), _art("p1", "2025-09-01"), _art("c1", None)]
    cls = [
        _cls("pre", "native_platform_breadth"),
        _cls("p1", "consolidation"),
        _cls("c1", "automation"),
    ]
    tb = temporal_baseline(cls, arts, WINDOWS)
    assert tb["outside_windows"] == 1
    assert "native_platform_breadth" not in tb["current_window"]["themes"]
    assert tb["prior_window"]["themes"] == {"consolidation": 1}
    assert "outside both windows" in tb["note"]
    assert tb["prior_window"]["themes_share"]["consolidation"] == 1.0


# ---------------------------------------------------------------------------
# reconcile_change_events — mid-run events vs the final corpus
# ---------------------------------------------------------------------------


def _event(theme: str, prior_state: str | None = None) -> dict:
    return {
        "change_id": f"CHG-{theme}",
        "dimension": "theme_emergence",
        "prior_state": prior_state or f"“{theme}” not observed in the prior-window sample",
        "current_state": f"“{theme}” present in 5 current-window artifacts",
        "lifecycle": "emerging",
        "confidence": "low",
        "prior_evidence_ids": ["stale1", "stale2"],
        "current_evidence_ids": ["stale3"],
        "alternative_explanations": ["this may be a collection/archive coverage asymmetry x"],
    }


def test_reconcile_relabels_rare_prior_theme_to_expanding():
    from competitive_agent.processing.temporal import reconcile_change_events

    arts = [_art(f"p{i}", "2025-09-01") for i in range(3)] + [
        _art(f"c{i}", "2026-05-01") for i in range(5)
    ]
    cls = [
        _cls("p0", "cross_system_workflow"),
        _cls("p1", "consolidation"),
        _cls("p2", "consolidation"),
    ]
    cls += [_cls(f"c{i}", "cross_system_workflow") for i in range(5)]
    events, notes = reconcile_change_events([_event("cross_system_workflow")], cls, arts, WINDOWS)
    assert len(events) == 1
    ev = events[0]
    # The contradiction is repaired: never "not observed" when the baseline has it.
    assert ev["lifecycle"] == "expanding"
    assert "present but rare" in ev["prior_state"]
    assert ev["prior_theme_count"] == 1 and ev["prior_window_n"] == 3
    assert ev["prior_evidence_ids"] == ["p0"]  # theme-filtered, not "first 5"
    assert ev["prior_evidence_role"] == "theme_occurrences"
    assert ev["current_evidence_ids"] == [f"c{i}" for i in range(5)]
    assert any("relabeled" in n for n in notes)


def test_reconcile_true_emergence_keeps_nonempty_window_sample_ids():
    from competitive_agent.processing.temporal import reconcile_change_events

    arts = [_art("p0", "2025-09-01"), _art("c0", "2026-05-01"), _art("c1", "2026-05-02")]
    cls = [_cls("p0", "consolidation"), _cls("c0", "cost_reduction"), _cls("c1", "cost_reduction")]
    events, _ = reconcile_change_events([_event("cost_reduction")], cls, arts, WINDOWS)
    ev = events[0]
    assert ev["lifecycle"] == "emerging"
    assert ev["prior_theme_count"] == 0
    # Grounding gate requires non-empty prior ids — the window SAMPLE, marked.
    assert ev["prior_evidence_ids"] and ev["prior_evidence_role"] == "window_sample"
    assert "not observed in any of the 1 dated prior-window artifacts" in ev["prior_state"]


def test_reconcile_drops_theme_that_thinned_out_and_stale_scalar():
    from competitive_agent.processing.temporal import reconcile_change_events

    arts = [_art("p0", "2025-09-01"), _art("c0", "2026-05-01")]
    cls = [_cls("p0", "consolidation"), _cls("c0", "automation")]
    scalar = {
        "change_id": "CHG-scalar",
        "dimension": "primary_theme",
        "prior_state": "global_hiring",  # final corpus says consolidation -> stale
        "current_state": "automation",
        "lifecycle": "repositioned",
        "confidence": "medium",
        "prior_evidence_ids": ["p0"],
        "current_evidence_ids": ["c0"],
        "alternative_explanations": [],
    }
    events, notes = reconcile_change_events([_event("ghost_theme"), scalar], cls, arts, WINDOWS)
    assert events == []
    assert any("dropped" in n for n in notes) and len(notes) == 2


def test_reconcile_keeps_scalar_that_still_reproduces():
    from competitive_agent.processing.temporal import reconcile_change_events

    arts = [_art("p0", "2025-09-01"), _art("c0", "2026-05-01")]
    cls = [_cls("p0", "global_hiring"), _cls("c0", "automation")]
    scalar = {
        "change_id": "CHG-scalar",
        "dimension": "primary_theme",
        "prior_state": "global_hiring",
        "current_state": "automation",
        "lifecycle": "repositioned",
        "confidence": "medium",
        "prior_evidence_ids": ["p0"],
        "current_evidence_ids": ["c0"],
        "alternative_explanations": [],
    }
    events, notes = reconcile_change_events([scalar], cls, arts, WINDOWS)
    assert len(events) == 1 and events[0]["change_id"] == "CHG-scalar"
    assert notes == []


def test_detect_candidate_changes_uses_real_prior_counts():
    from competitive_agent.processing.temporal import detect_candidate_changes

    class C:
        def __init__(self, artifact_id, primary_theme, supporting=None):
            self.artifact_id = artifact_id
            self.primary_theme = primary_theme
            self.supporting_themes = supporting or []
            self.pricing_disclosure_level = None
            self.competitive_stance = None

    by_window = {
        "comparison": [C("p0", "consolidation"), C("p1", "consolidation", ["automation"])],
        "current": [C(f"c{i}", "automation") for i in range(4)] + [C("c9", "consolidation")],
    }
    cands = detect_candidate_changes(by_window)
    emergences = [c for c in cands if c["dimension"] == "theme_emergence"]
    assert len(emergences) == 1
    e = emergences[0]
    # automation existed once prior -> EXPANSION wording with theme-filtered ids,
    # never "not observed".
    assert e.get("expansion") is True
    assert "present but rare" in e["prior_state"]
    assert e["prior_artifact_ids"] == ["p1"]
    assert e["prior_count"] == 1


def test_reconcile_drops_stale_numeric_alternative_explanations():
    # Mid-run alts carry sample sizes from the PARTIAL corpus ("only 6
    # artifacts" next to a reconciled "n of 14" prior_state) — any alt with
    # numbers or absence claims is stale by construction and must be replaced
    # with a fresh, correct quantitative caveat.
    from competitive_agent.processing.temporal import reconcile_change_events

    arts = [_art(f"p{i}", "2025-09-01") for i in range(3)] + [
        _art(f"c{i}", "2026-05-01") for i in range(5)
    ]
    cls = [
        _cls("p0", "cross_system_workflow"),
        _cls("p1", "consolidation"),
        _cls("p2", "consolidation"),
    ] + [_cls(f"c{i}", "cross_system_workflow") for i in range(5)]
    ev = _event("cross_system_workflow")
    ev["alternative_explanations"] = [
        "The small prior-window sample (only 6 artifacts) may have missed the theme",
        "the theme's absence earlier could reflect limited sampling",
        "editorial focus may have shifted rather than strategy",
    ]
    events, _ = reconcile_change_events([ev], cls, arts, WINDOWS)
    alts = events[0]["alternative_explanations"]
    assert not any("only 6" in a for a in alts)
    assert not any("absence" in a for a in alts)
    assert any("editorial focus" in a for a in alts)  # number-free qualitative alt kept
    assert any("3 dated artifacts vs 5 current" in a for a in alts)  # correct sizes


def test_temporal_baseline_counts_supporting_themes_like_events():
    # ONE counting rule: baseline membership must match the events' rule
    # (primary OR supporting, once per artifact) or the same brief calls a
    # theme "emerged (current only)" that its own event counts prior.
    from competitive_agent.synthesis import temporal_baseline

    arts = [_art("p1", "2025-09-01"), _art("c1", None)]
    cls = [
        _cls("p1", "consolidation", supporting=["global_hiring"]),
        _cls("c1", "global_hiring"),
    ]
    tb = temporal_baseline(cls, arts, WINDOWS)
    assert tb["prior_window"]["themes"]["global_hiring"] == 1
    assert "global_hiring" in tb["stable_themes"]
    assert "global_hiring" not in tb["emerged_themes"]


def test_cep_ownership_no_focal_corpus_is_not_compared():
    # A missing focal corpus is NOT a measured zero — snapshot runs must never
    # publish competitor_advantage against a side that was never collected.
    from competitive_agent.synthesis import category_entry_points

    comp = [_cls_full(f"a{i}", [], ceps=["opening_new_country"]) for i in range(5)]
    rows = category_entry_points(comp, [])
    assert rows and all(r["ownership"] == "not_compared" for r in rows)
    assert all(r["focal_pages"] is None and r["focal_share"] is None for r in rows)
    assert "no focal corpus" in rows[0]["ownership_basis"]


def test_cep_ratio_threshold_compares_unrounded():
    # True ratio 1.9978 rounds to 2.0 for display but must stay CONTESTED
    # (threshold compares the unrounded value).
    from competitive_agent.synthesis import category_entry_points

    comp = [_cls_full(f"a{i}", [], ceps=(["trigger"] if i < 97 else [])) for i in range(123)]
    focal = [_cls_full(f"b{i}", [], ceps=(["trigger"] if i < 45 else [])) for i in range(114)]
    row = category_entry_points(comp, focal)[0]
    assert row["share_ratio"] == 2.0  # display rounding
    assert row["ownership"] == "contested"  # decision on unrounded 1.9978


def _cls_full(aid, products, ceps=None):
    from competitive_agent.schemas.classification import MarketingClassification as MC

    return MC(
        classification_id="c-" + aid,
        artifact_id=aid,
        company_id="c1",
        products=products or [],
        category_entry_points=ceps or [],
    )


# ---------------------------------------------------------------------------
# Coverage floors + honest limitations / uncertainty / negatives
# ---------------------------------------------------------------------------


def _state(coverage=None, stop_reason="runtime_exhausted"):
    return SimpleNamespace(
        coverage=coverage or {},
        stop_reason=stop_reason,
        time_windows=WINDOWS,
        limitations=[],
        iteration=19,
    )


def test_honest_coverage_floors_raise_but_never_lower():
    from competitive_agent.report import _honest_coverage

    data = {
        "artifacts": [
            *[{"source_type": "linkedin_post"} for _ in range(6)],
            {"source_type": "google_ads"},
            *[{"source_type": "events"} for _ in range(3)],
            {"source_type": "news", "published_at": "2026-05-01T00:00:00Z"},
            *[{"source_type": "webpage"} for _ in range(25)],
        ],
        "classifications": [],
        "proof_gaps": [],
    }
    state = _state(coverage={"public_linkedin": "not_attempted", "events": "high"})
    cov = _honest_coverage(state, data, ceps=[{"competitor_pages": 2}] * 6)
    assert cov["public_linkedin"] == "medium"  # 6 posts -> medium floor
    assert cov["paid_media"] == "low"  # discovery pointers never above low
    assert cov["events"] == "high"  # floor never lowers an earned level
    assert cov["category_entry_points"] == "medium"  # 6 observed CEPs
    assert cov["launches_current"] == "low"  # 1 current-window news


def test_honest_coverage_classification_derived_dims_not_false_absences():
    # funnel/proof_strategy/focal_vulnerabilities were PERMANENTLY
    # not_attempted while the same brief displayed funnel stages, a proof-gap
    # table, and focal-proof ratings (verifier round 2).
    from competitive_agent.report import _honest_coverage

    data = {
        "artifacts": [],
        "classifications": [
            {"funnel_stages": ["awareness"], "proof_types": ["customer_logo"]} for _ in range(25)
        ],
        "proof_gaps": [{"focal_proof_strength": "none"}] * 3,
    }
    cov = _honest_coverage(_state(), data, ceps=[])
    assert cov["funnel"] == "medium"
    assert cov["proof_strategy"] == "medium"
    assert cov["focal_vulnerabilities"] == "medium"
    # Placeholder-only funnel stages don't count.
    data2 = {
        "artifacts": [],
        "classifications": [{"funnel_stages": ["not_observed"]}],
        "proof_gaps": [],
    }
    cov2 = _honest_coverage(_state(), data2, ceps=[])
    assert cov2.get("funnel", "not_attempted") == "not_attempted"


def test_coverage_details_include_not_attempted_rows():
    from competitive_agent.synthesis import coverage_details

    state = _state(coverage={"identity": "high"})
    details = coverage_details(state, [], [], coverage={"identity": "high"})
    by_dim = {d.dimension: d for d in details}
    assert by_dim["paid_media"].level == "not_attempted"
    assert "absence of collection" in by_dim["paid_media"].reason


def test_largest_uncertainty_never_claims_uncollected_channels_that_exist():
    from competitive_agent.report import _largest_uncertainty

    pkg = {
        "corpus_skew_warnings": [],
        "coverage_detail": [
            {"dimension": "paid_media", "level": "low"},
            {"dimension": "identity", "level": "high"},
        ],
        "change_events": [{"x": 1}],
        "temporal_baseline": {},
    }
    line = _largest_uncertainty(pkg)
    assert "were not collected" not in line
    assert "paid_media" in line


def test_derived_limitations_reframe_budget_stop_with_unattempted_dims():
    from competitive_agent.report import _derived_limitations

    state = _state()
    cov = {"identity": "high", "paid_media": "not_attempted", "funnel": "not_attempted"}
    lims = _derived_limitations(state, cov, {"artifacts": [{"source_type": "webpage"}]})
    assert len(lims) == 1
    assert "budget-bounded" in lims[0] and "19 iterations" in lims[0]
    assert "2 dimensions remain unattempted" in lims[0]


def test_stop_reason_label_maps_enums_and_prefixes():
    from competitive_agent.report import _stop_reason_label

    assert _stop_reason_label("runtime_exhausted") == "budget-bounded (hit the research-time cap)"
    assert _stop_reason_label("required_coverage_reached").startswith("completed")
    assert _stop_reason_label("no_remaining_actions_with_expected_value; x").startswith("completed")
    assert _stop_reason_label("node_error:foo").startswith("stopped on an internal error")


def test_reconcile_negatives_annotates_superseded_similarweb():
    from competitive_agent.report import _reconcile_negatives

    negs = ["Similarweb-via-Exa returned a payload but no metric fields; nothing synthesized."]
    out = _reconcile_negatives(
        negs, {"similarweb": {"metrics": {"estimated_monthly_visits": 1}}}, 4
    )
    assert "superseded" in out[0]
    assert any("google_ads discovery artifacts excluded" in n for n in out)
    # Without a populated block, the negative ships untouched.
    out2 = _reconcile_negatives(negs, {"similarweb": {}}, 0)
    assert out2 == negs


# ---------------------------------------------------------------------------
# Ads junk + LinkedIn excerpts + citation invariant + claim judge
# ---------------------------------------------------------------------------


def test_is_junk_ads_artifact_predicate():
    from competitive_agent.synthesis import is_junk_ads_artifact

    meta = {"is_discovery_pointer": True}
    faq = "https://adstransparency.google.com/faq?region=US"
    blank = "https://adstransparency.google.com/?authuser=0&region=DE"
    other = "https://adstransparency.google.com/?region=US&domain=costco.com"
    ar = "https://adstransparency.google.com/advertiser/AR00536403756997148673?region=US"
    ar_own = "https://adstransparency.google.com/advertiser/AR123?domain=deel.com"
    assert is_junk_ads_artifact(faq, meta, "deel.com")
    assert is_junk_ads_artifact(blank, meta, "deel.com")
    assert is_junk_ads_artifact(other, meta, "deel.com")
    assert not is_junk_ads_artifact(ar, meta, "deel.com")
    assert not is_junk_ads_artifact(ar_own, meta, "deel.com")
    # NOT a discovery pointer (e.g. a fixture ad creative) -> never junk.
    assert not is_junk_ads_artifact(faq, {}, "deel.com")
    # HTML-entity-encoded query strings must not bypass the domain rule
    # (verifier: &amp; made parse_qs see 'amp;domain' and the rule failed open).
    entity = "https://adstransparency.google.com/advertiser/AR123?region=US&amp;domain=costco.com"
    assert is_junk_ads_artifact(entity, meta, "deel.com")
    # Label-boundary domain match: wheeldeel.com is NOT deel.com; subdomain is.
    assert is_junk_ads_artifact(
        "https://adstransparency.google.com/advertiser/AR123?domain=wheeldeel.com",
        meta,
        "deel.com",
    )
    assert not is_junk_ads_artifact(
        "https://adstransparency.google.com/advertiser/AR123?domain=app.deel.com",
        meta,
        "deel.com",
    )


def test_clean_linkedin_excerpt_strips_wall_and_falls_back():
    from competitive_agent.report import _clean_linkedin_excerpt

    wall = (
        "Global hiring sounds exciting | Jane Doe posted on the topic | LinkedIn "
        "Agree & Join LinkedIn By clicking Continue to join or sign in"
    )
    assert _clean_linkedin_excerpt(wall, "Real classified message") == "Real classified message"
    post = (
        "# Post by Ashish Jeet Mitra · LinkedIn · 2026-06-12\n"
        "We just shipped a new payroll engine that closes books 3 days faster "
        "for every entity we operate. Sign in to view more"
    )
    cleaned = _clean_linkedin_excerpt(post, None)
    assert "Sign in" not in cleaned and "Post by" not in cleaned
    assert cleaned.startswith("We just shipped")


def test_placeholder_personas_filtered_from_matrix():
    from competitive_agent.synthesis import persona_channel_funnel

    cls = [
        MarketingClassification(
            classification_id="c1",
            artifact_id="a1",
            company_id="c",
            personas=[
                "not_observed",
                "(unspecified)",
                "HR leader",
                "unclassified_signals: Product Partnership Manager",
            ],
        )
    ]
    m = persona_channel_funnel(cls, {"a1": "webpage"})
    assert m["personas"] == ["HR leader"]


@pytest.mark.asyncio
async def test_claim_judge_rejects_banned_narrowed_claim():
    from competitive_agent.processing.claim_judge import ClaimJudgeVerdict, judge_claim
    from competitive_agent.prompt_registry import PromptRegistry
    from competitive_agent.schemas.claim import StrategicClaim
    from competitive_agent.schemas.evidence import EvidenceItem

    class FakeGateway:
        def __init__(self, output):
            self._output = output

        async def generate_structured(self, *_a, **_k):
            return SimpleNamespace(output=self._output)

    claim = StrategicClaim(
        claim_id="CLM1",
        company_id="c1",
        statement="Deel emphasizes payroll automation",
        status="hypothesis",
        evidence_ids=["EV1"],
        claim_confidence="high",
        research_coverage="low",
        source_quality="high",
        confidence_reason="x",
    )
    ev = EvidenceItem(
        evidence_id="EV1",
        artifact_id="a1",
        company_id="c1",
        exact_excerpt="x",
        normalized_statement="x",
        evidence_type="positioning",
        source_quality="high",
        extraction_confidence="high",
    )
    verdict = ClaimJudgeVerdict(
        verdict="partially_supported",
        narrowed_claim="Deel's ads drive strong ROAS on payroll keywords",
        reason="r",
    )
    judged = await judge_claim(claim, {"EV1": ev}, FakeGateway(verdict), PromptRegistry())
    # LLM narrowing reintroduced a banned performance term -> keep the vetted
    # original statement, still downgraded to hypothesis, with the rejection noted.
    assert judged.statement == claim.statement
    assert judged.status == "hypothesis"
    assert "performance term" in judged.confidence_reason


# ---------------------------------------------------------------------------
# comparison.py — asymmetric-corpus guards
# ---------------------------------------------------------------------------


def test_stance_hybrid_thin_protects_niche_corpus():
    from competitive_agent.comparison import _stance

    # 2 pages of a 130-page corpus (1.5% share): still thin -> investigate.
    overall_big, _, _ = _stance(
        "Niche",
        "Rippling",
        "global_payroll",
        "weak",
        "strong",
        n_pages=2,
        theme_share=2 / 130,
    )
    assert overall_big == "investigate"
    # 2 pages of a 12-page niche corpus (16.7% share): a real investment -> attack.
    overall_niche, level, _ = _stance(
        "Niche",
        "Rippling",
        "global_payroll",
        "weak",
        "strong",
        n_pages=2,
        theme_share=2 / 12,
    )
    assert overall_niche == "attack" and level == "high"


class _StubRepo:
    """Repository stub: two runs' classifications + artifact meta."""

    def __init__(self, by_run):
        self._by_run = by_run

    def list_classifications(self, run_id, family="merged"):
        return self._by_run.get(run_id, [])

    def list_artifacts(self, run_id=None):
        arts = []
        for c in self._by_run.get(run_id, []):
            arts.append(_art(c.artifact_id, "2026-05-01"))
        return arts


def _mk_cls(run, aid, theme, message="msg", proof=None):
    return MarketingClassification(
        classification_id=f"{run}-{aid}",
        artifact_id=aid,
        company_id=run,
        primary_theme=theme,
        primary_message=f"{message} {theme}",
        proof_types=proof or [],
    )


def test_small_focal_corpus_downgrades_attack_and_discloses():
    from competitive_agent.comparison import build_message_proof_gaps

    comp_cls = [_mk_cls("comp", f"a{i}", "global_payroll") for i in range(20)]
    focal_cls = [
        _mk_cls(
            "focal",
            f"b{i}",
            "global_payroll",
            proof=["quantified_customer_outcome", "named_customer_story"],
        )
        for i in range(5)
    ]
    repo = _StubRepo({"comp": comp_cls, "focal": focal_cls})
    gaps = build_message_proof_gaps(
        "comp", "focal", repo, competitor_name="Niche", focal_name="Rippling"
    )
    assert gaps, "expected at least one gap"
    g = gaps[0]
    assert g.sample_sufficiency == "insufficient_focal_sample"
    assert g.competitor_theme_share == 1.0
    # A would-be attack verdict cannot ship on a 5-page focal mirror: the guard
    # downgrades attack -> investigate/medium and says so.
    assert g.attackability == "medium"
    assert g.attackability_detail.overall == "investigate"
    assert "Downgraded from attack" in g.actionable_interpretation
    assert "only 5 classified" in g.actionable_interpretation


def test_gap_shares_populated_for_normal_corpora():
    from competitive_agent.comparison import build_message_proof_gaps

    comp_cls = [_mk_cls("comp", f"a{i}", "consolidation") for i in range(4)] + [
        _mk_cls("comp", f"x{i}", "automation") for i in range(16)
    ]
    focal_cls = [_mk_cls("focal", f"b{i}", "consolidation") for i in range(16)]
    repo = _StubRepo({"comp": comp_cls, "focal": focal_cls})
    gaps = build_message_proof_gaps(
        "comp", "focal", repo, competitor_name="Deel", focal_name="Rippling"
    )
    g = next(x for x in gaps if x.short_label == "consolidation")
    assert g.competitor_theme_share == pytest.approx(4 / 20)
    assert g.focal_theme_share == pytest.approx(1.0)
    assert g.sample_sufficiency == "ok"


# ---------------------------------------------------------------------------
# EDA insight graphics — the five judged joins, reproduced deterministically
# ---------------------------------------------------------------------------


def _cls_ig(
    aid,
    theme=None,
    supporting=None,
    proofs=None,
    funnel=None,
    ceps=None,
    named=None,
    pricing=None,
    cta=None,
):
    from competitive_agent.schemas.classification import MarketingClassification as MC

    return MC(
        classification_id="c-" + aid,
        artifact_id=aid,
        company_id="c1",
        primary_theme=theme,
        supporting_themes=supporting or [],
        proof_types=proofs or [],
        funnel_stages=funnel or [],
        category_entry_points=ceps or [],
        named_competitors=named or [],
        pricing_disclosure_level=pricing or "unknown",
        cta=cta,
    )


def test_insight_graphics_claim_vs_record_and_channel_split():
    from competitive_agent.synthesis import insight_graphics

    comp_cls = [
        _cls_ig("w1", "compliance", proofs=["certification_or_compliance_record"]),
        _cls_ig("w2", "compliance", proofs=["quantified_customer_outcome"]),
        _cls_ig(
            "w3", "automation", supporting=["compliance"], proofs=["quantified_customer_outcome"]
        ),
        _cls_ig("w4", "compliance", proofs=["quantified_customer_outcome"], pricing="hidden"),
        _cls_ig("li1", "automation", proofs=["product_demonstration"]),
        _cls_ig("li2", "automation"),
        *[_cls_ig(f"p{i}", "automation", pricing="hidden") for i in range(4)],
    ]
    comp_arts = [
        _art(a.artifact_id, None, "linkedin_post" if a.artifact_id.startswith("li") else "webpage")
        for a in comp_cls
    ]
    focal_cls = [
        _cls_ig("f1", "compliance", proofs=["certification_or_compliance_record"]),
        _cls_ig("f2", "compliance", proofs=["certification_or_compliance_record"]),
        _cls_ig("f3", "automation"),
        _cls_ig("f4", "automation"),
    ]
    ig = insight_graphics(comp_cls, comp_arts, focal_cls, [], {}, {}, {}, {}, "Deel", "Rippling")
    cvr = ig["claim_vs_record"]
    assert cvr["competitor"]["voice_n"] == 4 and cvr["competitor"]["voice_share"] == 0.4
    assert cvr["competitor"]["cert_rate"] == 0.25  # 1/4 incl. the supporting-theme page
    assert cvr["competitor"]["quant_standin_rate"] == 0.75
    assert cvr["focal"]["cert_rate"] == 1.0  # 2/2
    assert cvr["board_column"] == "ATTACK"
    cps = ig["channel_proof_split"]
    assert cps["competitor"]["demo_linkedin"] == 1 and cps["competitor"]["linkedin_n"] == 2
    assert cps["competitor"]["demo_web"] == 0 and cps["competitor"]["web_n"] == 8
    assert cps["competitor"]["no_public_pricing_web"] == 5  # w4 + 4 plain hidden


def test_insight_graphics_funnel_voids_and_proof_vs_voice():
    from competitive_agent.synthesis import insight_graphics

    comp_cls = [
        *[
            _cls_ig(
                f"a{i}",
                "automation",
                funnel=["evaluation"],
                ceps=["big_trigger"],
                proofs=["quantified_customer_outcome"],
            )
            for i in range(12)
        ],
        *[_cls_ig(f"b{i}", "automation", funnel=["awareness"]) for i in range(6)],
    ]
    comp_arts = [_art(a.artifact_id) for a in comp_cls]
    focal_cls = [
        *[
            _cls_ig(f"f{i}", "automation", funnel=["decision"], ceps=["big_trigger"])
            for i in range(8)
        ],
        *[_cls_ig(f"g{i}", "automation") for i in range(4)],
    ]
    comp_vmap = {a.artifact_id: ["benefits"] for a in comp_cls}
    focal_vmap = {c.artifact_id: ["benefits"] for c in focal_cls}
    ceps = [
        {"cep": "big_trigger", "ownership": "contested", "competitor_pages": 12, "focal_pages": 8}
    ]
    ig = insight_graphics(
        comp_cls, comp_arts, focal_cls, ceps, {}, comp_vmap, focal_vmap, {}, "Deel", "Rippling"
    )
    fv = ig["funnel_voids"]
    benefits = next(r for r in fv["rows"] if r["vertical"] == "benefits")
    assert benefits["void"] is True
    assert benefits["competitor"] == {"n": 18, "evaluation_n": 12, "decision_n": 0}
    assert benefits["focal"]["decision_n"] == 8
    row = ig["proof_vs_voice"]["rows"][0]
    assert row["competitor"]["rate"] == 1.0 and row["focal"]["rate"] == 0.0


def test_insight_graphics_affinity_defense_census():
    from competitive_agent.synthesis import insight_graphics

    sitemap = _art("sm", None, "sitemap")
    sitemap.metadata["page_map"] = [
        {"url": "https://x.com/vs/rippling/", "category": "comparison"},
        {"url": "https://x.com/vs/gusto/", "category": "comparison"},
        {"url": "https://x.com/products/payroll", "category": "product"},
    ]
    similarweb = {
        "metrics": {
            "digital_competitors": {
                "value": [
                    {"domain": "remote.com", "affinity": 1.0},
                    {"domain": "rippling.com", "affinity": 0.88},
                ]
            }
        }
    }
    comp_cls = [_cls_ig("w1", "automation", named=["Rippling"])]
    ig = insight_graphics(
        comp_cls,
        [sitemap, _art("w1")],
        [_cls_ig("f1", "automation")],
        [],
        similarweb,
        {},
        {},
        {},
        "Deel",
        "Rippling",
    )
    ad = ig["affinity_defense"]
    rows = {r["domain"]: r for r in ad["rows"]}
    assert rows["remote.com"]["defended"] is False  # top affinity, open SERP
    assert rows["rippling.com"]["defended"] is True
    assert rows["rippling.com"]["mentions"] == 1
    assert "gusto" in ad["orphan_comparison_slugs"]  # vs-page outside the audience


def test_insight_graphics_honest_without_focal():
    from competitive_agent.synthesis import insight_graphics

    comp_cls = [_cls_ig("w1", "compliance", proofs=["quantified_customer_outcome"])]
    ig = insight_graphics(comp_cls, [_art("w1")], [], [], {}, {}, {}, {}, "X", "Rippling")
    assert "focal" not in ig["claim_vs_record"]  # competitor-only, no fabricated zeros
    assert "proof_vs_voice" not in ig


# ---------------------------------------------------------------------------
# Custom time windows: per-run current_days + the rewindow endpoint
# ---------------------------------------------------------------------------


def test_time_windows_honor_state_current_days():
    import asyncio

    from competitive_agent.graph import GraphContext
    from competitive_agent.nodes import load_or_create_time_windows
    from competitive_agent.state import DirectorState

    state = DirectorState(run_id="RUN-w", company_input="x.com", lookback_days=365, current_days=30)
    ctx = GraphContext(repository=None, trace=None, config=None, settings=None)
    asyncio.run(load_or_create_time_windows(state, ctx))
    cur = next(w for w in state.time_windows if w.purpose == "current")
    comp = next(w for w in state.time_windows if w.purpose == "comparison")
    assert (cur.end_at - cur.start_at).days == 30
    assert comp.end_at == cur.start_at


def test_time_windows_clamp_inverted_current_days():
    import asyncio

    from competitive_agent.graph import GraphContext
    from competitive_agent.nodes import load_or_create_time_windows
    from competitive_agent.state import DirectorState

    # current >= lookback would make the comparison window empty — clamp.
    state = DirectorState(run_id="RUN-w2", company_input="x.com", lookback_days=60, current_days=90)
    ctx = GraphContext(repository=None, trace=None, config=None, settings=None)
    asyncio.run(load_or_create_time_windows(state, ctx))
    cur = next(w for w in state.time_windows if w.purpose == "current")
    assert (cur.end_at - cur.start_at).days == 30  # lookback // 2
