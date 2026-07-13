"""Report renderer: required sections + JSON package shape from stored state."""

from __future__ import annotations

from competitive_agent.report import build_json_package, render_markdown
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.buyer_voice import (
    BuyerVoiceSignals,
    BuyerVoiceTheme,
    MessageRealitySignal,
)
from competitive_agent.schemas.common import new_id, utcnow
from competitive_agent.schemas.company import Company
from competitive_agent.state import DirectorState


class _Ctx:
    def __init__(self, repository=None):
        self.repository = repository
        self.settings = None


class _Repo:
    """Minimal repository stand-in: stored artifacts + buyer_voice records."""

    def __init__(self, artifacts=(), buyer_voice=()):
        self._artifacts = list(artifacts)
        self._buyer_voice = list(buyer_voice)

    def list_artifacts(self, run_id=None):
        return list(self._artifacts)

    def list_classifications(self, run_id, family=None):
        return list(self._buyer_voice) if family == "buyer_voice" else []

    def list_claims(self, run_id=None):
        return []

    def list_opportunities(self, run_id=None):
        return []

    def list_runs(self, company=None):
        return []


def _state() -> DirectorState:
    now = utcnow()
    s = DirectorState(
        run_id="RUN-test",
        company_input="deel.com",
        compare_to="rippling.com",
        mode="comparative",
        execution_mode="fixture",
    )
    s.company = Company(
        company_id=new_id("CO"),
        canonical_name="Deel",
        primary_domain="deel.com",
        resolved_at=now,
        resolution_confidence="high",
    )
    s.focal_company = Company(
        company_id=new_id("CO"),
        canonical_name="Rippling",
        primary_domain="rippling.com",
        resolved_at=now,
        resolution_confidence="high",
    )
    s.coverage = {"current_website": "high", "pricing_and_packaging": "medium"}
    s.stop_reason = "required_coverage_reached"
    s.limitations = ["Similarweb unavailable"]
    return s


def test_json_package_has_required_top_level_keys():
    pkg = build_json_package(_state(), _Ctx())
    for key in (
        "schema_version",
        "run",
        "scope",
        "companies",
        "sources",
        "artifacts",
        "evidence",
        "classifications",
        "claims",
        "product_portfolios",
        "launches",
        "change_events",
        "matrices",
        "proof_gaps",
        "opportunities",
        "coverage",
        "limitations",
        "tool_failures",
        "trace_summary",
        "eval_summary",
    ):
        assert key in pkg, f"missing required JSON key: {key}"
    assert pkg["run"]["execution_mode"] == "fixture"
    assert [c["canonical_name"] for c in pkg["companies"]] == ["Deel", "Rippling"]


def test_markdown_leads_with_action_board_and_labels_fixture():
    s = _state()
    pkg = build_json_package(s, _Ctx())
    md = render_markdown(s, pkg)
    # Executive summary leads; Action Board (Rippling-first) precedes scope detail.
    assert md.index("## Executive summary") < md.index("## Action Board")
    assert md.index("## Action Board") < md.index("## Research scope")
    assert "What Rippling should do" in md  # Rippling-first ordering (feedback #28)
    assert "Fixture mode" in md  # synthetic runs are labeled, never passed as live
    assert "message–proof gaps" in md
    assert "Source distribution" in md  # feedback #8
    assert "Evidence appendix" in md  # feedback #10/#31
    assert "Limitations and missing data" in md
    assert "Similarweb unavailable" in md
    assert "not publicly knowable" in md.lower()


# ---------------------------------------------------------------------------
# Buyer voice: message_reality must reach BOTH human surfaces (pkg + brief)
# ---------------------------------------------------------------------------


def test_message_reality_renders_in_package_and_brief():
    s = _state()
    records = [
        BuyerVoiceSignals(
            artifact_id="ART-rev-1",
            company_id="CO-1",
            source_url="https://www.g2.com/products/deel/reviews",
            praise=[BuyerVoiceTheme(theme="clean_ui", quote="the UI is clean")],
            message_reality_signals=[
                MessageRealitySignal(
                    claim_theme="easy_setup",
                    relation="contradicts",
                    quote="onboarding was painful",
                ),
                MessageRealitySignal(
                    claim_theme="global_coverage",
                    relation="confirms",
                    quote="payroll in 40 countries just works",
                ),
            ],
        )
    ]
    pkg = build_json_package(s, _Ctx(repository=_Repo(buyer_voice=records)))
    assert pkg["buyer_voice"]["message_reality"] == [
        {
            "theme": "easy_setup",
            "relation": "contradicts",
            "n": 1,
            "example_quote": "onboarding was painful",
            "source_url": "https://www.g2.com/products/deel/reviews",
        },
        {
            "theme": "global_coverage",
            "relation": "confirms",
            "n": 1,
            "example_quote": "payroll in 40 countries just works",
            "source_url": "https://www.g2.com/products/deel/reviews",
        },
    ]
    md = render_markdown(s, pkg)
    assert "Message vs reality" in md
    assert "**CONTRADICTS**" in md and "**CONFIRMS**" in md
    assert "onboarding was painful" in md  # verbatim quote, never paraphrased
    assert "https://www.g2.com/products/deel/reviews" in md  # source link


