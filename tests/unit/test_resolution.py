"""Company resolution accepts a NAME or a DOMAIN (take-home requirement,
blueprint §39.2). A bare name and its domain must resolve to the same company,
and an unknown-but-domain-shaped input still resolves (medium confidence)."""

from __future__ import annotations

from competitive_agent.nodes import _resolve_offline


def test_name_and_domain_resolve_to_same_company():
    by_name = _resolve_offline("deel")
    by_domain = _resolve_offline("deel.com")
    assert by_name is not None and by_domain is not None
    assert by_name.canonical_name == by_domain.canonical_name == "Deel"
    assert by_name.primary_domain == by_domain.primary_domain == "deel.com"


def test_url_forms_normalize():
    for raw in ("https://www.deel.com/", "http://deel.com", "www.deel.com", "DEEL.COM"):
        c = _resolve_offline(raw)
        assert c is not None and c.primary_domain == "deel.com"


def test_unknown_domain_shaped_input_resolves_medium_confidence():
    c = _resolve_offline("acme-corp.io")
    assert c is not None
    assert c.primary_domain == "acme-corp.io"
    assert c.resolution_confidence == "medium"  # not a known seed, but domain-shaped


def test_unresolvable_freeform_name_returns_none():
    # A free-form name that is neither known nor domain-shaped can't be resolved
    # offline — the loop turns this into a clarifying question, not a guess.
    assert _resolve_offline("some random company that does not exist") is None
