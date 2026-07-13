"""Focal-claims verification gate — catch false-premise recommendations.

Red-team finding "false-premise recommendations": the opportunity engine
produced recommendations resting on claims that the FOCAL company (Rippling)
LACKS something — "Rippling names Deel on 0 pages", "Rippling has no goals
product" — that were never checked against Rippling's own corpus. Both were
false: ``rippling.com/compare/rippling-vs-deel`` exists, and Rippling ships a
Goals app. A recommendation built on a false premise must be dropped or
softened by the caller.

``verify_focal_claims`` is that gate. It scans the competitor package's
opportunities and proof-gaps for "focal lacks X" assertions, extracts the X
phrase, and checks the FOCAL corpus (themes, products, page titles, urls) for
evidence that the focal company DOES have X. It is PURE and deterministic given
its inputs: the only outside touch is an OPTIONAL injected ``live_check``
callable used when the corpus is silent, so tests exercise every path without a
network.

Generic: the focal company is configuration, not code. The focal identity is
derived from the inputs (an explicit ``focal_company``/``focal_domain`` hint on
the package, or the ``company_id`` carried by the focal corpus records), never
hardcoded.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# A live check of the focal site: (domain, x_phrase) -> True when the focal
# company demonstrably HAS the phrased thing. Injected so the gate stays pure.
LiveCheck = Callable[[str, str], bool]

# Cap on focal-evidence rows attached to one contradicted verdict.
_MAX_EVIDENCE = 4

# ---- "focal lacks X" detection ----------------------------------------------
#
# Each regex captures the X phrase (group "x") following a lack cue. They are
# tried in order and the FIRST regex that matches a sentence wins, so the more
# specific shapes (the "0 pages" family) are matched before the generic "no X"
# cue. Two X captures are used: ``_X_MID`` (lazy — the phrase sits BEFORE more
# required text, e.g. "Deel on 0 pages") and ``_X_END`` (greedy up to ~6 words —
# the phrase runs to the end of the clause, e.g. "no goals product"). A single
# lazy capture at a clause end would collapse to one character, so the end
# shapes must use the greedy bounded capture.
_X_MID = r"(?P<x>[A-Za-z0-9][\w&/.\- ]{0,40}?)"
_X_END = r"(?P<x>[A-Za-z0-9][\w&/.\-]*(?:\s+[\w&/.\-]+){0,5})"
_LACK_REGEXES: tuple[re.Pattern[str], ...] = (
    # "names/mentions Deel on 0 pages", "targets Deel across zero pages"
    re.compile(
        r"(?:names?|mentions?|targets?|references?|covers?|addresses?)\s+"
        + _X_MID
        + r"\s+(?:on|in|across|over)\s+(?:0|zero|no)\s+\w+",
        re.IGNORECASE,
    ),
    # "0 pages naming Deel", "no page about goals", "zero content for X"
    re.compile(
        r"(?:0|zero|no)\s+\w+\s+(?:naming|about|mentioning|covering|for|on|targeting)\s+" + _X_END,
        re.IGNORECASE,
    ),
    # "does not have / doesn't have / do not have / don't have X"
    re.compile(
        r"do(?:es)?\s+n(?:o|')t\s+have\s+(?:an?\s+|any\s+)?" + _X_END,
        re.IGNORECASE,
    ),
    # "has no / have no X"
    re.compile(r"ha(?:s|ve)\s+no\s+(?:an?\s+|any\s+)?" + _X_END, re.IGNORECASE),
    # "lacks / lacking X"
    re.compile(r"lack(?:s|ing)?\s+(?:an?\s+|any\s+)?" + _X_END, re.IGNORECASE),
    # "missing X"
    re.compile(r"missing\s+(?:an?\s+|any\s+)?" + _X_END, re.IGNORECASE),
    # "without X"
    re.compile(r"without\s+(?:an?\s+|any\s+)?" + _X_END, re.IGNORECASE),
    # "undefended X" (adjective before the noun phrase)
    re.compile(r"undefended\s+" + _X_END, re.IGNORECASE),
    # generic "no X" — last so the specific shapes above win first
    re.compile(r"\bno\s+" + _X_END, re.IGNORECASE),
)

# Generic words stripped from an X phrase before it is tokenized for corpus
# matching: they carry no distinguishing signal, so matching on them alone would
# produce spurious contradictions. Domain-agnostic on purpose.
_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "page",
        "pages",
        "product",
        "products",
        "feature",
        "features",
        "proof",
        "content",
        "dedicated",
        "marketing",
        "comparison",
        "comparisons",
        "story",
        "stories",
        "customer",
        "customers",
        "named",
        "name",
        "names",
        "any",
        "the",
        "a",
        "an",
        "of",
        "for",
        "on",
        "in",
        "and",
        "or",
        "its",
        "their",
        "own",
        "single",
        "landing",
        "asset",
        "assets",
        "public",
        "website",
        "site",
        "web",
        "material",
        "materials",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Sentence splitter — assertions are extracted per sentence so the excerpt is a
# tight quote and one field can carry several independent claims.
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")


def _tokens(text: str) -> set[str]:
    """Lowercased alphanumeric tokens length >= 2."""
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2}


def _key_tokens(x_phrase: str) -> set[str]:
    """Distinctive tokens of an X phrase (generic words removed)."""
    return {t for t in _tokens(x_phrase) if t not in _GENERIC_TOKENS and len(t) >= 3}


@dataclass
class _FocalItem:
    """One focal-corpus signal the X phrase can be matched against."""

    url: str
    label: str
    tokens: set[str] = field(default_factory=set)


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return []


def _artifact_url_map(focal_artifacts: list[dict[str, Any]]) -> dict[str, str]:
    """artifact_id -> url, so a classification signal can cite a page url."""
    out: dict[str, str] = {}
    for art in focal_artifacts:
        if not isinstance(art, dict):
            continue
        aid = str(art.get("artifact_id") or "").strip()
        url = str(art.get("url") or art.get("final_url") or "").strip()
        if aid and url:
            out[aid] = url
    return out


def _build_focal_index(
    focal_classifications: list[dict[str, Any]],
    focal_artifacts: list[dict[str, Any]],
) -> list[_FocalItem]:
    """Build the searchable focal-corpus index: themes/products from
    classifications, page titles/urls from artifacts (§ red-team fix)."""
    url_by_artifact = _artifact_url_map(focal_artifacts)
    items: list[_FocalItem] = []

    for cls in focal_classifications:
        if not isinstance(cls, dict):
            continue
        url = url_by_artifact.get(str(cls.get("artifact_id") or ""), "")
        signal_fields = (
            "products",
            "primary_theme",
            "supporting_themes",
            "proof_types",
            "personas",
            "buyer_jobs",
            "category_entry_points",
            "primary_message",
        )
        toks: set[str] = set()
        for fld in signal_fields:
            for value in _as_str_list(cls.get(fld)):
                toks |= _tokens(value)
        if toks:
            items.append(_FocalItem(url=url, label="focal classification signal", tokens=toks))

    for art in focal_artifacts:
        if not isinstance(art, dict):
            continue
        url = str(art.get("url") or art.get("final_url") or "").strip()
        title = str(art.get("title") or "").strip()
        toks = _tokens(title) | _tokens(url)
        if toks:
            label = f"focal page '{title or url}'" if (title or url) else "focal page"
            items.append(_FocalItem(url=url, label=label, tokens=toks))

    return items


def _focal_identity(
    competitor_pkg: dict[str, Any],
    focal_classifications: list[dict[str, Any]],
    focal_artifacts: list[dict[str, Any]],
) -> tuple[set[str], str]:
    """Derive focal name tokens (for gating) and a focal domain (for live_check).

    Generic — pulled from explicit package hints or the focal corpus's own
    ``company_id`` / urls, never hardcoded to any company.
    """
    names: set[str] = set()
    for key in ("focal_company", "focal", "focal_name"):
        value = competitor_pkg.get(key)
        if isinstance(value, str) and value.strip():
            names |= _tokens(value)
    for rec in list(focal_classifications) + list(focal_artifacts):
        if isinstance(rec, dict):
            cid = rec.get("company_id")
            if isinstance(cid, str) and cid.strip():
                names |= _tokens(cid)

    domain = str(competitor_pkg.get("focal_domain") or "").strip()
    if not domain:
        for art in focal_artifacts:
            if isinstance(art, dict):
                host = urlsplit(str(art.get("url") or "")).hostname or ""
                if host:
                    domain = host
                    break
    return names, domain


def _detect_lack(sentence: str) -> str | None:
    """Return the X phrase of the first lack-assertion in a sentence, else None."""
    for regex in _LACK_REGEXES:
        match = regex.search(sentence)
        if match:
            phrase = (match.group("x") or "").strip(" .,:;'\"-")
            phrase = re.sub(r"\s+", " ", phrase).strip()
            if phrase:
                return phrase
    return None


def _record_sentences(record: dict[str, Any]) -> list[tuple[str, str]]:
    """(field_key, sentence) pairs over every string/list-of-string value."""
    out: list[tuple[str, str]] = []
    for key, value in record.items():
        for text in _as_str_list(value):
            for sentence in _SENTENCE_RE.findall(text):
                trimmed = sentence.strip()
                if trimmed:
                    out.append((str(key), trimmed))
    return out


def _is_focal_lack(sentence: str, field_key: str, focal_names: set[str]) -> bool:
    """A lack-assertion is a FOCAL lack when the sentence names the focal
    company, or (when the focal name is unknown) when the field is explicitly a
    focal field. Keeps competitor-lack sentences from firing the gate."""
    if focal_names:
        return bool(_tokens(sentence) & focal_names)
    return field_key.lower().startswith("focal")


def _match_focal(x_phrase: str, focal_index: list[_FocalItem]) -> list[dict[str, str]]:
    """Focal evidence rows where the X phrase's distinctive tokens appear."""
    keys = _key_tokens(x_phrase)
    if not keys:
        return []
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in focal_index:
        overlap = keys & item.tokens
        if not overlap:
            continue
        why = f"focal corpus token(s) {sorted(overlap)} for '{x_phrase}' found in {item.label}"
        dedup_key = (item.url, why)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        evidence.append({"url": item.url, "why": why})
        if len(evidence) >= _MAX_EVIDENCE:
            break
    return evidence


