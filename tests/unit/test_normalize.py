from __future__ import annotations

from competitive_agent.processing.normalize import (
    contains_excerpt,
    content_hash,
    html_to_text,
    normalize_text,
)


def test_normalize_collapses_whitespace_and_smart_chars():
    raw = "One  “global”   platform\n\n— for everyone"
    assert normalize_text(raw) == 'One "global" platform - for everyone'


def test_contains_excerpt_survives_smart_quote_and_case_drift():
    page = "Deel says: “Run Global Payroll” with confidence.\n\nMore copy."
    assert contains_excerpt(page, "run global payroll")
    assert contains_excerpt(page, "“Run Global Payroll”")
    assert not contains_excerpt(page, "run domestic payroll")
    assert not contains_excerpt(page, "")


def test_content_hash_is_normalization_stable():
    assert content_hash("A  B\nC") == content_hash("A B C")
    assert content_hash("A B C") != content_hash("A B D")


def test_html_to_text_strips_scripts():
    html = "<html><body><h1>Pricing</h1><script>alert('x')</script><p>Simple, transparent plans for growing teams worldwide.</p></body></html>"
    text = html_to_text(html)
    assert "Pricing" in text or "Simple, transparent plans" in text
    assert "alert(" not in text
