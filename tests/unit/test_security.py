"""URL policy and secret redaction (blueprint §37.29)."""

from __future__ import annotations

import pytest

from competitive_agent.security import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    redact_secrets,
    url_is_allowed,
)


def test_public_https_allowed() -> None:
    allowed, reason = url_is_allowed("https://example.com")
    assert allowed, reason
    assert reason == "ok"


def test_public_http_with_path_and_port_allowed() -> None:
    allowed, _ = url_is_allowed("http://example.com:8080/pricing?plan=team")
    assert allowed


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file.txt",
        "data:text/html,hello",
        "javascript:alert(1)",
        "example.com/no-scheme",
    ],
)
def test_non_http_schemes_rejected(url: str) -> None:
    allowed, reason = url_is_allowed(url)
    assert not allowed
    assert reason


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://localhost:8000/admin",
        "http://LOCALHOST/",
        "http://app.localhost/",
        "http://127.0.0.1",
        "http://127.0.0.1:9200/_cluster",
        "http://[::1]",
        "http://[::1]:8080/",
    ],
)
def test_localhost_and_loopback_rejected(url: str) -> None:
    allowed, _ = url_is_allowed(url)
    assert not allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1",
        "http://10.255.1.2/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.10/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata, link-local
        "http://0.0.0.0/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
    ],
)
def test_private_and_reserved_addresses_rejected(url: str) -> None:
    allowed, _ = url_is_allowed(url)
    assert not allowed


def test_credentials_in_url_rejected() -> None:
    allowed, reason = url_is_allowed("https://user:pass@host.com/path")
    assert not allowed
    assert "credentials" in reason

    allowed, _ = url_is_allowed("https://user@host.com/")
    assert not allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://printer.local",
        "https://mymac.local/share",
        "https://deep.sub.domain.local/x",
    ],
)
def test_dot_local_hostnames_rejected(url: str) -> None:
    allowed, _ = url_is_allowed(url)
    assert not allowed


def test_constants_match_policy() -> None:
    assert MAX_RESPONSE_BYTES == 2_000_000
    assert MAX_REDIRECTS == 5


def test_redact_secrets_env_style() -> None:
    text = "API_KEY=sk-live-abc123 and SECRET: hunter2 and token = tok_9f8e"
    redacted = redact_secrets(text)
    assert "sk-live-abc123" not in redacted
    assert "hunter2" not in redacted
    assert "tok_9f8e" not in redacted
    assert "[REDACTED]" in redacted
    # keys are preserved so logs stay debuggable
    assert "API_KEY" in redacted


def test_redact_secrets_json_and_headers() -> None:
    text = '{"api_key": "sk-super-secret-42", "query": "deel"} Authorization: Bearer eyJhbGci'
    redacted = redact_secrets(text)
    assert "sk-super-secret-42" not in redacted
    assert "eyJhbGci" not in redacted
    assert '"query": "deel"' in redacted  # non-secret content untouched


def test_redact_secrets_leaves_plain_text_alone() -> None:
    text = "Deel expanded its EOR campaign across LinkedIn in Q2."
    assert redact_secrets(text) == text