def test_message_reality_honest_empty_when_reviews_were_mined():
    s = _state()
    records = [
        BuyerVoiceSignals(
            artifact_id="ART-rev-1",
            company_id="CO-1",
            source_url="https://g2.example/r1",
            praise=[BuyerVoiceTheme(theme="clean_ui", quote="the UI is clean")],
        )
    ]
    pkg = build_json_package(s, _Ctx(repository=_Repo(buyer_voice=records)))
    assert pkg["buyer_voice"]["n_reviews"] == 1
    assert pkg["buyer_voice"]["message_reality"] == []
    md = render_markdown(s, pkg)
    assert "no review language matched their marketing claims either way" in md


# ---------------------------------------------------------------------------
# Similarweb peers: peer artifacts surface as pkg["similarweb_peers"] + brief
# ---------------------------------------------------------------------------


def _similarweb_artifact(artifact_id, domain, metrics, peer=False) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        company_id="CO-1",
        source_type="similarweb",
        source_name="similarweb",
        url=f"https://www.similarweb.com/website/{domain}/",
        final_url=f"https://www.similarweb.com/website/{domain}/",
        retrieved_at=utcnow(),
        raw_text="similarweb estimates",
        normalized_text="similarweb estimates",
        content_hash=f"sw-{domain}",
        collection_method="exa_similarweb",
        metadata={"peer": peer, "domain": domain, "metrics": metrics},
    )


def test_similarweb_peers_rollup_exact_and_peer_skip_parity():
    s = _state()
    own = _similarweb_artifact(
        "ART-sw-own",
        "deel.com",
        {
            "estimated_monthly_visits": {"value": 5000000, "estimated": True},
            "digital_competitors": {
                "value": [
                    {"domain": "deel.com", "affinity": 0.99},
                    {"domain": "remote.com", "affinity": 0.92},
                    {"domain": "gusto.com", "affinity": 0.81},
                ],
                "estimated": True,
            },
        },
    )
    peer_with_visits = _similarweb_artifact(
        "ART-sw-p1",
        "remote.com",
        {"estimated_monthly_visits": {"value": 1200000, "estimated": True}},
        peer=True,
    )
    peer_without_visits = _similarweb_artifact("ART-sw-p2", "gusto.com", {}, peer=True)
    # Peer rows stored FIRST so parity with _similarweb_summary's peer-skip is
    # actually exercised (the own-domain block must never be a peer's).
    ctx = _Ctx(repository=_Repo(artifacts=[peer_with_visits, peer_without_visits, own]))
    pkg = build_json_package(s, ctx)
    assert pkg["similarweb_peers"] == [
        {"domain": "remote.com", "estimated_monthly_visits": 1200000, "affinity": 0.92},
        {"domain": "gusto.com", "estimated_monthly_visits": None, "affinity": 0.81},
    ]
    assert pkg["similarweb"]["domain"] == "deel.com"
    assert pkg["similarweb"]["metrics"]["estimated_monthly_visits"]["value"] == 5000000
    md = render_markdown(s, pkg)
    assert "peer traffic" in md
    assert "remote.com ~1,200,000/mo" in md
    assert "gusto.com ~" not in md  # no fabricated visits for a peer without data


def test_similarweb_peers_empty_when_no_peer_artifacts():
    pkg = build_json_package(_state(), _Ctx())
    assert pkg["similarweb_peers"] == []


# ---------------------------------------------------------------------------
# Assignment deliverable: the four questions, composed then rendered
# ---------------------------------------------------------------------------

ASSIGNMENT_HEADING = "## The assignment deliverable — four questions, answered from the evidence"


