"""Unit tests for claim building/judging and the temporal engine's guards."""

from __future__ import annotations

import pytest

from competitive_agent.processing.claim_builder import build_claims
from competitive_agent.processing.claim_judge import judge_claim
from competitive_agent.processing.temporal import detect_candidate_changes
from competitive_agent.prompt_registry import PromptRegistry
from competitive_agent.schemas.claim import StrategicClaim
from competitive_agent.schemas.evidence import EvidenceItem


class FakeGateway:
    """Returns a programmed output object regardless of prompt."""

    def __init__(self, output):
        self._output = output

    async def generate_structured(self, *_a, **_k):
        class R:
            output = self._output

        return R()


def _ev(eid: str, excerpt: str = "all-in-one workforce platform") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid,
        artifact_id="art1",
        company_id="c1",
        exact_excerpt=excerpt,
        normalized_statement="stmt",
        evidence_type="positioning",
        source_quality="high",
        extraction_confidence="high",
    )


PROMPTS = PromptRegistry()


async def test_builder_strips_unknown_evidence_and_performance_claims():
    from competitive_agent.processing.claim_builder import CandidateClaim, ClaimBuilderOutput

    output = ClaimBuilderOutput(
        claims=[
            CandidateClaim(
                statement="Deel leads with an all-in-one platform message",
                supporting_evidence_ids=["EV1", "GHOST"],
                proposed_status="observed",
                suggested_confidence="high",
            ),
            CandidateClaim(
                statement="Their ads have a 3x ROAS",
                supporting_evidence_ids=["EV1"],
                proposed_status="observed",
            ),
            CandidateClaim(statement="No support here", supporting_evidence_ids=["GHOST"]),
        ]
    )
    claims = await build_claims(
        [_ev("EV1")],
        FakeGateway(output),
        PROMPTS,
        company_name="Deel",
        company_id="c1",
        time_windows=["current"],
    )
    # ROAS claim dropped (performance), unsupported claim dropped, ghost id stripped
    assert len(claims) == 1
    assert claims[0].evidence_ids == ["EV1"]
    assert "ROAS" not in claims[0].statement


@pytest.mark.parametrize(
    "verdict,expected_status",
    [
        ("fully_supported", "observed"),
        ("partially_supported", "hypothesis"),
        ("unsupported", "rejected"),
        ("contradicted", "contradicted"),
        ("context_dependent", "hypothesis"),
    ],
)
async def test_judge_applies_acceptance_policy(verdict, expected_status):
    from competitive_agent.processing.claim_judge import ClaimJudgeVerdict

    claim = StrategicClaim(
        claim_id="CLM1",
        company_id="c1",
        statement="Deel is all-in-one",
        status="hypothesis",
        evidence_ids=["EV1"],
        claim_confidence="high",
        research_coverage="low",
        source_quality="high",
        confidence_reason="x",
    )
    v = ClaimJudgeVerdict(
        verdict=verdict, narrowed_claim="Deel markets multiple products", reason="r"
    )
    judged = await judge_claim(claim, {"EV1": _ev("EV1")}, FakeGateway(v), PROMPTS)
    assert judged.status == expected_status


class _C:
    """Minimal classification stand-in for temporal candidate detection."""

    def __init__(self, artifact_id, primary_theme):
        self.artifact_id = artifact_id
        self.primary_theme = primary_theme
        self.pricing_disclosure_level = None
        self.competitive_stance = None


def test_temporal_requires_both_periods():
    # only a current window populated -> no candidate (Rule 8)
    by_window = {"current": [_C("a", "consolidation")], "comparison": []}
    assert detect_candidate_changes(by_window) == []


def test_temporal_detects_message_change_across_periods():
    by_window = {
        "comparison": [_C("old", "global_hiring")],
        "current": [_C("new", "consolidation")],
    }
    candidates = detect_candidate_changes(by_window)
    dims = {c["dimension"] for c in candidates}
    assert "primary_theme" in dims
    change = next(c for c in candidates if c["dimension"] == "primary_theme")
    assert change["prior_state"] == "global_hiring"
    assert change["current_state"] == "consolidation"


def test_temporal_no_candidate_when_message_unchanged():
    by_window = {
        "comparison": [_C("old", "consolidation")],
        "current": [_C("new", "consolidation")],
    }
    assert detect_candidate_changes(by_window) == []
