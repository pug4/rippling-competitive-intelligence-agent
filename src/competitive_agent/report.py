"""Deterministic report rendering: Markdown brief + JSON package + trace.

Both renderers consume ONLY validated stored records (state + repository) — the
React UI consumes the same JSON package, never separate logic (§40.6). The
Markdown leads with an executive summary and a Rippling-first Action Board,
labels every rating with the evidence behind it, states what the evidence shows
(never converting a proof gap into a capability claim), and collapses empty
sections into an honest one-liner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import GraphContext
from .schemas.common import utcnow
from .state import DirectorState

JSON_SCHEMA_VERSION = "1.2.0"


def run_output_dir(state: DirectorState, ctx: GraphContext) -> Path:
    base = ctx.settings.outputs_dir if ctx.settings else Path("outputs")
    d = Path(base) / "runs" / state.run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(ctx: GraphContext, state: DirectorState) -> dict[str, Any]:
    """Load every stored record for the run once, split by type."""
    repo = ctx.repository
    out: dict[str, Any] = {
        "artifacts": [],
        "artifact_models": [],
        "classifications": [],
        "classification_models": [],
        "evidence": [],
        "claims": [],
        "change_events": [],
        "opportunities": [],
        "proof_gaps": [],
    }
    if repo is None:
        return out
    for m in repo.list_artifacts(run_id=state.run_id):
        out["artifact_models"].append(m)
        a = json.loads(m.model_dump_json())
        a.pop("raw_text", None)
        if a.get("normalized_text"):
            a["normalized_text"] = a["normalized_text"][:1500]
        out["artifacts"].append(a)
    for m in repo.list_classifications(state.run_id, family="merged"):
        out["classification_models"].append(m)
        out["classifications"].append(json.loads(m.model_dump_json()))
    for m in repo.list_classifications(state.run_id, family="evidence"):
        if m.__class__.__name__ == "EvidenceItem":
            out["evidence"].append(json.loads(m.model_dump_json()))
    for m in repo.list_claims(run_id=state.run_id):
        payload = json.loads(m.model_dump_json())
        (out["change_events"] if m.__class__.__name__ == "ChangeEvent" else out["claims"]).append(
            payload
        )
    for m in repo.list_opportunities(run_id=state.run_id):
        payload = json.loads(m.model_dump_json())
        (
            out["proof_gaps"] if m.__class__.__name__ == "MessageProofGap" else out["opportunities"]
        ).append(payload)
    return out


def _focal_run_id(ctx: GraphContext, state: DirectorState) -> str | None:
    """Resolve THIS run's focal (Rippling) mirror id. Prefer the persisted
    ``state.focal_run_id`` (set when the mirror ran), then live scratch. Only as
    a last resort fall back to the most recent focal-domain run — that heuristic
    can pick up an unrelated (e.g. fixture) run, so it is genuinely last."""
    if state.focal_run_id:
        return state.focal_run_id
    if ctx.repository is None or state.focal_company is None:
        return None
    try:
        scratch = getattr(ctx, "scratch", None) or {}
        focal_run = scratch.get("focal_run_id")
        if not focal_run:
            for row in ctx.repository.list_runs(company=state.focal_company.primary_domain):
                focal_run = row["run_id"]
                break
        return focal_run
    except Exception:
        return None


def _focal_classifications(ctx: GraphContext, state: DirectorState):
    """Load the focal company's merged classifications for CEP-ownership."""
    focal_run = _focal_run_id(ctx, state)
    if not focal_run or ctx.repository is None:
        return []
    from .schemas.classification import MarketingClassification

    return [
        m
        for m in ctx.repository.list_classifications(focal_run, family="merged")
        if isinstance(m, MarketingClassification)
    ]


