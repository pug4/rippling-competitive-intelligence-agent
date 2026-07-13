"""On-demand PRODUCT FOCUS lens for a completed run.

The user story: "I put in Vanta; I want the analysis scoped to the ONE
Rippling product it competes with — compliance — with a very detailed
product-marketing comparison for that category, not whole-company noise."

Everything the model sees is deterministic and citation-bearing:

- Candidate categories are ranked by the COMPETITOR's mapped page count from
  the run's keyword-derived ``product_vertical_analysis`` (the top candidate
  is the auto-resolution, e.g. Vanta -> ``compliance_legal``).
- In-category scoping is STRICT: only artifacts the deterministic mapper
  tagged with the vertical (``product_vertical_analysis.by_artifact``) feed
  the prompt blocks — a page outside the category never leaks in.
- The focal side comes from the focal MIRROR run's own rendered ``data.json``
  (resolved via the package's ``focal_evidence.run_id``, falling back to the
  parent state's ``focal_run_id`` in the DB — exemplar: ``paid_search``).
  A missing mirror degrades honestly: the focal side is ``None``/UNKNOWN,
  never a fabricated zero.

One schema-forced tier2 call drafts the report; hard guards the model cannot
override run after validation: every ``supporting_quote`` is
containment-checked against the evidence text actually supplied — an
unverifiable quote marks the item ``quote_verified: false`` and flags it
"treat as unverified" (never silently kept as fact). No market share, revenue,
size, or win-rate numbers exist anywhere in the pipeline — they are not
publicly observable.

Results are cached at ``outputs/runs/<run_id>/product_focus_<vertical>.json``
so repeat views never re-spend model budget (``force=True`` regenerates).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .config import get_config, get_settings
from .schemas.common import utcnow
from .schemas.product_focus import ProductFocusReport

TASK_NAME = "product_focus"

SYSTEM = (
    "You are a rigorous product marketing strategist. Ground every item in the "
    "observed in-category evidence supplied; never state or imply market share, "
    "revenue, customer counts, or win rates; state absence of evidence as "
    "absence; return only the structured report."
)

# Exact flag prepended to an item's basis when its quote fails containment.
_UNVERIFIED_FLAG = "treat as unverified — quote not found in the observed evidence"

# Bounded number of verbatim in-category message examples per side (spec: 4-6).
_MAX_MESSAGE_EXAMPLES = 6

# The prompt-block keys whose concatenation is the quote-containment evidence.
_EVIDENCE_KEYS = (
    "competitor_in_category",
    "focal_in_category",
    "category_entry_points_in_category",
    "proof_comparison",
)


def _run_dir(run_id: str) -> Path:
    return Path(get_settings().outputs_dir) / "runs" / run_id


def _cache_path(run_id: str, vertical: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", vertical)
    return _run_dir(run_id) / f"product_focus_{safe}.json"


def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


# ---------------------------------------------------------------------------
# deterministic scoping helpers
# ---------------------------------------------------------------------------


def _vertical_rows(pkg: dict[str, Any]) -> list[dict[str, Any]]:
    return list((pkg.get("product_vertical_analysis") or {}).get("verticals") or [])


def _vertical_row(pkg: dict[str, Any], vertical: str) -> dict[str, Any] | None:
    for row in _vertical_rows(pkg):
        if row.get("vertical") == vertical:
            return row
    return None


def _by_artifact(pkg: dict[str, Any]) -> dict[str, list[str]]:
    return dict((pkg.get("product_vertical_analysis") or {}).get("by_artifact") or {})


def _mapped_total(pkg: dict[str, Any]) -> int:
    """Denominator for shares: distinct artifacts the mapper tagged with ANY
    vertical (an artifact can carry several verticals, so per-vertical shares
    deliberately do not sum to 1 — disclosed in the method note)."""
    by_artifact = _by_artifact(pkg)
    if by_artifact:
        return len(by_artifact)
    return sum(int(r.get("n_artifacts") or 0) for r in _vertical_rows(pkg))


def _in_vertical_classifications(pkg: dict[str, Any], vertical: str) -> list[dict[str, Any]]:
    """STRICT vertical filter (same idiom as ``chat.scope_to_vertical``): only
    classifications whose artifact the deterministic mapper tagged with this
    vertical — nothing outside the category ever leaks into the blocks."""
    allowed = {aid for aid, verts in _by_artifact(pkg).items() if vertical in (verts or [])}
    return [c for c in pkg.get("classifications") or [] if c.get("artifact_id") in allowed]


def _artifact_urls(pkg: dict[str, Any]) -> dict[str, str]:
    return {
        str(a.get("artifact_id")): str(a.get("final_url") or a.get("url") or "")
        for a in pkg.get("artifacts") or []
    }


# ---------------------------------------------------------------------------
# focal mirror
# ---------------------------------------------------------------------------


def _focal_mirror_pkg(run_id: str, pkg: dict[str, Any]) -> dict[str, Any] | None:
    """The focal mirror run's own rendered ``data.json``, or None (honest
    degrade — the report then says the mirror wasn't rendered).

    The mirror id comes from the package's own record (``focal_evidence.run_id``
    — exact parity with what the report used), falling back to the parent
    state's ``focal_run_id`` read via ``json_extract`` from ``runs.state_json``
    (exemplar: ``paid_search._fmt_focal_proof``).
    """
    focal_run_id = (pkg.get("focal_evidence") or {}).get("run_id")
    if not focal_run_id and run_id:
        try:
            from .storage.repository import Repository

            repo = Repository.open(get_settings().db_path)
            row = repo.conn.execute(
                "SELECT json_extract(state_json, '$.focal_run_id') FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            focal_run_id = row[0] if row else None
        except Exception:
            focal_run_id = None
    if not focal_run_id:
        return None
    path = _run_dir(str(focal_run_id)) / "data.json"
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def _focal_products_for_vertical(mirror_pkg: dict[str, Any], vertical: str) -> list[str]:
    """Focal product NAMES for the vertical: the mirror's ``product_positioning``
    rows whose themes intersect the mirror vertical row's top themes — simple
    and deterministic (no model judgment)."""
    row = _vertical_row(mirror_pkg, vertical)
    top_themes = set(row.get("top_themes") or []) if row else set()
    if not top_themes:
        return []
    scored: list[tuple[int, str]] = []
    for p in mirror_pkg.get("product_positioning") or []:
        name = str(p.get("product") or "").strip()
        overlap = len(set(p.get("themes") or []) & top_themes)
        if name and overlap:
            scored.append((overlap, name))
    # Rank by theme overlap and keep the top few — platform-wide products
    # share the dominant themes with EVERY vertical, so an unranked
    # intersection returned the whole portfolio for each vertical.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [name for _, name in scored[:3]]


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def resolve_focus_candidates(pkg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank product verticals by the COMPETITOR's mapped page count; the top
    candidate is the auto-resolution ("Vanta -> compliance_legal").

    Each candidate carries the focal mirror's side for the same vertical:
    ``focal_pages`` is None when the mirror wasn't rendered (unknown, not
    zero) and 0 when the mirror rendered but maps no pages to the vertical.
    """
    rows = _vertical_rows(pkg)
    if not rows:
        return []
    total = max(1, _mapped_total(pkg))
    run_id = str((pkg.get("run") or {}).get("run_id") or "")
    mirror = _focal_mirror_pkg(run_id, pkg)
    out: list[dict[str, Any]] = []
    # Stable sort: package row order (already deterministic) breaks ties.
    for row in sorted(rows, key=lambda r: -int(r.get("n_artifacts") or 0)):
        vertical = str(row.get("vertical") or "")
        if not vertical:
            continue
        focal_pages: int | None
        focal_products: list[str]
        if mirror is None:
            focal_pages, focal_products = None, []
        else:
            mrow = _vertical_row(mirror, vertical)
            focal_pages = int(mrow.get("n_artifacts") or 0) if mrow else 0
            focal_products = _focal_products_for_vertical(mirror, vertical)
        n = int(row.get("n_artifacts") or 0)
        out.append(
            {
                "vertical": vertical,
                "competitor_pages": n,
                "competitor_share": round(n / total, 4),
                "focal_pages": focal_pages,
                "focal_products": focal_products,
            }
        )
    return out


# ---------------------------------------------------------------------------
# prompt blocks (all deterministic, all citation-bearing)
# ---------------------------------------------------------------------------


def _fmt_message_examples(cls_rows: list[dict[str, Any]], urls: dict[str, str]) -> list[str]:
    """4-6 verbatim primary_message examples from the highest-salience
    in-vertical classifications, each with its source URL."""
    ranked = sorted(
        (c for c in cls_rows if c.get("primary_message")),
        key=lambda c: -float(c.get("message_salience") or 0.0),
    )
    lines: list[str] = []
    seen: set[str] = set()
    for c in ranked:
        msg = " ".join(str(c["primary_message"]).split())
        key = _norm(msg)
        if not key or key in seen:
            continue
        seen.add(key)
        source = urls.get(str(c.get("artifact_id")), "") or "unknown"
        lines.append(f'- "{msg}" (source: {source})')
        if len(lines) >= _MAX_MESSAGE_EXAMPLES:
            break
    return lines


def _cep_counts(cls_rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for c in cls_rows:
        for cep in c.get("category_entry_points") or []:
            if cep:
                counts[str(cep)] += 1
    return counts


def _proof_counts(cls_rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for c in cls_rows:
        for p in c.get("proof_types") or []:
            if p:
                counts[str(p)] += 1
    return counts


def _fmt_side_block(
    row: dict[str, Any] | None,
    cls_rows: list[dict[str, Any]],
    urls: dict[str, str],
    extra_lines: list[str] | None = None,
) -> str:
    """One side's in-category block: page count, theme mix with counts+shares,
    stance mix, personas, in-category CEPs, verbatim message examples."""
    n = int(row.get("n_artifacts") or 0) if row else 0
    lines = [f"{n} page(s) mapped to this category."]
    if extra_lines:
        lines.extend(extra_lines)
    theme_counts: dict[str, int] = dict((row or {}).get("theme_counts") or {})
    if theme_counts:
        lines.append("Theme mix on in-category pages (count, share of in-category pages):")
        for theme, cnt in sorted(theme_counts.items(), key=lambda kv: -kv[1]):
            share = cnt / n if n else 0.0
            lines.append(f"- {theme}: {cnt} page(s) ({share:.0%})")
    stance_mix: dict[str, int] = dict((row or {}).get("stance_mix") or {})
    if stance_mix:
        lines.append(
            "Stance mix: "
            + "; ".join(f"{s}: {c}" for s, c in sorted(stance_mix.items(), key=lambda kv: -kv[1]))
        )
    personas = list((row or {}).get("personas") or [])
    if personas:
        lines.append("Personas observed in-category: " + ", ".join(personas))
    ceps = _cep_counts(cls_rows)
    if ceps:
        lines.append(
            "Buying triggers (CEPs) on in-category pages: "
            + "; ".join(f"{cep}: {c} page(s)" for cep, c in ceps.most_common(10))
        )
    examples = _fmt_message_examples(cls_rows, urls)
    if examples:
        lines.append("Verbatim in-category messages (highest salience first):")
        lines.extend(examples)
    example_urls = list((row or {}).get("example_urls") or [])
    if example_urls:
        lines.append("Example in-category pages: " + "; ".join(example_urls[:3]))
    return "\n".join(lines)


def _fmt_cep_ownership(pkg: dict[str, Any], in_category_ceps: set[str]) -> str:
    """Ownership rows from the package's CEP map, restricted to buying
    triggers that appear on in-vertical competitor classifications. Counts in
    these rows are corpus-wide (the ownership verdict is judged there) — the
    restriction is which triggers qualify, disclosed in the header line."""
    lines: list[str] = []
    for row in pkg.get("category_entry_points") or []:
        cep = row.get("cep")
        if not cep or str(cep) not in in_category_ceps:
            continue
        comp_n = row.get("competitor_pages")
        focal_n = row.get("focal_pages")
        lines.append(
            f"- {cep}: ownership={row.get('ownership')} ({row.get('ownership_basis') or 'n/a'}); "
            f"competitor {comp_n} page(s), focal {focal_n if focal_n is not None else 'n/a'} "
            "page(s) (corpus-wide counts)"
        )
    return "\n".join(lines) or "(no buying triggers observed on in-category pages)"


_MIRROR_MISSING_LINE = (
    "(focal mirror was not rendered for this run — the focal side of this "
    "category is UNKNOWN, not zero; no focal in-category evidence exists in "
    "this deliverable)"
)


def build_focus_inputs(run_id: str, pkg: dict[str, Any], vertical: str) -> dict[str, str]:
    """All prompt blocks for one vertical — deterministic, citation-bearing,
    strictly scoped to in-vertical artifacts via ``by_artifact``."""
    companies = pkg.get("companies") or []
    competitor = (companies[0].get("canonical_name") if companies else None) or "the competitor"
    focal = (
        companies[1].get("canonical_name") if len(companies) > 1 else None
    ) or "the focal company"

    comp_row = _vertical_row(pkg, vertical)
    comp_cls = _in_vertical_classifications(pkg, vertical)
    comp_urls = _artifact_urls(pkg)
    comp_total = _mapped_total(pkg)
    comp_n = int(comp_row.get("n_artifacts") or 0) if comp_row else 0

    mirror = _focal_mirror_pkg(run_id, pkg)
    focal_products: list[str] = []
    if mirror is None:
        focal_block = _MIRROR_MISSING_LINE
        focal_n: int | None = None
        focal_total: int | None = None
    else:
        focal_row = _vertical_row(mirror, vertical)
        focal_cls = _in_vertical_classifications(mirror, vertical)
        focal_products = _focal_products_for_vertical(mirror, vertical)
        focal_total = _mapped_total(mirror)
        focal_n = int(focal_row.get("n_artifacts") or 0) if focal_row else 0
        product_line = (
            ["Focal products mapped to this category: " + ", ".join(focal_products)]
            if focal_products
            else ["(no focal product names mapped to this category)"]
        )
        if focal_row is None:
            focal_block = "\n".join(
                [
                    "0 page(s) mapped to this category — the focal mirror WAS collected "
                    "and rendered, so this is an observed absence in the mapped corpus, "
                    "not missing data."
                ]
                + product_line
            )
        else:
            focal_block = _fmt_side_block(
                focal_row, focal_cls, _artifact_urls(mirror), extra_lines=product_line
            )

    proof_lines = [f"{competitor} proof types on in-category pages:"]
    comp_proof = _proof_counts(comp_cls)
    if comp_proof:
        proof_lines.extend(f"- {p}: {c} page(s)" for p, c in comp_proof.most_common(10))
    else:
        proof_lines.append("- (none observed)")
    proof_lines.append(f"{focal} proof types on in-category pages:")
    if mirror is None:
        proof_lines.append(
            "- unknown — the focal mirror was not rendered (absence of the mirror, "
            "not absence of proof)"
        )
    else:
        focal_proof = _proof_counts(_in_vertical_classifications(mirror, vertical))
        if focal_proof:
            proof_lines.extend(f"- {p}: {c} page(s)" for p, c in focal_proof.most_common(10))
        else:
            proof_lines.append("- (none observed on in-category pages)")

    if mirror is None:
        corpus_note = (
            f"In-category corpus sizes: {competitor} {comp_n} of {comp_total} mapped "
            f"page(s); {focal} side unknown — the focal mirror was not rendered for "
            "this run. Shares are normalized within each corpus; raw counts across "
            "corpora of different sizes are not directly comparable."
        )
    else:
        corpus_note = (
            f"In-category corpus sizes: {competitor} {comp_n} of {comp_total} mapped "
            f"page(s); {focal} {focal_n} of {focal_total} mapped page(s). Shares are "
            "normalized within each corpus; raw counts across corpora of different "
            "sizes are not directly comparable — compare shares, not counts."
        )

    return {
        "focal_company": focal,
        "competitor": competitor,
        "vertical": vertical,
        "focal_products": ", ".join(focal_products)
        or (
            "(focal products unknown — mirror not rendered)"
            if mirror is None
            else "(no focal products mapped to this category)"
        ),
        "competitor_in_category": _fmt_side_block(comp_row, comp_cls, comp_urls),
        "focal_in_category": focal_block,
        "category_entry_points_in_category": _fmt_cep_ownership(pkg, set(_cep_counts(comp_cls))),
        "proof_comparison": "\n".join(proof_lines),
        "corpus_note": corpus_note,
    }


# ---------------------------------------------------------------------------
# guards + generation
# ---------------------------------------------------------------------------


def _guard_item(d: dict[str, Any], blob: str) -> dict[str, Any]:
    quote = d.get("supporting_quote")
    verified = bool(quote) and _norm(str(quote)) in blob
    d["quote_verified"] = verified
    if quote and not verified:
        # The quote is not in the evidence we supplied — never silently kept
        # as fact; the item is flagged and its basis says so.
        d["basis"] = f"{_UNVERIFIED_FLAG}. {d.get('basis') or ''}".strip()
    return d


def _apply_guards(report: ProductFocusReport, evidence_blob: str) -> dict[str, Any]:
    """Deterministic post-generation guards; returns render-ready dicts with
    every ``supporting_quote`` containment-checked against the supplied
    evidence (``quote_verified`` on every item)."""
    blob = _norm(evidence_blob)
    out: dict[str, Any] = json.loads(report.model_dump_json())
    for key in ("category_narrative", "their_target_buyer", "how_focal_should_counter"):
        _guard_item(out[key], blob)
    for key in ("messaging_gaps", "detailed_opportunities", "what_not_to_claim"):
        out[key] = [_guard_item(item, blob) for item in out[key]]
    return out


def _side_stats(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "n_pages": int(row.get("n_artifacts") or 0),
        "theme_counts": dict(row.get("theme_counts") or {}),
        "stance_mix": dict(row.get("stance_mix") or {}),
        "personas": list(row.get("personas") or []),
        "example_urls": list(row.get("example_urls") or []),
    }


async def generate_product_focus(
    run_id: str,
    *,
    vertical: str | None = None,
    execution_mode: str = "live",
    force: bool = False,
) -> dict[str, Any]:
    """Product-focus report for one category of a completed run (cached)."""
    data = _run_dir(run_id) / "data.json"
    if not data.exists():
        raise KeyError(f"run not found (no data.json): {run_id}")
    pkg = json.loads(data.read_text(encoding="utf-8"))

    candidates = resolve_focus_candidates(pkg)
    if not candidates:
        raise ValueError(
            "no product verticals mapped on this run — the product-focus lens "
            "has nothing to scope to"
        )
    known = [c["vertical"] for c in candidates]
    resolved_automatically = vertical is None
    if vertical is None:
        vertical = known[0]
    elif vertical not in known:
        raise ValueError(f"unknown vertical {vertical!r} — candidates: {', '.join(known)}")

    cache = _cache_path(run_id, vertical)
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    from .model_gateway import build_gateway
    from .prompt_registry import PromptRegistry

    inputs = build_focus_inputs(run_id, pkg, vertical)
    prompt = PromptRegistry().get(TASK_NAME)
    user_content = prompt.render(**inputs)
    gateway = build_gateway(execution_mode, get_settings(), get_config())  # type: ignore[arg-type]
    result = await gateway.generate_structured(
        TASK_NAME,
        SYSTEM,
        user_content,
        ProductFocusReport,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    evidence_blob = "\n".join(inputs[k] for k in _EVIDENCE_KEYS)
    report = _apply_guards(result.output, evidence_blob)

    mirror = _focal_mirror_pkg(run_id, pkg)
    comp_stats = _side_stats(_vertical_row(pkg, vertical))
    focal_stats = _side_stats(_vertical_row(mirror, vertical)) if mirror is not None else None
    comp_total = _mapped_total(pkg)
    method_note = (
        f"Deterministic scoping: pages were mapped to '{vertical}' by the run's "
        "keyword-derived product_vertical_analysis (by_artifact) — the mapper's "
        "verdicts, not a model judgment; a page can map to several categories, so "
        "category shares do not sum to 1. In-category corpus: competitor "
        f"{comp_stats['n_pages']} of {comp_total} mapped page(s), focal "
        + (
            f"{focal_stats['n_pages']} of {_mapped_total(mirror)} mapped page(s). "
            if mirror is not None and focal_stats is not None
            else "side unknown (the focal mirror was not rendered for this run). "
        )
        + "This is a product-vs-product read for this ONE category — not a "
        "whole-company comparison. Quotes were containment-verified against the "
        "supplied evidence; unverified items are flagged, never kept as fact."
    )
    envelope: dict[str, Any] = {
        "run_id": run_id,
        "vertical": vertical,
        "resolved_automatically": resolved_automatically,
        "candidates": candidates,
        "competitor_stats": comp_stats,
        "focal_stats": focal_stats,
        "report": report,
        "generated_at": utcnow().isoformat(),
        "prompt_version": prompt.version,
        "model_id": result.model_id,
        "method_note": method_note,
    }
    cache.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return envelope
