"""Report renderer: required sections + JSON package shape from stored state."""

from __future__ import annotations

from competitive_agent.report import build_json_package, render_markdown
from competitive_agent.schemas.common import new_id, utcnow
from competitive_agent.schemas.company import Company
from competitive_agent.state import DirectorState


class _Ctx:
    def __init__(self):
        self.repository = None
        self.settings = None


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
