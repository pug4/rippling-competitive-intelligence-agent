"""Deterministic, on-demand visualization builders for the analysis chat.

The chat model may REQUEST one visualization (a ``chart_type`` + ``params``);
it never computes the numbers. This module owns that computation. Every builder
is a PURE function over the run's stored ``data.json`` package that returns a
rendered *spec* whose every value is a REAL count/share tallied from the
package — there is no model call in this file and no number is ever invented.

Honesty rules (Accuracy is #1):
- An unknown ``chart_type`` returns a typed ``{"error", "error_type"}``.
- A non-whitelisted ``group_by`` field returns a typed error.
- Genuinely empty data returns a typed ``empty_data`` error — never a fake row.

Rendered spec schema (what every successful builder returns)::

    {
      "chart_type": str,            # echo of the requested type
      "type": "bar"|"grouped_bar"|"table"|"heatmap"|"line",
      "title": str,
      "caption": str,               # what it shows + "computed from N pages"
      "data": <shape per type>,     # see below — REAL counts/shares only
      "citations": [{"artifact_id", "url"}],
      "source_note": str,           # computed from this run's classifications
    }

``data`` shapes by ``type``:
    bar          -> [ {"label", "value", "share"?}, ... ]
    grouped_bar  -> {"groups": [...], "series": [ {"name", "values": [...]} ]}
    table        -> {"columns": [...], "rows": [ [...], ... ]}
    heatmap      -> {"rows": [...], "cols": [...], "cells": {row: {col: value}}}
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

# group_by is DOUBLY whitelisted: the chart_type "group_by" AND the field it
# groups classifications on. Anything outside this set is rejected with a typed
# error (no arbitrary attribute access over the package).
GROUP_BY_FIELDS: tuple[str, ...] = (
    "personas",
    "segments",
    "proof_types",
    "funnel_stages",
    "claim_types",
    "category_entry_points",
    "competitive_stance",
    "primary_theme",
)

# Public ad libraries the ad_creatives table may read (transparency-center
# records only — never spend/performance, which the corpus does not expose).
_AD_SOURCE_TYPES: tuple[str, ...] = ("google_ads", "meta_ads")

# How many {artifact_id,url} citations any single chart attaches (keeps specs
# small; the full corpus is always in the report/JSON).
_MAX_CITATIONS = 20


# --------------------------------------------------------------------------- #
# small deterministic helpers                                                  #
# --------------------------------------------------------------------------- #
def _err(error_type: str, message: str) -> dict[str, Any]:
    """A typed, honest error — the ONLY thing a builder returns for empty or
    rejected input. Never accompanied by fabricated data."""
    return {"error": message, "error_type": error_type}


def _artifact_urls(pkg: dict[str, Any]) -> dict[str, str]:
    """artifact_id -> url, from both the artifacts and sources views."""
    urls: dict[str, str] = {}
    for a in pkg.get("artifacts", []) or []:
        aid = a.get("artifact_id")
        if aid:
            urls[str(aid)] = a.get("url") or a.get("final_url") or ""
    for s in pkg.get("sources", []) or []:
        sid = s.get("artifact_id")
        if sid:
            urls.setdefault(str(sid), s.get("url") or "")
    return urls


def _companies(pkg: dict[str, Any]) -> list[dict[str, Any]]:
    return pkg.get("companies", []) or []


def _competitor_name(pkg: dict[str, Any]) -> str:
    comps = _companies(pkg)
    return (
        (comps[0].get("canonical_name") if comps else None)
        or (pkg.get("scope") or {}).get("company_input")
        or "the competitor"
    )


def _focal_name(pkg: dict[str, Any]) -> str:
    comps = _companies(pkg)
    return (comps[1].get("canonical_name") if len(comps) > 1 else None) or "the focal company"


def _competitor_id(pkg: dict[str, Any]) -> str | None:
    comps = _companies(pkg)
    return comps[0].get("company_id") if comps else None


def _competitor_classifications(pkg: dict[str, Any]) -> list[dict[str, Any]]:
    """The competitor's classified pages. Classifications carry a company_id;
    when present we scope to the competitor so a focal mirror can't pollute the
    counts. When none carry a company_id we use them all (single-company run)."""
    cls = pkg.get("classifications", []) or []
    cid = _competitor_id(pkg)
    if cid and any(c.get("company_id") for c in cls):
        scoped = [c for c in cls if c.get("company_id") == cid]
        return scoped or cls
    return cls


def _distinct(items: Iterable[Any]) -> list[str]:
    """Order-preserving de-dup of non-empty string ids."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it in (None, ""):
            continue
        s = str(it)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _cite(urls: dict[str, str], artifact_ids: Iterable[Any]) -> list[dict[str, str]]:
    """Build {artifact_id,url} citations for the given ids (deduped, capped).
    Only ids we can resolve to the corpus are included — no invented urls."""
    out: list[dict[str, str]] = []
    for aid in _distinct(artifact_ids):
        if aid in urls:
            out.append({"artifact_id": aid, "url": urls[aid]})
        if len(out) >= _MAX_CITATIONS:
            break
    return out


