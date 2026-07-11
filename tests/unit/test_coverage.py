from __future__ import annotations

from competitive_agent import coverage as cov


def test_initial_coverage_covers_all_dimensions():
    c = cov.initial_coverage()
    assert set(c) == set(cov.COVERAGE_DIMENSIONS)
    assert all(v == "not_attempted" for v in c.values())


def test_raise_coverage_never_lowers():
    c = cov.initial_coverage()
    assert cov.raise_coverage(c, "current_website", "medium")
    assert not cov.raise_coverage(c, "current_website", "low")
    assert c["current_website"] == "medium"
    assert cov.raise_coverage(c, "current_website", "high")


def test_unavailable_is_terminal_finding_not_failure():
    c = cov.initial_coverage()
    cov.mark_unavailable(c, "out_of_home")
    assert c["out_of_home"] == "unavailable"
    ok, missing = cov.sufficient(c, "snapshot", compare=False)
    # unavailable dimensions are excluded from "missing" — they are findings
    assert "out_of_home" not in missing


def test_sufficient_requires_historical_for_longitudinal():
    c = cov.initial_coverage()
    for dim in cov.required_dimensions("snapshot", compare=False):
        c[dim] = "high"
    ok, _ = cov.sufficient(c, "snapshot", compare=False)
    assert ok
    ok, missing = cov.sufficient(c, "longitudinal", compare=False)
    assert not ok and "historical_website" in missing