def _focal_evidence(ctx: GraphContext, state: DirectorState) -> dict[str, Any]:
    """Load the focal (Rippling) run's artifacts + evidence so every
    'Rippling proof: …' claim is traceable WITHIN this deliverable (QA
    finding: focal proof was asserted without in-package provenance)."""
    focal_run = _focal_run_id(ctx, state)
    out: dict[str, Any] = {"run_id": focal_run, "artifacts": [], "evidence": []}
    if not focal_run or ctx.repository is None:
        return out
    try:
        for m in ctx.repository.list_artifacts(run_id=focal_run):
            a = json.loads(m.model_dump_json())
            a.pop("raw_text", None)
            if a.get("normalized_text"):
                a["normalized_text"] = a["normalized_text"][:1500]
            out["artifacts"].append(a)
        for m in ctx.repository.list_classifications(focal_run, family="evidence"):
            if m.__class__.__name__ == "EvidenceItem":
                out["evidence"].append(json.loads(m.model_dump_json()))
    except Exception:
        pass
    return out


def _linkedin_posts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Join each LinkedIn post artifact with its merged classification so the
    brief/dashboard can showcase per-post: author, link, theme, stance, excerpt."""
    cls_by_art = {c.get("artifact_id"): c for c in data["classifications"]}
    out: list[dict[str, Any]] = []
    for a in data["artifacts"]:
        if a.get("source_type") != "linkedin_post":
            continue
        c = cls_by_art.get(a["artifact_id"], {})
        meta = a.get("metadata", {}) or {}
        out.append(
            {
                "artifact_id": a["artifact_id"],
                "author": a.get("author"),
                "author_role": meta.get("author_role"),
                "post_url": a.get("url"),
                "posted_at": a.get("published_at"),
                "theme": c.get("primary_theme") or meta.get("theme"),
                "primary_message": c.get("primary_message"),
                "competitive_stance": c.get("competitive_stance"),
                "personas": c.get("personas", []),
                "excerpt": (a.get("normalized_text") or "")[:280],
            }
        )
    return out


def _similarweb_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Pull the Similarweb (estimated) traffic block from its artifact, if any."""
    for a in data["artifacts"]:
        if a.get("source_type") == "similarweb":
            meta = a.get("metadata", {}) or {}
            return {
                "domain": meta.get("domain"),
                "data_source": meta.get("data_source", "similarweb"),
                "observation_period": meta.get("observation_period"),
                "estimated": True,
                "metrics": meta.get("metrics", {}),
                "url": a.get("url"),
            }
    return {}