def _assignment_pkg(s: DirectorState) -> dict:
    """A crafted package: 6 classified pages across two dated windows, one
    LinkedIn post, one verified change, one proof gap whose claim text embeds
    pipe characters (untrusted competitor text), and one campaign play."""
    from competitive_agent.report import build_assignment_answers

    now = utcnow().isoformat()
    pkg = build_json_package(s, _Ctx())
    pkg["artifacts"] = [
        {
            "artifact_id": "ART-cur-0001",
            "url": "https://deel.com/global-payroll",
            "source_type": "webpage",
            "retrieved_at": now,
            "time_window_ids": ["TW-cur"],
        },
        {
            "artifact_id": "ART-cur-0002",
            "url": "https://deel.com/compliance",
            "source_type": "webpage",
            "retrieved_at": now,
            "time_window_ids": ["TW-cur"],
        },
        {
            "artifact_id": "ART-cur-0003",
            "url": "https://deel.com/eor",
            "source_type": "webpage",
            "retrieved_at": now,
            "time_window_ids": ["TW-cur"],
        },
        {
            "artifact_id": "ART-pri-0001",
            "url": "https://web.archive.org/web/2025/https://deel.com/",
            "source_type": "wayback",
            "archive_capture_at": "2025-06-01T00:00:00Z",
            "time_window_ids": ["TW-pri"],
        },
        {
            "artifact_id": "ART-pri-0002",
            "url": "https://web.archive.org/web/2025/https://deel.com/pricing",
            "source_type": "wayback",
            "archive_capture_at": "2025-06-02T00:00:00Z",
            "time_window_ids": ["TW-pri"],
        },
        {
            "artifact_id": "ART-pri-0003",
            "url": "https://web.archive.org/web/2025/https://deel.com/eor",
            "source_type": "wayback",
            "archive_capture_at": "2025-06-03T00:00:00Z",
            "time_window_ids": ["TW-pri"],
        },
    ]
    pkg["scope"]["time_windows"] = [
        {"window_id": "TW-cur", "purpose": "current"},
        {"window_id": "TW-pri", "purpose": "comparison"},
    ]
    pkg["classifications"] = [
        {
            "artifact_id": "ART-cur-0001",
            "primary_theme": "global_payroll",
            "primary_message": "Run payroll in 150 countries from one platform",
            "message_salience": 0.9,
            "personas": ["CFO"],
            "products": ["Global Payroll"],
            "villain_exact_wording": ["a patchwork of local providers"],
        },
        {
            "artifact_id": "ART-cur-0002",
            "primary_theme": "compliance",
            "primary_message": "Compliance built-in | everywhere at once",  # untrusted pipe
            "message_salience": 0.8,
            "personas": ["CFO"],
            "products": ["Global Payroll"],
        },
        {
            "artifact_id": "ART-cur-0003",
            "primary_theme": "global_payroll",
            "primary_message": "Hire anywhere without an entity",
            "message_salience": 0.5,
            "personas": ["CFO"],
            "products": ["EOR"],
        },
        {
            "artifact_id": "ART-pri-0001",
            "primary_theme": "compliance",
            "primary_message": "Stay compliant",
            "message_salience": 0.4,
            "personas": ["HR leader"],
        },
        {
            "artifact_id": "ART-pri-0002",
            "primary_theme": "compliance",
            "primary_message": "Compliance handled",
            "message_salience": 0.4,
            "personas": ["HR leader"],
        },
        {
            "artifact_id": "ART-pri-0003",
            "primary_theme": "compliance",
            "primary_message": "Global compliance",
            "message_salience": 0.4,
            "personas": ["HR leader"],
        },
    ]
    pkg["dominant_message"] = {
        "theme": "global_payroll",
        "label": "Global payroll, one platform",
        "reason": "recurs across surfaces",
        "is_company_level": True,
        "surfaces": ["website"],
        "source_classes": ["webpage"],
    }
    pkg["product_positioning"] = [
        {
            "product": "Global Payroll",
            "pages": 2,
            "themes": ["global_payroll", "compliance"],
            "personas": ["CFO"],
            "proof_types": ["quantified_outcome"],
        }
    ]
    pkg["linkedin_posts"] = [
        {
            "theme": "global_payroll",
            "excerpt": "we just launched payroll in 150 countries",
            "url": "https://www.linkedin.com/posts/abc",
            "post_url": "https://www.linkedin.com/posts/abc",
            "published_at": "2026-06-01",
            "author": "Jane",
        }
    ]
    pkg["evidence"] = [
        {
            "evidence_id": "EV-1",
            "artifact_id": "ART-cur-0002",
            "exact_excerpt": "Compliance you can prove",
        }
    ]
    pkg["claims"] = [{"claim_id": "CLM-1", "evidence_ids": ["EV-1"]}]
    pkg["change_events"] = [
        {
            "dimension": "messaging_emphasis",
            "theme": "compliance",
            "prior_state": "compliance as a feature bullet",
            "current_state": "compliance as the headline",
            "apparent_change_at": "2026-05-01",
            "confidence": "high",
            "lifecycle": "expanding",
            "current_evidence_ids": ["EV-1"],
        }
    ]
    pkg["proof_gaps"] = [
        {
            "claim_id": "CLM-1",
            "claim_text": "Compliance built-in | 150 countries | zero risk",  # pipes!
            "short_label": "compliance",
            "attackability": "high",
            "proof_strength": "none",
            "focal_proof_strength": "available",
            "actionable_interpretation": "attack with certified proof",
            "claim_specificity": "quantified",
        }
    ]
    pkg["opportunities"] = [
        {
            "title": "Prove-it compliance campaign",
            "deliverable_type": "landing_page",
            "action_category": "attack",
            "structural_defensibility": "durable",
            "message_angle": "They claim it; we certify it",
            "target_personas": ["CFO", "COO"],
            "primary_metric": "demo requests",
            "kill_rule": "kill if CTR < 0.5% after 2 weeks",
            "focal_proof_status": "available",
            "focal_current_usage": "partial",
            "legal_review_required": False,
            "why_this_could_backfire": "could invite scrutiny of our own claims",
            "supporting_claim_ids": ["CLM-1"],
            "competitor_pattern": "unproven compliance claims repeated on 2 pages",
        }
    ]
    pkg["assignment_answers"] = build_assignment_answers(pkg)
    return pkg