def _int_param(params: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(params.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def build_theme_distribution(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Bar: the competitor's classified pages by ``primary_theme`` (count + share)."""
    cls = _competitor_classifications(pkg)
    themed = [c for c in cls if c.get("primary_theme")]
    counts = Counter(str(c["primary_theme"]) for c in themed)
    if not counts:
        return _err("empty_data", "No classified page carries a primary_theme in this run.")
    total = sum(counts.values())
    top_n = _int_param(params, "top_n", default=12, lo=1, hi=50)
    rows = [
        {"label": theme.replace("_", " "), "value": n, "share": round(n / total, 4)}
        for theme, n in counts.most_common(top_n)
    ]
    comp = _competitor_name(pkg)
    urls = _artifact_urls(pkg)
    return {
        "chart_type": "theme_distribution",
        "type": "bar",
        "title": f"{comp} — message themes by page count",
        "caption": (
            f"Share of {comp}'s {total} classified pages by primary message theme "
            f"(top {len(rows)} of {len(counts)} distinct themes). Computed from this "
            "run's stored classifications."
        ),
        "data": rows,
        "citations": _cite(urls, (c.get("artifact_id") for c in themed)),
        "source_note": (
            f"Computed from {total} classified pages ({len(counts)} distinct themes) "
            "in this run's stored corpus."
        ),
    }


def build_cep_ownership(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Grouped bar: competitor vs focal ownership of each buying-intent
    (category entry point), by share of each company's classified corpus."""
    ceps = pkg.get("category_entry_points", []) or []
    if not ceps:
        return _err("empty_data", "This run extracted no category entry points (buying intents).")
    comp, focal = _competitor_name(pkg), _focal_name(pkg)
    # Uniform mode across the chart: shares when every row has them, else counts.
    use_shares = all(c.get("competitor_share") is not None for c in ceps)
    groups: list[str] = []
    comp_vals: list[float | int] = []
    focal_vals: list[float | int] = []
    missing_focal = 0
    for c in ceps:
        groups.append(str(c.get("cep", "")).replace("_", " "))
        if use_shares:
            comp_vals.append(round(float(c.get("competitor_share") or 0.0), 4))
            fs = c.get("focal_share")
            if fs is None:
                missing_focal += 1
                focal_vals.append(0.0)
            else:
                focal_vals.append(round(float(fs), 4))
        else:
            comp_vals.append(int(c.get("competitor_pages") or 0))
            fp = c.get("focal_pages")
            if fp is None:
                missing_focal += 1
                focal_vals.append(0)
            else:
                focal_vals.append(int(fp))
    unit = "share of each company's classified corpus" if use_shares else "classified pages"
    caption = (
        f"Buying-intent ownership: {comp} vs {focal} by {unit}, across "
        f"{len(ceps)} category entry points."
    )
    if missing_focal:
        caption += f" ({missing_focal} intent(s) had no {focal} mirror — shown as 0)."
    caption += " Computed from this run's stored category_entry_points."
    return {
        "chart_type": "cep_ownership",
        "type": "grouped_bar",
        "title": f"Buying-intent ownership — {comp} vs {focal}",
        "caption": caption,
        "data": {
            "groups": groups,
            "series": [
                {"name": comp, "values": comp_vals},
                {"name": focal, "values": focal_vals},
            ],
        },
        # CEP rows are corpus-level aggregates with no single backing artifact.
        "citations": [],
        "source_note": (
            f"Computed from {len(ceps)} category entry points derived from this run's "
            "classified corpus."
        ),
    }


def build_proof_gaps(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Table: each repeated competitor claim — attackability and proof strength
    on BOTH sides (competitor vs focal), with the missing proof types."""
    gaps = pkg.get("proof_gaps", []) or []
    if not gaps:
        return _err("empty_data", "No repeated claim with a proof gap was found this run.")
    comp, focal = _competitor_name(pkg), _focal_name(pkg)
    limit = _int_param(params, "limit", default=len(gaps), lo=1, hi=100)
    columns = ["Their claim", "Attackability", f"{comp} proof", f"{focal} proof", "Missing proof"]
    rows: list[list[str]] = []
    ev_by_id = {e.get("evidence_id"): e for e in (pkg.get("evidence", []) or [])}
    urls = _artifact_urls(pkg)
    cited_ids: list[str] = []
    for g in gaps[:limit]:
        claim = g.get("short_label") or (g.get("claim_text") or "")[:90] or "—"
        missing = ", ".join(g.get("missing_proof") or []) or "—"
        rows.append(
            [
                str(claim),
                str(g.get("attackability") or "—"),
                str(g.get("proof_strength") or "none"),
                str(g.get("focal_proof_strength") or "none"),
                missing,
            ]
        )
        ev = ev_by_id.get(g.get("strongest_proof_id"))
        if ev and ev.get("artifact_id"):
            cited_ids.append(ev["artifact_id"])
    return {
        "chart_type": "proof_gaps",
        "type": "table",
        "title": f"Message–proof gaps — {comp} claims vs {focal}",
        "caption": (
            f"For each repeated {comp} claim: how attackable it is and how strongly "
            f"{comp} vs {focal} proves it ({len(rows)} of {len(gaps)} gaps). Computed "
            "from this run's stored proof gaps."
        ),
        "data": {"columns": columns, "rows": rows},
        "citations": _cite(urls, cited_ids),
        "source_note": (
            f"Computed from {len(gaps)} message–proof gaps derived from this run's "
            "classified corpus."
        ),
    }


def build_temporal_changes(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Table: verified strategy changes — prior state -> current state, with the
    confidence and lifecycle of each signal."""
    changes = pkg.get("change_events", []) or []
    if not changes:
        return _err("empty_data", "No verified change over the lookback window this run.")
    limit = _int_param(params, "limit", default=len(changes), lo=1, hi=100)
    columns = ["Dimension", "Prior state", "Current state", "Confidence", "Lifecycle"]
    rows: list[list[str]] = []
    urls = _artifact_urls(pkg)
    cited_ids: list[str] = []
    for c in changes[:limit]:
        rows.append(
            [
                str(c.get("dimension") or "change").replace("_", " "),
                str(c.get("prior_state") or "—"),
                str(c.get("current_state") or "—"),
                str(c.get("confidence") or "—"),
                str(c.get("lifecycle") or "—"),
            ]
        )
        cited_ids.extend(c.get("current_evidence_ids") or [])
    return {
        "chart_type": "temporal_changes",
        "type": "table",
        "title": f"Strategy changes over time — {_competitor_name(pkg)}",
        "caption": (
            f"Verified prior→current strategy changes with confidence and lifecycle "
            f"({len(rows)} of {len(changes)} events). Computed from this run's stored "
            "change events."
        ),
        "data": {"columns": columns, "rows": rows},
        "citations": _cite(urls, cited_ids),
        "source_note": (
            f"Computed from {len(changes)} verified change events over this run's lookback window."
        ),
    }


def build_persona_channel(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Heatmap: personas x channels coverage from the run's persona/channel
    matrix (cell = classified pages reaching that persona on that channel)."""
    m = pkg.get("persona_channel_matrix", {}) or {}
    personas = list(m.get("personas") or [])
    channels = list(m.get("channels") or [])
    cells = m.get("cells") or {}
    if not personas or not channels:
        return _err("empty_data", "No persona×channel matrix was built for this run.")
    total = sum(int((cells.get(p, {}) or {}).get(c, 0) or 0) for p in personas for c in channels)
    if total == 0:
        return _err("empty_data", "The persona×channel matrix has no populated cells this run.")
    return {
        "chart_type": "persona_channel",
        "type": "heatmap",
        "title": f"Persona × channel coverage — {_competitor_name(pkg)}",
        "caption": (
            f"Classified pages reaching each persona on each channel "
            f"({len(personas)} personas × {len(channels)} channels; {total} page-tags). "
            "Computed from this run's persona×channel matrix."
        ),
        "data": {"rows": personas, "cols": channels, "cells": cells},
        "citations": [],
        "source_note": (
            f"Computed from the run's persona×channel matrix ({total} persona/channel "
            "page-tags across the classified corpus)."
        ),
    }


def build_product_verticals(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Bar: classified pages per product vertical the competitor markets."""
    pva = pkg.get("product_vertical_analysis", {}) or {}
    verts = pva.get("verticals") or []
    if not verts:
        return _err("empty_data", "No product-vertical signal matched this run's corpus.")
    total = sum(int(v.get("n_artifacts") or 0) for v in verts)
    rows = [
        {
            "label": str(v.get("vertical", "")).replace("_", " "),
            "value": int(v.get("n_artifacts") or 0),
            **({"share": round(int(v.get("n_artifacts") or 0) / total, 4)} if total else {}),
        }
        for v in verts
    ]
    return {
        "chart_type": "product_verticals",
        "type": "bar",
        "title": f"Product verticals by page count — {_competitor_name(pkg)}",
        "caption": (
            f"Classified pages mapped to each of {len(verts)} product verticals "
            f"({total} mapped pages total). Computed from this run's product-vertical "
            "analysis."
        ),
        "data": rows,
        # Verticals are corpus aggregates (per-artifact mapping is in the JSON).
        "citations": [],
        "source_note": (
            f"Computed from {total} vertical-mapped pages across {len(verts)} verticals "
            "in this run's corpus."
        ),
    }


def build_ad_creatives(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Table: real public ad-library creatives captured this run (advertiser,
    format, headline — incl. video-ad headline where present — CTA, run dates,
    landing URL). Never spend/performance; that is not in the corpus."""
    requested = params.get("source_type")
    if isinstance(requested, str) and requested in _AD_SOURCE_TYPES:
        wanted: tuple[str, ...] = (requested,)
    else:
        wanted = ("google_ads",)  # default: Google Ads transparency creatives
    ads = [a for a in (pkg.get("artifacts", []) or []) if a.get("source_type") in wanted]
    if not ads:
        label = " / ".join(wanted)
        return _err("empty_data", f"No {label} ad-library creatives were captured this run.")
    columns = ["Advertiser", "Format", "Headline", "CTA", "Run dates", "Landing URL"]
    rows: list[list[str]] = []
    citations: list[dict[str, str]] = []
    for a in ads:
        md = a.get("metadata") or {}
        first, last = md.get("first_observed") or "", md.get("last_observed") or ""
        dates = f"{first} → {last}" if (first or last) else "—"
        rows.append(
            [
                str(md.get("advertiser") or a.get("author") or "—"),
                str(md.get("format") or "—").replace("_", " "),
                str(md.get("headline") or "—"),
                str(md.get("cta") or "—"),
                dates,
                str(md.get("landing_url") or a.get("final_url") or a.get("url") or "—"),
            ]
        )
        if a.get("artifact_id") and len(citations) < _MAX_CITATIONS:
            citations.append(
                {
                    "artifact_id": a["artifact_id"],
                    "url": a.get("url") or md.get("landing_url") or "",
                }
            )
    return {
        "chart_type": "ad_creatives",
        "type": "table",
        "title": f"Public ad creatives — {_competitor_name(pkg)}",
        "caption": (
            f"{len(rows)} public ad-library creative(s) captured this run (advertiser, "
            "format, headline, CTA, observed run dates). Transparency-center records "
            "only — no spend or performance is exposed."
        ),
        "data": {"columns": columns, "rows": rows},
        "citations": citations,
        "source_note": (
            f"Computed from {len(rows)} public ad-library creative(s) captured in this "
            "run (transparency-center records; spend/performance not exposed)."
        ),
    }


def build_group_by(pkg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Bar: count classifications grouped by ONE whitelisted field. List-valued
    fields (personas, proof_types, ...) count each tag; scalar fields
    (primary_theme, competitive_stance) count each page once."""
    field = (params or {}).get("field")
    if field not in GROUP_BY_FIELDS:
        return _err(
            "invalid_group_by_field",
            f"group_by field {field!r} is not allowed. Allowed fields: "
            + ", ".join(GROUP_BY_FIELDS)
            + ".",
        )
    cls = _competitor_classifications(pkg)
    counts: Counter[str] = Counter()
    pages_with_field: list[dict[str, Any]] = []
    for c in cls:
        v = c.get(field)
        if v in (None, "", []):
            continue
        pages_with_field.append(c)
        if isinstance(v, list):
            for item in v:
                if item not in (None, ""):
                    counts[str(item)] += 1
        else:
            counts[str(v)] += 1
    if not counts:
        return _err("empty_data", f"No classified page carries the {field!r} field this run.")
    top_n = _int_param(params, "top_n", default=15, lo=1, hi=50)
    total_tags = sum(counts.values())
    rows = [
        {"label": str(lbl).replace("_", " "), "value": n, "share": round(n / total_tags, 4)}
        for lbl, n in counts.most_common(top_n)
    ]
    comp = _competitor_name(pkg)
    urls = _artifact_urls(pkg)
    field_label = field.replace("_", " ")
    return {
        "chart_type": "group_by",
        "type": "bar",
        "title": f"{comp} — classifications by {field_label}",
        "caption": (
            f"{comp}'s classified pages grouped by {field_label} "
            f"({total_tags} tags across {len(pages_with_field)} pages; top "
            f"{len(rows)} of {len(counts)} values). Computed from this run's stored "
            "classifications."
        ),
        "data": rows,
        "citations": _cite(urls, (c.get("artifact_id") for c in pages_with_field)),
        "source_note": (
            f"Computed by grouping {len(pages_with_field)} classified pages on "
            f"'{field}' ({total_tags} tags) in this run's corpus."
        ),
    }


# --------------------------------------------------------------------------- #
# registry + dispatch                                                          #
# --------------------------------------------------------------------------- #
VIZ_BUILDERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "theme_distribution": build_theme_distribution,
    "cep_ownership": build_cep_ownership,
    "proof_gaps": build_proof_gaps,
    "temporal_changes": build_temporal_changes,
    "persona_channel": build_persona_channel,
    "product_verticals": build_product_verticals,
    "ad_creatives": build_ad_creatives,
    "group_by": build_group_by,
}

# The chart-type whitelist IS the registry keys — the sole source of truth the
# chat system prompt advertises and build_visualization enforces.
SUPPORTED_CHART_TYPES: tuple[str, ...] = tuple(VIZ_BUILDERS.keys())


def build_visualization(
    pkg: dict[str, Any], chart_type: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Dispatch a rendered spec for ``chart_type`` computed from ``pkg``.

    Returns a rendered spec, or a typed ``{"error", "error_type"}`` for an
    unknown chart type, a rejected group_by field, empty data, or a builder
    fault. NEVER returns fabricated data.
    """
    builder = VIZ_BUILDERS.get(chart_type)
    if builder is None:
        return _err(
            "unknown_chart_type",
            f"unknown chart_type {chart_type!r}; supported: "
            + ", ".join(SUPPORTED_CHART_TYPES)
            + ".",
        )
    try:
        return builder(pkg, params or {})
    except Exception as exc:  # a builder fault must never crash the chat surface
        return _err("builder_error", f"failed to build {chart_type!r}: {type(exc).__name__}")