def build_json_package(state: DirectorState, ctx: GraphContext) -> dict[str, Any]:
    from . import synthesis

    data = _load(ctx, state)
    dom = synthesis.dominant_message(data["classification_models"], data["artifact_models"])
    skew = synthesis.corpus_skew(data["artifact_models"])
    dist = synthesis.source_distribution(data["artifact_models"])
    coverage_detail = [
        json.loads(cd.model_dump_json())
        for cd in synthesis.coverage_details(
            state, data["artifact_models"], data["classification_models"]
        )
    ]
    motion = synthesis.commercial_motion(data["classification_models"])
    positioning = synthesis.product_positioning(data["classification_models"])
    artifact_source = {a["artifact_id"]: a["source_type"] for a in data["artifacts"]}
    matrix = synthesis.persona_channel_funnel(data["classification_models"], artifact_source)
    focal_cls_models = _focal_classifications(ctx, state)
    ceps = synthesis.category_entry_points(data["classification_models"], focal_cls_models)
    focal_evidence = _focal_evidence(ctx, state)
    linkedin_posts = _linkedin_posts(data)
    similarweb = _similarweb_summary(data)
    # Source-URL registry (traceability chain): every collected source with its
    # provenance, so a claim's evidence id -> artifact id -> URL+timestamp is
    # resolvable from the JSON alone.
    source_registry = [
        {
            "artifact_id": a["artifact_id"],
            "url": a.get("url"),
            "source_type": a["source_type"],
            "retrieved_at": a.get("retrieved_at"),
            "archive_capture_at": a.get("archive_capture_at"),
        }
        for a in data["artifacts"]
        if a.get("url")
    ]
    # Named matrices (also exposed as their own top-level keys for convenience).
    matrices = [
        {"name": "persona_channel_funnel", "matrix": matrix},
        {"name": "category_entry_point_ownership", "matrix": ceps},
    ]
    claims_list = data["claims"]
    material_total = sum(
        1 for c in claims_list if c.get("status") not in ("rejected", "contradicted")
    )
    eval_summary = {
        "n_artifacts": len(data["artifacts"]),
        "n_classifications": len(data["classifications"]),
        "n_claims": len(claims_list),
        "n_opportunities": len(data["opportunities"]),
        "n_proof_gaps": len(data["proof_gaps"]),
        "n_change_events": len(data["change_events"]),
        "material_claims_total": material_total,
        "material_claims_cited": sum(1 for c in claims_list if c.get("evidence_ids")),
        "corpus_skew_warnings": len(skew),
        "note": "In-package self-summary. Full graded benchmark: `competitive-agent eval-benchmark`.",
    }
    classified_ids = {c.get("artifact_id") for c in data["classifications"]}
    unclassified = [
        {
            "artifact_id": a["artifact_id"],
            "source_type": a["source_type"],
            "url": a["url"],
            "reason": "site map (URL list, not marketing content)"
            if a["source_type"] in ("sitemap", "robots")
            else "too little extractable text"
            if len(a.get("normalized_text", "")) < 200
            else "classification unavailable",
        }
        for a in data["artifacts"]
        if a["artifact_id"] not in classified_ids
    ]

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "run": {
            "run_id": state.run_id,
            "parent_run_id": state.parent_run_id,
            "mode": state.mode,
            "execution_mode": state.execution_mode,
            "generated_at": utcnow().isoformat(),
            "stop_reason": state.stop_reason,
            "iterations": state.iteration,
            "tool_cost_usd": round(state.spent_usd, 4),
            "model_cost_usd": round(state.model_cost_usd, 4),
            "total_spend_usd": round(state.total_spend_usd(), 4),
        },
        "scope": {
            "company_input": state.company_input,
            "compare_to": state.compare_to,
            "lookback_days": state.lookback_days,
            "user_focus": state.user_focus,
            "time_windows": [json.loads(w.model_dump_json()) for w in state.time_windows],
        },
        "companies": [
            json.loads(c.model_dump_json())
            for c in (state.company, state.focal_company)
            if c is not None
        ],
        "dominant_message": dom,
        "source_distribution": dist,
        "corpus_skew_warnings": skew,
        "coverage": state.coverage,
        "coverage_detail": coverage_detail,
        "commercial_motion": motion,
        "product_positioning": positioning,
        "category_entry_points": ceps,
        "persona_channel_matrix": matrix,
        "sources": source_registry,
        "artifacts": data["artifacts"],
        "unclassified_artifacts": unclassified,
        "evidence": data["evidence"],
        # Focal (Rippling) mirror evidence — so every "Rippling proof: …" claim is
        # traceable within this deliverable, not just in the sibling mirror run.
        "focal_evidence": focal_evidence,
        # Competitor LinkedIn employee posts (one per post) + Similarweb traffic.
        "linkedin_posts": linkedin_posts,
        "similarweb": similarweb,
        "classifications": data["classifications"],
        "claims": data["claims"],
        # product_portfolios/launches require the deep §38 product-entity loop
        # (documented as deferred in IMPLEMENTATION_STATUS.md); product intel is
        # exposed via product_positioning + category_entry_points today.
        "product_portfolios": [],
        "launches": [],
        "change_events": data["change_events"],
        "matrices": matrices,
        "proof_gaps": data["proof_gaps"],
        "opportunities": data["opportunities"],
        "opportunities_rejected": (
            (getattr(ctx, "scratch", None) or {}).get("opportunities_rejected", [])
        ),
        "limitations": state.limitations,
        "negative_observations": state.negative_observations,
        "tool_failures": [json.loads(f.model_dump_json()) for f in state.failed_actions.values()],
        "trace_summary": {"tool_calls": state.tool_calls_made},
        "eval_summary": eval_summary,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _badge(level: str, n: int | None = None, sources: list[str] | None = None) -> str:
    bits = [level]
    if n is not None:
        bits.append(f"{n} artifacts")
    if sources:
        bits.append(" + ".join(sources[:3]))
    return "[" + " · ".join(bits) + "]"


