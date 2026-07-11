"""URL safety policy and secret redaction (blueprint §37.29).

Only public HTTP(S) URLs may be fetched. Blocked: non-http(s) schemes
(file://, ftp://, data:), credentials embedded in the URL, localhost,
loopback, link-local, private, and reserved addresses.

Deliberately NO DNS resolution happens here: resolving every candidate
hostname would be slow, flaky, and itself a data leak vector. Instead the
fetch layer caps redirects (``MAX_REDIRECTS``), re-validates every redirect
hop against this same policy, and non-public hostnames simply fail to
connect naturally. IP *literals* are still checked statically below via the
``ipaddress`` stdlib module.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

# Response-size and redirect caps enforced by the shared HTTP client.
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5

_ALLOWED_SCHEMES = {"http", "https"}

# Env-style and JSON-style secret assignments. The blueprint mandates
# (?i)(api[_-]?key|secret|token|authorization)\s*[=:]\s*\S+ ; this pattern is
# a superset that also catches quoted JSON keys ("api_key": "...") and
# "Authorization: Bearer <token>" values, which the literal mandated regex
# would miss (logged deviation).
_SECRET_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|secret|token|authorization)(["']?\s*[=:]\s*)"""
    r"""(bearer\s+\S+|"[^"]*"|'[^']*'|\S+)"""
)


def redact_secrets(text: str) -> str:
    """Replace secret values (never the keys) with ``[REDACTED]``."""
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


def url_is_allowed(url: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a candidate fetch URL.

    ``reason`` is a human-readable explanation either way ("ok" when
    allowed); it is safe to surface in traces and error messages.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False, "URL could not be parsed"

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"scheme '{scheme or '(none)'}' is not allowed; only http/https"

    if parts.username or parts.password:
        return False, "credentials embedded in URL are not allowed"

    try:
        host = parts.hostname
    except ValueError:
        return False, "hostname could not be parsed"
    if not host:
        return False, "URL has no hostname"
    host = host.lower().rstrip(".")

    # IP literals are checked statically (no DNS involved).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback:
            return False, "loopback address is not allowed"
        if ip.is_link_local:
            return False, "link-local address is not allowed"
        if ip.is_private:
            return False, "private address is not allowed"
        if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "reserved/multicast/unspecified address is not allowed"
        if not ip.is_global:
            return False, "non-public IP address is not allowed"
        return True, "ok"

    # Hostname checks. We intentionally do NOT resolve DNS here (see module
    # docstring): the fetch layer caps redirects and re-checks each hop, and
    # non-public hosts fail to connect naturally.
    if host == "localhost" or host.endswith(".localhost"):
        return False, "localhost is not allowed"
    if host.endswith(".local"):
        return False, "mDNS/.local hostnames are not allowed"

    return True, "ok"