def _verify_one(
    *,
    source: str,
    claim_id: str,
    claim_excerpt: str,
    x_phrase: str,
    focal_index: list[_FocalItem],
    corpus_has_content: bool,
    focal_domain: str,
    live_check: LiveCheck | None,
) -> dict[str, Any]:
    """Resolve a single "focal lacks X" assertion to a verdict + evidence."""
    corpus_evidence = _match_focal(x_phrase, focal_index)
    if corpus_evidence:
        return {
            "source": source,
            "id": claim_id,
            "claim_excerpt": claim_excerpt,
            "x_phrase": x_phrase,
            "verdict": "contradicted",
            "focal_evidence": corpus_evidence,
        }

    # Corpus is silent on X. Fall back to the injected live check when present.
    if live_check is not None:
        try:
            found = bool(live_check(focal_domain, x_phrase))
        except Exception:  # noqa: BLE001 - a live-check failure never crashes the gate
            found = False
        if found:
            return {
                "source": source,
                "id": claim_id,
                "claim_excerpt": claim_excerpt,
                "x_phrase": x_phrase,
                "verdict": "contradicted",
                "focal_evidence": [
                    {
                        "url": focal_domain,
                        "why": (f"live check of the focal site found evidence of '{x_phrase}'"),
                    }
                ],
            }
        return {
            "source": source,
            "id": claim_id,
            "claim_excerpt": claim_excerpt,
            "x_phrase": x_phrase,
            "verdict": "confirmed_absent",
            "focal_evidence": [],
        }

    # Corpus-only mode. A substantive corpus that does not show X confirms the
    # absence; an empty corpus leaves it unverified (no basis either way).
    verdict = "confirmed_absent" if corpus_has_content else "unverified"
    return {
        "source": source,
        "id": claim_id,
        "claim_excerpt": claim_excerpt,
        "x_phrase": x_phrase,
        "verdict": verdict,
        "focal_evidence": [],
    }