def render_markdown(state: DirectorState, pkg: dict[str, Any]) -> str:
    company = state.company.canonical_name if state.company else state.company_input
    focal = state.focal_company.canonical_name if state.focal_company else "Rippling"
    L: list[str] = []
    add = L.append

    add(f"# Competitive Marketing Intelligence Brief — {company}")
    add("")
    add(
        f"*Run `{state.run_id}` · mode `{state.mode}` · execution `{state.execution_mode}` · "
        f"generated {pkg['run']['generated_at']}*"
    )
    if state.execution_mode == "fixture":
        add("")
        add("> **Fixture mode — synthetic, deterministic data.** Not live public evidence.")

    changes = pkg["change_events"]
    gaps = pkg["proof_gaps"]
    opps = pkg["opportunities"]
    cls = pkg["classifications"]
    dom = pkg["dominant_message"]

    # --- Executive summary (feedback #27) -----------------------------------
    add("\n## Executive summary\n")
    if dom.get("theme"):
        label = (
            "Dominant company message"
            if dom.get("is_company_level")
            else "Most prominent message in the collected corpus"
        )
        add(f"- **{label}:** {dom.get('label')} ({dom['reason']}).")
    add(f"- **Product-positioning read:** {_positioning_oneliner(cls, company)}")
    if changes:
        # Prefer the highest-confidence change and label it by its ACTUAL
        # confidence/lifecycle — an emerging, low-confidence, absence-based signal
        # is not a "confirmed change" (a both-periods high/medium change is).
        rank = {"high": 3, "medium": 2, "low": 1}
        top = max(changes, key=lambda c: rank.get(str(c.get("confidence", "low")), 0))
        conf = str(top.get("confidence", "low"))
        emerging = str(top.get("lifecycle", "")) == "emerging"
        if conf in ("high", "medium") and not emerging:
            label = "Confirmed change"
        else:
            label = f"Emerging signal ({conf} confidence, needs a prior-window baseline)"
        add(f"- **{label}:** {top['dimension']} — {top['prior_state']} → {top['current_state']}.")
    else:
        add(
            "- **Change over time:** none met the both-period evidence bar this run (see Strategy-over-time)."
        )
    if opps:
        # "Top-ranked" reflects the engine's overall ordering (defensibility is
        # one input, not the only one); don't imply it has the strongest proof
        # when it may not (QA finding #6).
        add(f"- **Top-ranked {focal} opening:** {opps[0]['title']}.")
    add(f"- **Largest uncertainty:** {_largest_uncertainty(pkg)}.")

    # --- Action Board — Rippling-first (feedback #28) -----------------------
    add("\n## Action Board\n")
    add(f"### What {focal} should do")
    if opps:
        for o in opps[:3]:
            add(
                f"- **{o['title']}** `{o.get('action_category', o['deliverable_type'])}` "
                f"{_badge(o['structural_defensibility'])}"
            )
            add(f"  - Why now: {o['message_angle']}")
            comp = o.get("product_comparability") or {}
            if comp:
                add(
                    f"  - Product comparability: **{comp.get('comparability', 'weak')}** "
                    f"({comp.get('competitor_product', '?')} vs {comp.get('focal_product', '?')})"
                )
            add(
                f"  - {focal} proof: {o['focal_proof_status']} · already-saying-it: {o['focal_current_usage']} "
                f"· legal review: {o['legal_review_required']}"
            )
            add(f"  - Risk (backfire): {o['why_this_could_backfire']}")
            if o.get("campaign_plan"):
                cp = o["campaign_plan"]
                add(
                    f"  - Campaign: audience {', '.join(cp.get('audience', [])[:3]) or '—'}; "
                    f"channels {', '.join(cp.get('channels', [])[:3]) or '—'}; metric {cp.get('primary_metric', '—')}"
                )
            stages = o.get("staged_plan") or []
            if stages:
                add(
                    f"  - Staged: {' → '.join(s['stage'] for s in stages)} (each with its own proceed/stop rule)"
                )
            add(f"  - Kill rule: {o['kill_rule']}")
    else:
        add("- _No opportunity survived the genericness / superiority critics this run._")
        for r in pkg.get("opportunities_rejected", [])[:3]:
            add(f"  - rejected: {r}")

    add(f"\n### Strongest message–proof gaps (competitor vs {focal})")
    if gaps:
        add("| Gap | Competitor proof | " + focal + " proof | Stance | Specificity |")
        add("|---|---|---|---|---|")
        for g in gaps[:5]:
            label = g.get("short_label") or g["claim_text"][:40]
            stance = (g.get("attackability_detail") or {}).get("overall", g["attackability"])
            add(
                f"| {label} | {g['proof_strength']} | {g.get('focal_proof_strength', 'n/a')} | "
                f"{stance} | {g.get('claim_specificity', 'unknown')} |"
            )
        add("")
        add("_Proof distributions and the exact repeated claims are in the Evidence appendix._")
        add(
            f"\n_Rating rubric: proof 'none' = no third-party/quantified proof observed (a feature "
            f"assertion or demo alone doesn't count); 'weak' = assertions/demos only; 'moderate' = "
            f"some named-customer or partial quantified proof; 'strong' = repeated quantified/"
            f"third-party proof. '{focal} proof: missing/partial/available' rates {focal}'s own "
            f"publishable proof for the equivalent claim._"
        )
    else:
        add("- _No repeated competitor theme with a proof gap was observed in this corpus._")

    add("\n### What not to attack")
    concede = [
        g
        for g in gaps
        if (g.get("attackability_detail") or {}).get("overall") in ("reframe", "concede")
    ]
    # Dedup by theme label — the gap engine can emit several records for one
    # theme; "don't attack consolidation" should be said once.
    seen_labels: set[str] = set()
    concede_unique = []
    for g in concede:
        label = str(g.get("short_label", ""))
        if label in seen_labels:
            continue
        seen_labels.add(label)
        concede_unique.append(g)
    if concede_unique:
        for g in concede_unique[:2]:
            add(f"- **{g.get('short_label')}** — {g['actionable_interpretation']}")
    else:
        add("- _No theme reached the 'reframe/concede' bar; none flagged as off-limits._")

    # --- Current positioning (feedback #1, #23, #24) -----------------------
    add("\n## Current public positioning\n")
    if dom.get("theme"):
        label = (
            "Dominant company message"
            if dom.get("is_company_level")
            else "Most prominent in the collected corpus"
        )
        add(f"- **{label}:** {dom.get('label')}")
        add(
            f"  - Basis: surfaces {', '.join(s for s in dom.get('surfaces', []) if s and s != 'None')}; "
            f"source classes {', '.join(dom.get('source_classes', []))}"
        )
    themes = _theme_counts(cls)
    if themes:
        add("- **Themes observed:** " + ", ".join(f"{t} ({n})" for t, n in themes[:8]))
    villains = _villain_wording(cls)
    if villains["exact"]:
        add("- **Villain / status-quo wording (exact):**")
        for w in villains["exact"][:5]:
            add(f"  - “{w}”")
    if villains["normalized"]:
        add(f"  - normalized: {', '.join(sorted(villains['normalized']))}")
    stance = _stance_distribution(cls)
    if stance:
        add("- **Competitive stance distribution:**")
        add("  | Stance | Artifacts |")
        add("  |---|---:|")
        for s, n in stance:
            add(f"  | {s} | {n} |")

    # --- Commercial motion (feedback #20) ----------------------------------
    m = pkg.get("commercial_motion", {})
    if m and m.get("primary_motion") != "unclear":
        add(
            f"\n## Commercial motion ({m.get('confidence', 'low')} confidence — {m.get('basis', '')})\n"
        )
        add(
            f"- **Inferred motion:** {m['primary_motion']} · **pricing disclosure:** {m.get('pricing_disclosure')}"
        )
        ctas = m.get("dominant_ctas") or {}
        if ctas:
            add(
                "- **Dominant CTAs (share of observed):** "
                + ", ".join(f"{k} {v}" for k, v in ctas.items())
            )
        seg = m.get("segment_focus") or {}
        if seg:
            add(
                "- **Apparent segment focus (by mentions):** "
                + ", ".join(f"{k} ({v})" for k, v in seg.items())
            )
        add("- _Public-signal inference only — not CAC, conversion, or spend._")

    # --- Product positioning (feedback #18) --------------------------------
    positioning = pkg.get("product_positioning") or []
    if positioning:
        add("\n## Product positioning\n")
        add("| Product | Pages | Themes | Personas | Proof |")
        add("|---|---:|---|---|---|")
        for p in positioning[:10]:
            add(
                f"| {p['product'][:28]} | {p['pages']} | {', '.join(p['themes'][:2]) or '—'} | "
                f"{', '.join(p['personas'][:2]) or '—'} | {', '.join(p['proof_types'][:2]) or '—'} |"
            )

    # --- Category entry points (feedback #22) ------------------------------
    ceps = pkg.get("category_entry_points") or []
    if ceps:
        add(f"\n## Category entry points ({company} vs {focal})\n")
        add("| Buying trigger | Competitor | " + focal + " | Ownership |")
        add("|---|---:|---:|---|")
        for r in ceps[:10]:
            add(f"| {r['cep']} | {r['competitor_pages']} | {r['focal_pages']} | {r['ownership']} |")

    # --- Persona × channel matrix (feedback #21) ---------------------------
    mtx = pkg.get("persona_channel_matrix") or {}
    if mtx.get("personas") and mtx.get("channels"):
        add("\n## Persona × channel coverage (observed)\n")
        add(
            "_Cells are observed-page counts; an empty cell is **not observed**, not proof of absence._\n"
        )
        channels = mtx["channels"][:6]
        add("| Persona | " + " | ".join(channels) + " |")
        add("|---|" + "|".join("---:" for _ in channels) + "|")
        for persona in mtx["personas"][:8]:
            cells = mtx["cells"].get(persona, {})
            add(f"| {persona} | " + " | ".join(str(cells.get(ch, "")) for ch in channels) + " |")

    # --- Competitor LinkedIn employee posts --------------------------------
    posts = pkg.get("linkedin_posts") or []
    add(f"\n## {company} LinkedIn employee posts ({len(posts)})\n")
    if posts:
        add(
            "Individual public posts (Exa-extracted text + real post link), classified. "
            "Click a link to review the post on LinkedIn.\n"
        )
        add("| Author | Theme | Stance | Post | Excerpt |")
        add("|---|---|---|---|---|")
        for p in posts[:15]:
            who = p.get("author") or "?"
            if p.get("author_role"):
                who += f" ({p['author_role']})"
            excerpt = (p.get("excerpt") or "").replace("\n", " ")[:90]
            add(
                f"| {who} | {p.get('theme') or '—'} | {p.get('competitive_stance') or '—'} | "
                f"[link]({p.get('post_url')}) | {excerpt} |"
            )
    else:
        add(
            "_No individual LinkedIn posts were collected this run (needs Exa credits + the "
            "`exa_linkedin` source enabled)._"
        )

    # --- Traffic & channel mix (Similarweb, estimated) ---------------------
    sw = pkg.get("similarweb") or {}
    if sw.get("metrics"):
        label = "Similarweb" if sw.get("data_source") == "similarweb" else "public-web estimate"
        add(f"\n## Traffic & channel mix — {company} ({label}, estimated)\n")
        m = sw["metrics"]
        for key in (
            "estimated_monthly_visits",
            "channel_mix",
            "top_countries",
            "digital_competitors",
        ):
            if key in m:
                val = m[key].get("value") if isinstance(m[key], dict) else m[key]
                add(f"- **{key.replace('_', ' ')}:** {val} _(estimated)_")

    # --- Strategy over time (feedback #25) ---------------------------------
    add("\n## Strategy over time\n")
    if changes:
        for ch in changes[:4]:
            add(
                f"- **{ch['dimension']}** ({ch['lifecycle']}, confidence {ch['confidence']}): "
                f"“{ch['prior_state']}” → “{ch['current_state']}”"
            )
            if ch.get("alternative_explanations"):
                add(f"  - alternatives: {'; '.join(ch['alternative_explanations'][:2])}")
    else:
        snaps = [a for a in pkg["artifacts"] if a["source_type"] == "wayback"]
        snap_dates = sorted(
            {
                str(a.get("archive_capture_at") or a.get("published_at") or "")[:10]
                for a in snaps
                if a.get("archive_capture_at") or a.get("published_at")
            }
        )
        depth = "insufficient_history" if len(snaps) < 3 else "partial"
        add(f"**No confirmed strategic change** (historical support: `{depth}`).")
        add(
            f"- Compared {len(snaps)} archived snapshot(s)"
            + (f" from {', '.join(snap_dates)}" if snap_dates else "")
            + " against current pages; the normalized theme did not differ enough to clear the "
            "both-period bar."
        )
        add(
            "- Pages compared: "
            + (
                ", ".join(
                    sorted(
                        {a.get("metadata", {}).get("original_url", a["url"])[:50] for a in snaps}
                    )
                )
                or "homepage only"
            )
        )
        if len(snaps) < 3:
            add(
                "- **Caveat:** this is too few historical observations to confirm OR rule out a trend. "
                "A deeper archive sample (more snapshots across the window on platform/pricing pages) is needed."
            )
        add(
            "- Unresolved hypotheses a deeper sample could test: whether enterprise/platform language is "
            "increasing; whether API/automation language is broadening."
        )

    # --- Scope, coverage, sources (feedback #7, #8, #9, #36) ---------------
    add("\n## Research scope and coverage\n")
    add(
        f"- Competitor **{company}** · focal **{focal}** · lookback {state.lookback_days}d across {len(state.time_windows)} windows"
    )
    add(
        f"- Artifacts collected: {len(pkg['artifacts'])} · classified: {len(cls)} · claims: {len(pkg['claims'])}"
    )
    if pkg["corpus_skew_warnings"]:
        add("- **Corpus-skew warnings:**")
        for w in pkg["corpus_skew_warnings"]:
            add(f"  - {w}")
    add("\n**Source distribution**\n")
    add("| Source | Count |")
    add("|---|---:|")
    for label, n in sorted(pkg["source_distribution"].items(), key=lambda kv: -kv[1]):
        add(f"| {label} | {n} |")
    if pkg["unclassified_artifacts"]:
        add("\n**Not classified**\n")
        for u in pkg["unclassified_artifacts"]:
            add(f"- {u['source_type']} ({u['url'][:60]}) — {u['reason']}")
    add("\n**Coverage (what each rating is based on)**\n")
    add("| Dimension | Level | Basis |")
    add("|---|---|---|")
    for cd in pkg["coverage_detail"]:
        add(f"| {cd['dimension']} | {cd['level']} | {cd['reason']} |")

    # --- Limitations (feedback #32) ----------------------------------------
    add("\n## Limitations and missing data\n")
    if state.limitations:
        for item in state.limitations:
            add(f"- {item}")
    else:
        add("- No blocking limitations recorded.")
    add(
        "- Not publicly knowable (excluded): actual ad performance/ROAS, exact spend, negotiated pricing, "
        "complete OOH/employee-post coverage, internal intent."
    )

    # --- Evidence appendix (feedback #10, #31) -----------------------------
    add("\n## Evidence appendix\n")
    focal_ev = pkg.get("focal_evidence") or {}
    focal_arts = focal_ev.get("artifacts") or []
    n_comp, n_focal = len(pkg["artifacts"]), len(focal_arts)
    add(
        f"All {n_comp} competitor ({company}) sources below; all {n_focal} focal ({focal}) "
        f"mirror sources follow so every '{focal} proof: …' rating is traceable within this "
        "deliverable. Claims resolve to evidence IDs (see JSON `claims[].evidence_ids`, "
        "`focal_evidence`). Note: `normalized_text` in the JSON is truncated to 1,500 chars "
        "per artifact for size; full text lives in the run store.\n"
    )
    add(f"**{company} sources ({n_comp})**\n")
    add("| Artifact | Source | Date | URL |")
    add("|---|---|---|---|")
    for a in pkg["artifacts"]:
        date = a.get("archive_capture_at") or a.get("published_at") or a.get("retrieved_at") or ""
        add(
            f"| {a['artifact_id'][:14]} | {a['source_type']} | {str(date)[:10]} | {a['url'][:60]} |"
        )

    if focal_arts:
        add(f"\n**{focal} (focal mirror) sources ({n_focal})** — run `{focal_ev.get('run_id', '?')}`\n")
        add("| Artifact | Source | Date | URL |")
        add("|---|---|---|---|")
        for a in focal_arts:
            date = (
                a.get("archive_capture_at") or a.get("published_at") or a.get("retrieved_at") or ""
            )
            add(
                f"| {a['artifact_id'][:14]} | {a['source_type']} | {str(date)[:10]} | {a['url'][:60]} |"
            )
    else:
        add(
            f"\n_No {focal} mirror evidence available in this run — {focal}-proof ratings "
            "are drawn from the focal run's classifications and should be treated as "
            "provisional until the mirror is attached._"
        )

    add(
        f"\n---\n*Stop reason: `{state.stop_reason}` · iterations {state.iteration} · tool calls {state.tool_calls_made}*"
    )
    return "\n".join(x for x in L if x is not None)