def _assignment_section(md: str) -> str:
    return md[md.index(ASSIGNMENT_HEADING) : md.index("\n## Action Board\n")]


def test_assignment_section_order_and_headings():
    s = _state()
    md = render_markdown(s, _assignment_pkg(s))
    positions = [
        md.index("## Executive summary"),
        md.index(ASSIGNMENT_HEADING),
        md.index("### 1. What messaging angles and themes are they running?"),
        md.index("### 2. How do they position their product(s)?"),
        md.index(
            "### 3. What's changed recently (new campaigns, new ICPs targeted, messaging pivots)?"
        ),
        md.index("### 4. What gaps does this surface for Rippling — and what we'd exploit"),
        md.index("\n## Action Board\n"),
    ]
    assert positions == sorted(positions), "section must sit between exec summary and Action Board"
    section = _assignment_section(md)
    # the structured mirror + evidence appendix are both pointed to
    assert "assignment_answers" in section and "Evidence appendix" in section


def test_assignment_section_every_artifact_link_resolves():
    import re

    s = _state()
    pkg = _assignment_pkg(s)
    md = render_markdown(s, pkg)
    section = _assignment_section(md)
    links = re.findall(r"\[(ART-[^\]]+)\]\((https?://[^)]+)\)", section)
    assert links, "the section must carry artifact citations"
    by_url = {a["url"]: a["artifact_id"] for a in pkg["artifacts"]}
    for label, url in links:
        assert url in by_url, f"cited URL {url} not in the package artifact ledger"
        assert by_url[url].startswith(label), f"label {label} does not match artifact for {url}"


def test_assignment_section_bars_and_shares():
    s = _state()
    md = render_markdown(s, _assignment_pkg(s))
    section = _assignment_section(md)
    q1 = section[section.index("### 1.") : section.index("### 2.")]
    q1_rows = [
        ln
        for ln in q1.splitlines()
        if ln.startswith("| global_payroll") or ln.startswith("| compliance")
    ]
    # both nonzero-share themes carry a real bar glyph and a percent
    assert len(q1_rows) == 2
    for row in q1_rows:
        assert "█" in row, f"nonzero share must render a bar: {row}"
        assert "%" in row