def _scan_records(
    records: list[dict[str, Any]],
    *,
    source: str,
    id_keys: tuple[str, ...],
    focal_names: set[str],
    focal_index: list[_FocalItem],
    corpus_has_content: bool,
    focal_domain: str,
    live_check: LiveCheck | None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        claim_id = ""
        for key in id_keys:
            candidate = record.get(key)
            if isinstance(candidate, str) and candidate.strip():
                claim_id = candidate.strip()
                break
        seen_phrases: set[str] = set()
        for field_key, sentence in _record_sentences(record):
            x_phrase = _detect_lack(sentence)
            if x_phrase is None:
                continue
            if not _is_focal_lack(sentence, field_key, focal_names):
                continue
            dedup = x_phrase.lower()
            if dedup in seen_phrases:
                continue
            seen_phrases.add(dedup)
            results.append(
                _verify_one(
                    source=source,
                    claim_id=claim_id,
                    claim_excerpt=sentence,
                    x_phrase=x_phrase,
                    focal_index=focal_index,
                    corpus_has_content=corpus_has_content,
                    focal_domain=focal_domain,
                    live_check=live_check,
                )
            )
    return results


def verify_focal_claims(
    competitor_pkg: dict[str, Any],
    focal_classifications: list[dict[str, Any]],
    focal_artifacts: list[dict[str, Any]],
    *,
    live_check: LiveCheck | None = None,
) -> list[dict[str, Any]]:
    """Verify every "focal lacks X" assertion in a competitor package.

    Scans ``competitor_pkg['opportunities']`` and ``competitor_pkg['proof_gaps']``
    for assertions that the focal company LACKS something, extracts the X phrase,
    and checks the focal corpus (themes/products/page titles/urls) for evidence
    the focal company DOES have X.

    Returns a list of dicts, one per detected assertion::

        {
          "source": "opportunity" | "proof_gap",
          "id": <opportunity_id | claim_id | "">,
          "claim_excerpt": <the sentence the assertion was found in>,
          "x_phrase": <the extracted phrase>,
          "verdict": "confirmed_absent" | "contradicted" | "unverified",
          "focal_evidence": [{"url": ..., "why": ...}, ...],
        }

    ``contradicted`` means the focal corpus (or the injected ``live_check``)
    shows the focal company HAS X — the recommendation rests on a false premise
    and must be dropped or softened by the caller. Pure and deterministic given
    its inputs; ``live_check`` (default ``None`` = corpus-only) is the only
    outside touch and is injected.
    """
    focal_names, focal_domain = _focal_identity(
        competitor_pkg, focal_classifications, focal_artifacts
    )
    focal_index = _build_focal_index(focal_classifications, focal_artifacts)
    corpus_has_content = bool(focal_index)

    opportunities = [
        r for r in _as_record_list(competitor_pkg.get("opportunities")) if isinstance(r, dict)
    ]
    proof_gaps = [
        r for r in _as_record_list(competitor_pkg.get("proof_gaps")) if isinstance(r, dict)
    ]

    results = _scan_records(
        opportunities,
        source="opportunity",
        id_keys=("opportunity_id", "id"),
        focal_names=focal_names,
        focal_index=focal_index,
        corpus_has_content=corpus_has_content,
        focal_domain=focal_domain,
        live_check=live_check,
    )
    results += _scan_records(
        proof_gaps,
        source="proof_gap",
        id_keys=("claim_id", "id"),
        focal_names=focal_names,
        focal_index=focal_index,
        corpus_has_content=corpus_has_content,
        focal_domain=focal_domain,
        live_check=live_check,
    )
    return results


def _as_record_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []
