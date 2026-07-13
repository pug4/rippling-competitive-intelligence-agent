"""On-demand paid-search targeting draft for a completed run.

The user asks "which keywords should we target from a paid-ads perspective?"
Nothing in public data answers that with volumes or CPCs (not knowable), but
the run's OBSERVED evidence — buying triggers, competitor themes and villain
wording, live ad creatives from the transparency libraries, and the focal
company's own proof — supports grounded HYPOTHESES. One bounded reasoning-tier
call drafts keyword clusters from deterministic input blocks built here; hard
guards the model cannot override are applied after validation:

- ``validate_before_spend`` is forced true on every cluster (economics must be
  validated in a keyword planner / the live auction, never asserted);
- ``legal_review_required`` is forced true for competitor-conquesting clusters;
- every ``supporting_quote`` is containment-checked against the evidence text
  actually supplied — an unverifiable quote demotes the cluster to
  ``inferred`` and caps its priority at ``low``.

Results are cached at ``outputs/runs/<run_id>/paid_search.json`` so repeat
views never re-spend model budget (``force=True`` regenerates).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import get_config, get_settings
from .schemas.common import utcnow
from .schemas.paid_search import PaidSearchTargetingDraft

TASK_NAME = "paid_search_targeting"

SYSTEM = (
    "You are a rigorous paid-search strategist. Ground every keyword cluster in "
    "the observed evidence supplied; never invent volumes, CPCs, or spend; "
    "return only the structured draft."
)

_DISCLAIMER = (
    "Search volume, CPC, competition density, and commercial ad spend are not "
    "publicly knowable — every cluster is a hypothesis to validate in Google "
    "Keyword Planner / the live auction before any spend."
)


def _run_dir(run_id: str) -> Path:
    return Path(get_settings().outputs_dir) / "runs" / run_id


def _cache_path(run_id: str) -> Path:
    return _run_dir(run_id) / "paid_search.json"


def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def _fmt_ceps(pkg: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in pkg.get("category_entry_points") or []:
        cep = row.get("cep")
        if not cep:
            continue
        comp_n = row.get("competitor_pages")
        focal_n = row.get("focal_pages")
        lines.append(
            f"- {cep}: ownership={row.get('ownership')} ({row.get('ownership_basis') or 'n/a'}); "
            f"competitor {comp_n} page(s), focal {focal_n if focal_n is not None else 'n/a'} page(s)"
        )
    return "\n".join(lines) or "(none observed)"


def _fmt_themes_and_villains(pkg: dict[str, Any]) -> str:
    tc = pkg.get("theme_comparison") or {}
    comp = tc.get("competitor_themes") or {}
    shares = tc.get("competitor_shares") or {}
    lines = ["Competitor page themes (count, share of their classified corpus):"]
    for theme, n in sorted(comp.items(), key=lambda kv: -kv[1])[:12]:
        share = shares.get(theme)
        lines.append(f"- {theme}: {n} page(s)" + (f" ({share:.0%})" if share else ""))
    dm = pkg.get("dominant_message") or {}
    if dm.get("label"):
        lines.append(f"Dominant message: {dm['label']}")
    villains: list[str] = []
    seen: set[str] = set()
    for c in pkg.get("classifications") or []:
        for wording in c.get("villain_exact_wording") or []:
            key = _norm(wording)
            if key and key not in seen:
                seen.add(key)
                villains.append(wording)
    if villains:
        lines.append("Villain wording they use verbatim (the problem they sell against):")
        lines.extend(f'- "{v}"' for v in villains[:15])
    return "\n".join(lines)


def _fmt_ad_creatives(run_id: str, competitor_domain: str) -> str:
    """Observed ad creatives from the stored artifacts (junk discovery rows
    filtered with the SAME rule the report uses — never re-implemented)."""
    from .storage.repository import Repository
    from .synthesis import is_junk_ads_artifact

    repo = Repository.open(get_settings().db_path)
    rows = repo.conn.execute(
        "SELECT url, json_extract(payload_json,'$.title') AS title, "
        "       json_extract(payload_json,'$.metadata') AS metadata, normalized_text "
        "FROM artifacts WHERE run_id = ? AND source_type LIKE '%ads%' "
        "ORDER BY created_at LIMIT 24",
        (run_id,),
    ).fetchall()
    out: list[str] = []
    for r in rows:
        try:
            metadata = json.loads(r["metadata"]) if r["metadata"] else None
        except Exception:
            metadata = None
        if is_junk_ads_artifact(r["url"] or "", metadata, competitor_domain):
            continue
        text = " ".join((r["normalized_text"] or "").split())[:400]
        if not text:
            continue
        out.append(f"- [{r['title'] or 'ad'}] {text} (source: {r['url']})")
        if len(out) >= 8:
            break
    return "\n".join(out) or "(none collected on this run)"


def _fmt_focal_proof(pkg: dict[str, Any]) -> str:
    """Focal proof by theme, counted from the focal company's classifications."""
    companies = pkg.get("companies") or []
    focal_id = companies[1].get("company_id") if len(companies) > 1 else None
    if not focal_id:
        return "(no focal mirror on this run — treat all focal proof as unverified)"
    by_theme: dict[str, dict[str, Any]] = {}
    for c in pkg.get("classifications") or []:
        if c.get("company_id") != focal_id:
            continue
        theme = c.get("primary_theme")
        if not theme:
            continue
        slot = by_theme.setdefault(theme, {"n": 0, "proof": set()})
        slot["n"] += 1
        for p in c.get("proof_types") or []:
            slot["proof"].add(p)
    lines = [
        f"- {theme}: {v['n']} page(s); proof types observed: "
        + (", ".join(sorted(v["proof"])[:6]) or "none")
        for theme, v in sorted(by_theme.items(), key=lambda kv: -kv[1]["n"])[:12]
    ]
    return "\n".join(lines) or "(no classified focal pages)"