# ---- small render helpers --------------------------------------------------


def _theme_counts(cls: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter

    c: Counter[str] = Counter()
    for x in cls:
        if x.get("primary_theme"):
            c[x["primary_theme"]] += 1
    return c.most_common()


def _positioning_oneliner(cls: list[dict], company: str) -> str:
    themes = _theme_counts(cls)
    if not themes:
        return "insufficient classified evidence to summarize positioning"
    top = ", ".join(t for t, _ in themes[:3])
    return f"{company}'s public pages cluster around {top}"


def _villain_wording(cls: list[dict]) -> dict[str, Any]:
    exact: list[str] = []
    normalized: set[str] = set()
    for x in cls:
        for w in x.get("villain_exact_wording", []) or []:
            if w and w not in exact:
                exact.append(w)
        for v in x.get("villain_normalized", []) or []:
            normalized.add(v)
    return {"exact": exact, "normalized": normalized}


def _stance_distribution(cls: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter

    c: Counter[str] = Counter()
    for x in cls:
        s = x.get("competitive_stance")
        if s:
            c[s] += 1
    return c.most_common()


def _largest_uncertainty(pkg: dict) -> str:
    if pkg["corpus_skew_warnings"]:
        return pkg["corpus_skew_warnings"][0]
    if not pkg["change_events"]:
        return "historical sampling is too shallow to confirm messaging shifts"
    lows = [cd for cd in pkg["coverage_detail"] if cd["level"] in ("low", "unavailable")]
    if lows:
        return f"low coverage on {', '.join(cd['dimension'] for cd in lows[:3])}"
    return "optional channels (paid media, social) were not collected"


def render_run_outputs(state: DirectorState, ctx: GraphContext) -> dict[str, Path]:
    out = run_output_dir(state, ctx)
    package = build_json_package(state, ctx)
    json_path = out / "data.json"
    json_path.write_text(json.dumps(package, indent=2, default=str), encoding="utf-8")
    md_path = out / "brief.md"
    md_path.write_text(render_markdown(state, package), encoding="utf-8")
    return {"json": json_path, "markdown": md_path, "dir": out}
