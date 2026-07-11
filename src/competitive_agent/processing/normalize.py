"""Single normalization path for content storage AND excerpt verification.

Every artifact's ``normalized_text`` is produced here, and every exact-excerpt
containment check re-normalizes through the same functions — so verification
can never drift from storage (§40.1 accuracy gate).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_SMART_CHARS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    " ": " ",
    "​": "",
}

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for src, dst in _SMART_CHARS.items():
        text = text.replace(src, dst)
    return _WS_RE.sub(" ", text).strip()


def contains_excerpt(haystack: str, excerpt: str) -> bool:
    """True when the excerpt appears in the haystack after shared normalization."""
    if not excerpt:
        return False
    return normalize_text(excerpt).casefold() in normalize_text(haystack).casefold()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def html_to_text(html: str) -> str:
    """Extract readable text from HTML: trafilatura first, bs4 fallback."""
    if not html:
        return ""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html, include_comments=False, include_tables=True, favor_recall=True
        )
        if extracted and extracted.strip():
            return extracted
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ")
    except Exception:
        return html