def test_assignment_section_pipes_in_competitor_text_do_not_break_tables():
    s = _state()
    md = render_markdown(s, _assignment_pkg(s))
    section = _assignment_section(md)
    # Q4 gaps table: header + separator + exactly ONE data row, 6 columns each
    q4 = section[section.index("### 4.") :]
    gap_table = [ln for ln in q4.splitlines() if ln.startswith("|")]
    assert len(gap_table) == 3, f"pipe in claim text split the row: {gap_table}"
    assert all(ln.count("|") == 7 for ln in gap_table)
    assert "150 countries" in gap_table[2]  # the claim text survived, sans pipes
    # Q1 row with the piped example message also stays one row / 6 columns
    q1 = section[section.index("### 1.") : section.index("### 2.")]
    q1_compliance = [ln for ln in q1.splitlines() if ln.startswith("| compliance")]
    assert len(q1_compliance) == 1 and q1_compliance[0].count("|") == 7
    assert "Compliance built-in" in q1_compliance[0]


def test_assignment_section_icp_shift_and_linkedin_render():
    s = _state()
    md = render_markdown(s, _assignment_pkg(s))
    section = _assignment_section(md)
    # prior window is 3 HR-leader pages, current 3 CFO pages -> both deltas clear 15 pts
    assert "**CFO**: 0% → 100% (+100 pts)" in section
    assert "**HR leader**: 100% → 0% (-100 pts)" in section
    assert "we just launched payroll in 150 countries" in section
    assert "([post](https://www.linkedin.com/posts/abc))" in section


def test_assignment_gap_and_play_citations_survive_humanized_theme_labels():
    """Regression (flagship RUN-b256fab1c1dd): gap records whose claim_id is a
    GAP- id (not in the claims ledger), with no strongest_proof_id and a
    HUMANIZED short_label ('global payroll' for taxonomy theme
    'global_payroll'), must still cite the theme's classified pages — and the
    play that supports on that GAP id inherits those citations. Before the
    theme-key normalization fix, both rendered 'Sources: —'."""
    from competitive_agent.report import build_assignment_answers

    s = _state()
    pkg = _assignment_pkg(s)
    pkg["proof_gaps"] = [
        {
            "claim_id": "GAP-deadbeef0001",  # NOT in pkg["claims"]
            "claim_text": "One platform for global payroll everywhere",
            "short_label": "global payroll",  # humanized: theme is global_payroll
            "strongest_proof_id": None,
            "attackability": "medium",
            "proof_strength": "weak",
            "focal_proof_strength": "strong",
            "actionable_interpretation": "attack breadth with proof",
        }
    ]
    pkg["opportunities"][0]["supporting_claim_ids"] = ["GAP-deadbeef0001"]
    pkg["assignment_answers"] = build_assignment_answers(pkg)

    q4 = pkg["assignment_answers"]["q4_gaps_and_opportunities"]
    gap_cites = q4["message_proof_gaps"][0]["citations"]
    play_cites = q4["campaign_plays"][0]["citations"]
    assert gap_cites, "humanized short_label must still resolve to theme pages"
    theme_artifact_ids = {"ART-cur-0001", "ART-cur-0003"}  # the global_payroll pages
    assert all(c["artifact_id"] in theme_artifact_ids for c in gap_cites)
    assert play_cites, "play must inherit the gap's citations via its GAP- id"
    assert {c["artifact_id"] for c in play_cites} <= theme_artifact_ids

    md = render_markdown(s, pkg)
    section = _assignment_section(md)
    q4_md = section[section.index("### 4.") :]
    assert "Sources: —" not in q4_md, "no uncited gap/play rows in the rendered section"


def test_assignment_section_empty_run_renders_honest_oneliners():
    s = _state()
    pkg = build_json_package(s, _Ctx())  # no artifacts, no classifications
    md = render_markdown(s, pkg)
    section = _assignment_section(md)
    assert "Classified messaging themes: none observed on this run." in section
    assert "LinkedIn amplification: none observed on this run." in section
    assert "Verified change events: none observed on this run." in section
    assert "Message–proof gaps: none observed on this run." in section
    assert "Campaign plays: none survived the critics on this run." in section
    # never an empty table: no table header without data rows
    assert "| Theme |" not in section and "| Their claim |" not in section


def _iter_assignment_text_fields(answers):
    """The free-text fields build_assignment_answers slices/formats from
    untrusted classification/opportunity input — every one must be a plain str."""
    q1 = answers["q1_messaging_themes"]
    for row in q1["themes"]:
        yield "q1.example_message", row["example_message"]
    for row in q1["linkedin_amplification"]:
        yield "q1.example_excerpt", row["example_excerpt"]
    for row in answers["q2_product_positioning"]["villain_wording"]:
        yield "q2.verbatim", row["verbatim"]
    q4 = answers["q4_gaps_and_opportunities"]
    for row in q4["message_proof_gaps"]:
        yield "q4.their_claim", row["their_claim"]
        yield "q4.gap_justification", row["justification"]
    for row in q4["campaign_plays"]:
        yield "q4.play_justification", row["justification"]