def build_inputs(run_id: str, pkg: dict[str, Any]) -> dict[str, str]:
    companies = pkg.get("companies") or []
    competitor = companies[0].get("canonical_name") if companies else "the competitor"
    focal = companies[1].get("canonical_name") if len(companies) > 1 else "the focal company"
    competitor_domain = companies[0].get("primary_domain", "") if companies else ""
    return {
        "focal_company": focal,
        "competitor": competitor,
        "category_entry_points": _fmt_ceps(pkg),
        "competitor_themes_and_villains": _fmt_themes_and_villains(pkg),
        "ad_creatives": _fmt_ad_creatives(run_id, competitor_domain),
        "focal_proof_by_theme": _fmt_focal_proof(pkg),
    }


def _apply_guards(draft: PaidSearchTargetingDraft, evidence_blob: str) -> list[dict[str, Any]]:
    """Deterministic post-generation guards; returns render-ready dicts."""
    blob = _norm(evidence_blob)
    out: list[dict[str, Any]] = []
    for cluster in draft.clusters:
        d = json.loads(cluster.model_dump_json())
        d["validate_before_spend"] = True  # forced: economics are never observable
        if d.get("cluster_type") == "competitor_conquesting":
            d["legal_review_required"] = True  # forced: brand-bidding legal/policy risk
        quote = d.get("supporting_quote")
        verified = bool(quote) and _norm(quote) in blob
        d["quote_verified"] = verified
        if quote and not verified:
            # The quote is not in the evidence we supplied — the grounding is
            # not trustworthy, so the cluster degrades honestly instead of
            # shipping a fabricated citation.
            d["evidence_basis"] = "inferred"
            d["priority_tier"] = "low"
            d["risk_note"] = (
                "Supporting quote could not be verified against the observed "
                "evidence — treat as inferred. " + (d.get("risk_note") or "")
            ).strip()
        out.append(d)
    return out


async def generate_paid_search_targets(
    run_id: str,
    *,
    execution_mode: str = "live",
    force: bool = False,
) -> dict[str, Any]:
    """Draft paid-search keyword clusters for a completed run (cached)."""
    cache = _cache_path(run_id)
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    data = _run_dir(run_id) / "data.json"
    if not data.exists():
        raise KeyError(f"run not found (no data.json): {run_id}")
    pkg = json.loads(data.read_text(encoding="utf-8"))

    from .model_gateway import build_gateway
    from .prompt_registry import PromptRegistry

    prompt = PromptRegistry().get("paid_search_targeting")
    inputs = build_inputs(run_id, pkg)
    user_content = prompt.render(**inputs)
    gateway = build_gateway(execution_mode, get_settings(), get_config())  # type: ignore[arg-type]
    result = await gateway.generate_structured(
        TASK_NAME,
        SYSTEM,
        user_content,
        PaidSearchTargetingDraft,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    evidence_blob = "\n".join(
        inputs[k]
        for k in ("category_entry_points", "competitor_themes_and_villains", "ad_creatives")
    )
    envelope: dict[str, Any] = {
        "run_id": run_id,
        "focal_company": inputs["focal_company"],
        "competitor": inputs["competitor"],
        "generated_at": utcnow().isoformat(),
        "prompt_name": prompt.name,
        "prompt_version": prompt.version,
        "model_id": result.model_id,
        "disclaimer": _DISCLAIMER,
        "method_note": result.output.method_note,
        "clusters": _apply_guards(result.output, evidence_blob),
    }
    cache.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return envelope