def test_assignment_answers_coerce_dict_shaped_fields_no_crash():
    """Regression (P0 item 1): a classifier/opportunity field arriving as a DICT
    (or list) rather than a string must not crash build_assignment_answers on the
    ``[:220]``/``[:180]`` slices (the live ``KeyError: slice(None, 220, None)``);
    ``_coerce_text`` degrades it to clean text and the brief still renders."""
    from competitive_agent.report import build_assignment_answers

    s = _state()
    pkg = _assignment_pkg(s)
    cls = pkg["classifications"]
    # primary_message as a wrapped object — the exact shape that crashed render.
    cls[0]["primary_message"] = {"text": "Run payroll in 150 countries from one platform"}
    # primary_message dict with no known text key -> compact-JSON fallback.
    cls[1]["primary_message"] = {"promise": "Compliance built-in", "detail": "everywhere"}
    # villain wording items as dicts.
    cls[0]["villain_exact_wording"] = [{"label": "a patchwork of local providers"}]
    # an OPPORTUNITY field as a dict.
    pkg["opportunities"][0]["competitor_pattern"] = {
        "statement": "unproven compliance claims repeated on 2 pages"
    }
    # a proof-gap claim text as a dict.
    pkg["proof_gaps"][0]["claim_text"] = {"text": "Compliance built-in, 150 countries, zero risk"}

    # 1) build must not raise; every emitted text field must be a plain str.
    aa = build_assignment_answers(pkg)
    for path, value in _iter_assignment_text_fields(aa):
        assert isinstance(value, str), f"{path} not coerced to str: {value!r}"

    # 2) coercion extracted the inner string, never leaving a dict/list.
    themes = aa["q1_messaging_themes"]["themes"]
    payroll = next(r for r in themes if r["theme"] == "global_payroll")
    assert payroll["example_message"] == "Run payroll in 150 countries from one platform"
    play = aa["q4_gaps_and_opportunities"]["campaign_plays"][0]
    assert play["justification"] == "unproven compliance claims repeated on 2 pages"
    verbatim = aa["q2_product_positioning"]["villain_wording"][0]["verbatim"]
    assert verbatim == "a patchwork of local providers"
    gap = aa["q4_gaps_and_opportunities"]["message_proof_gaps"][0]
    assert gap["their_claim"] == "Compliance built-in, 150 countries, zero risk"
    li = aa["q1_messaging_themes"]["linkedin_amplification"][0]
    assert li["example_excerpt"] == "we just launched payroll in 150 countries"  # str passthrough
    # no-known-key dict -> compact JSON (double-quoted), never a Python dict repr.
    compliance = next(r for r in themes if r["theme"] == "compliance")
    assert compliance["example_message"].startswith("{")
    assert "'" not in compliance["example_message"]

    # 3) citations survive and the brief still renders the deliverable section.
    assert sum(len(r.get("citations", [])) for r in themes) >= 1
    pkg["assignment_answers"] = aa
    md = render_markdown(s, pkg)
    section = _assignment_section(md)
    assert "### 1. What messaging angles and themes are they running?" in section
    assert "Run payroll in 150 countries from one platform" in section


def test_coerce_text_branches():
    """_coerce_text contract: str passthrough, dict text/label/statement lookup,
    compact-JSON fallback, list join, None -> '', scalar -> str."""
    from competitive_agent.report import _coerce_text

    assert _coerce_text("plain") == "plain"
    assert _coerce_text(None) == ""
    assert _coerce_text({"text": "t"}) == "t"
    assert _coerce_text({"label": "l"}) == "l"
    assert _coerce_text({"statement": "s"}) == "s"
    # first matching key wins in priority order text > label > statement
    assert _coerce_text({"label": "l", "text": "t"}) == "t"
    # non-string inner value is ignored -> compact JSON fallback (double-quoted)
    dumped = _coerce_text({"text": {"nested": 1}, "x": 2})
    assert dumped.startswith("{") and "'" not in dumped
    # list -> coerced items joined by a space, empties dropped
    assert _coerce_text(["a", {"text": "b"}, None, ""]) == "a b"
    # scalar fallback
    assert _coerce_text(7) == "7"
