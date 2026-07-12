"""Grounded per-run analysis chatbot (assignment: "Be conversational").

Answers follow-up questions about a COMPLETED run using ONLY that run's collected
findings (dominant message, proof gaps, opportunities, LinkedIn posts, temporal
changes, coverage/limitations). It never invents data: when the run can't answer
a question it says so and flags that a deeper run is needed. It also proposes
specific follow-up questions so the user can drill into the insights they want.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

CHAT_TASK = "analysis_chat"

CHAT_SYSTEM = (
    "You are a competitive-intelligence analyst answering follow-up questions about a "
    "COMPLETED marketing analysis. You are given the FULL dataset for this run: every "
    "source (with URL + timestamp), every extracted evidence excerpt, every classification, "
    "every grounded claim WITH its justification, all message-proof gaps, opportunities, "
    "temporal changes, LinkedIn posts, and traffic — plus the focal company's mirror data and "
    "brief summaries of OTHER competitors analyzed. Answer ONLY from this data — never invent "
    "facts, numbers, or sources. When you make a claim, CITE the specific source URL or "
    "evidence excerpt it comes from. If the data genuinely does not contain the answer, say so "
    "plainly and set needs_deeper_research=true. You may compare across competitors when the "
    "cross-competitor summaries support it. Be concise and insight-dense — you are talking to a "
    "growth marketer who wants to act.\n\n"
    "BE GENUINELY CONVERSATIONAL: if the question is ambiguous, underspecified, or would be "
    "answered very differently depending on the user's intent (e.g. 'their positioning' — "
    "enterprise vs SMB? current vs historical? 'best channel' — for demand gen vs brand?), do NOT "
    "guess. Set clarifying_question to ONE crisp question that would let you answer precisely, "
    "give your best partial answer for the most likely reading, and say which reading you assumed. "
    "Only ask when the answer genuinely forks — never for stalling. "
    "Always propose 2-3 specific follow-up questions the user could ask next. "
    "Respond ONLY via the structured tool."
)

# Char budget for the grounded context. Structured findings are always included;
# raw evidence excerpts fill the remainder and truncate last (with a disclosed note).
_CONTEXT_BUDGET_CHARS = 280_000


class ChatResponse(BaseModel):
    answer: str = Field(description="The grounded answer, in the marketer's terms.")
    suggested_followups: list[str] = Field(
        default_factory=list, description="2-3 specific follow-up questions to drill deeper."
    )
    grounded_in: list[str] = Field(
        default_factory=list, description="Which findings informed the answer (labels/ids)."
    )
    needs_deeper_research: bool = Field(
        default=False, description="True if the run's data cannot answer this question."
    )
    clarifying_question: str | None = Field(
        default=None,
        description="ONE crisp question back to the user when their question is ambiguous and "
        "the answer genuinely forks on their intent. Null when the question is clear.",
    )
    confidence: str = Field(default="medium", description="high | medium | low")


def _package_path(run_id: str) -> Path:
    from .config import get_settings

    return Path(get_settings().outputs_dir) / "runs" / run_id / "data.json"


def _fmt_list(items: list[Any], fields: list[str], limit: int | None = None) -> str:
    lines = []
    for it in items[: (limit or len(items))]:
        parts = [f"{f}={it.get(f)}" for f in fields if it.get(f) not in (None, "", [])]
        if parts:
            lines.append("  - " + " · ".join(str(p) for p in parts))
    return "\n".join(lines)


def _artifact_urls(pkg: dict[str, Any]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for a in pkg.get("artifacts", []):
        urls[a.get("artifact_id")] = a.get("url") or ""
    for s in pkg.get("sources", []):
        urls.setdefault(s.get("artifact_id"), s.get("url") or "")
    return urls


def _classifications_block(pkg: dict[str, Any], urls: dict[str, str]) -> str:
    lines = []
    for c in pkg.get("classifications", []):
        u = urls.get(str(c.get("artifact_id")), "")
        bits = [
            f"theme={c.get('primary_theme')}",
            f'msg="{(c.get("primary_message") or "")[:120]}"',
        ]
        for f in ("segments", "personas", "category_entry_points", "claim_types", "proof_types",
                  "villain_exact_wording", "competitive_stance", "cta", "pricing_disclosure_level"):
            if c.get(f):
                bits.append(f"{f}={c.get(f)}")
        lines.append(f"  - [{u}] " + " · ".join(str(b) for b in bits))
    return "\n".join(lines)


def _claims_block(pkg: dict[str, Any], ev_by_id: dict[str, dict], urls: dict[str, str]) -> str:
    lines = []
    for cl in pkg.get("claims", []):
        just = []
        for eid in (cl.get("evidence_ids") or [])[:4]:
            ev = ev_by_id.get(eid)
            if ev:
                just.append(f'"{(ev.get("exact_excerpt") or "")[:140]}" [{urls.get(str(ev.get("artifact_id")), "")}]')
        lines.append(
            f"  - CLAIM [{cl.get('status')}, conf {cl.get('claim_confidence')}]: {cl.get('statement')}\n"
            f"      justification: {cl.get('confidence_reason') or 'n/a'}\n"
            + ("      evidence: " + " | ".join(just) if just else "")
        )
    return "\n".join(lines)


def _evidence_block(pkg: dict[str, Any], urls: dict[str, str], budget: int) -> tuple[str, int]:
    """Every evidence excerpt with its source URL + quality, up to a char budget."""
    lines = []
    used = 0
    total = len(pkg.get("evidence", []))
    shown = 0
    for ev in pkg.get("evidence", []):
        u = urls.get(str(ev.get("artifact_id")), "")
        line = f'  - [{ev.get("source_quality")}|{u}] "{(ev.get("exact_excerpt") or "")[:220]}"'
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
        shown += 1
    note = "" if shown >= total else f"\n  … ({total - shown} more excerpts omitted for length; ask about a specific source)"
    return "\n".join(lines) + note, used


def scope_to_vertical(pkg: dict[str, Any], vertical: str) -> dict[str, Any]:
    """Shallow-copy the package filtered to one product vertical: only the
    classifications/evidence/sources/posts whose artifact is tagged with that
    vertical. Cross-cutting findings (gaps, opportunities, dominant message)
    stay global — the chat notes they are corpus-wide."""
    by_artifact = (pkg.get("product_vertical_analysis") or {}).get("by_artifact") or {}
    allowed = {aid for aid, verts in by_artifact.items() if vertical in verts}
    scoped = dict(pkg)
    scoped["classifications"] = [
        c for c in pkg.get("classifications", []) if c.get("artifact_id") in allowed
    ]
    scoped["evidence"] = [e for e in pkg.get("evidence", []) if e.get("artifact_id") in allowed]
    scoped["sources"] = [s for s in pkg.get("sources", []) if s.get("artifact_id") in allowed]
    scoped["artifacts"] = [a for a in pkg.get("artifacts", []) if a.get("artifact_id") in allowed]
    scoped["linkedin_posts"] = [
        p for p in pkg.get("linkedin_posts", []) if vertical in (p.get("verticals") or [])
    ]
    return scoped


def build_context(
    pkg: dict[str, Any],
    cross: str = "",
    budget_chars: int = _CONTEXT_BUDGET_CHARS,
    vertical: str | None = None,
) -> str:
    """FULL grounded context: all sources, evidence, classifications, claims (with
    justifications), gaps, opportunities, temporal, LinkedIn, traffic, focal mirror,
    and cross-competitor summaries. Structured findings always included; raw
    evidence excerpts fill the remaining budget and truncate last."""
    if vertical:
        pkg = scope_to_vertical(pkg, vertical)
    companies = pkg.get("companies", [])
    competitor = (companies[0].get("canonical_name") if companies else pkg.get("scope", {}).get("company_input")) or "the competitor"
    focal = companies[1].get("canonical_name") if len(companies) > 1 else "Rippling"
    dom = pkg.get("dominant_message", {}) or {}
    es = pkg.get("eval_summary", {}) or {}
    sw = pkg.get("similarweb", {}) or {}
    urls = _artifact_urls(pkg)
    ev_by_id = {e.get("evidence_id"): e for e in pkg.get("evidence", [])}
    fe = pkg.get("focal_evidence", {}) or {}

    sections = [
        f"COMPETITOR: {competitor}   FOCAL COMPANY: {focal}",
        f"Dominant message: {dom.get('label') or 'n/a'} (theme: {dom.get('theme')}; basis: {dom.get('reason')})",
        f"Corpus: {es.get('n_artifacts', '?')} artifacts · {es.get('n_classifications','?')} classifications · "
        f"{es.get('n_claims', '?')} claims · {es.get('n_proof_gaps', '?')} proof gaps · "
        f"{es.get('n_opportunities', '?')} opportunities · {es.get('n_change_events','?')} changes.",
    ]
    # Full structured findings (always included).
    if pkg.get("proof_gaps"):
        sections.append("ALL MESSAGE–PROOF GAPS:\n" + _fmt_list(
            pkg["proof_gaps"],
            ["short_label", "claim_text", "attackability", "proof_strength", "focal_proof_strength",
             "claim_specificity", "missing_proof", "actionable_interpretation", "why_attack_might_backfire"]))
    if pkg.get("opportunities"):
        sections.append(f"ALL {focal} OPPORTUNITIES:\n" + _fmt_list(
            pkg["opportunities"],
            ["title", "message_angle", "focal_proof_status", "focal_current_usage",
             "structural_defensibility", "why_this_could_backfire", "experiment_hypothesis", "kill_rule"]))
    if pkg.get("claims"):
        sections.append("ALL GROUNDED CLAIMS (with justifications + cited evidence):\n"
                        + _claims_block(pkg, ev_by_id, urls))
    if pkg.get("classifications"):
        sections.append("ALL CLASSIFICATIONS (per source):\n" + _classifications_block(pkg, urls))
    if pkg.get("change_events"):
        sections.append("STRATEGY OVER TIME:\n" + _fmt_list(
            pkg["change_events"], ["dimension", "prior_state", "current_state", "confidence",
                                   "lifecycle", "alternative_explanations"]))
    if pkg.get("linkedin_posts"):
        sections.append(f"{competitor} LINKEDIN EMPLOYEE POSTS:\n" + _fmt_list(
            pkg["linkedin_posts"], ["author", "author_role", "theme", "competitive_stance",
                                    "post_url", "excerpt"]))
    cm = pkg.get("commercial_motion", {}) or {}
    if cm:
        sections.append("COMMERCIAL MOTION: " + json.dumps(cm)[:600])
    if pkg.get("category_entry_points"):
        sections.append("CATEGORY ENTRY POINTS (ownership):\n" + _fmt_list(
            pkg["category_entry_points"], ["cep", "ownership", "competitor_pages", "focal_pages"]))
    if pkg.get("product_positioning"):
        sections.append("PRODUCT POSITIONING: " + json.dumps(pkg["product_positioning"])[:800])
    if sw.get("metrics"):
        sections.append(f"TRAFFIC (est., {sw.get('data_source')}): {json.dumps(sw['metrics'])[:600]}")
    # All sources (URL + type + timestamp).
    if pkg.get("sources"):
        sections.append("ALL SOURCES (url · type · retrieved):\n" + "\n".join(
            f"  - {s.get('url')} · {s.get('source_type')} · {str(s.get('retrieved_at'))[:10]}"
            for s in pkg["sources"]))
    # Focal (Rippling) mirror.
    if fe.get("artifacts"):
        sections.append(f"{focal} MIRROR SOURCES ({len(fe['artifacts'])}):\n" + "\n".join(
            f"  - {a.get('url')} · {a.get('source_type')}" for a in fe["artifacts"][:40]))
    if pkg.get("limitations"):
        sections.append("LIMITATIONS: " + "; ".join(str(x) for x in pkg["limitations"]))
    if pkg.get("corpus_skew_warnings"):
        sections.append("CORPUS CAVEATS: " + "; ".join(str(x) for x in pkg["corpus_skew_warnings"]))
    if cross:
        sections.append("OTHER COMPETITORS ANALYZED (for comparison):\n" + cross)

    body = "\n\n".join(sections)
    # Fill remaining budget with raw evidence excerpts (the source justifications).
    remaining = max(0, budget_chars - len(body))
    if remaining > 500 and pkg.get("evidence"):
        ev_text, _ = _evidence_block(pkg, urls, remaining - 200)
        body += "\n\nALL EVIDENCE EXCERPTS (source justifications):\n" + ev_text
    return body


def cross_competitor_summaries(current_run_id: str, competitor_id: str | None) -> str:
    """One-line summaries of OTHER competitors analyzed (from stored runs), so the
    chat can compare across competitors."""
    from .config import get_settings

    runs_dir = Path(get_settings().outputs_dir) / "runs"
    if not runs_dir.exists():
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    paths = sorted(runs_dir.glob("*/data.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        if path.parent.name == current_run_id:
            continue
        try:
            other = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        comps = other.get("companies", [])
        cid = comps[0].get("company_id") if comps else None
        name = comps[0].get("canonical_name") if comps else other.get("scope", {}).get("company_input")
        if not name or name in seen or cid == competitor_id:
            continue
        seen.add(name)
        dom = other.get("dominant_message", {}) or {}
        gaps = [g.get("short_label") for g in (other.get("proof_gaps") or [])[:5]]
        lines.append(
            f"  - {name} (vs {(comps[1].get('canonical_name') if len(comps) > 1 else '?')}): "
            f"dominant='{dom.get('label') or dom.get('theme')}'; top gaps={gaps}; "
            f"{other.get('eval_summary', {}).get('n_artifacts', '?')} artifacts."
        )
        if len(lines) >= 6:
            break
    return "\n".join(lines)


async def chat_about_run(
    run_id: str,
    question: str,
    history: list[dict[str, str]] | None = None,
    execution_mode: str = "live",
    vertical: str | None = None,
) -> dict[str, Any]:
    """Answer a follow-up question about a run, grounded in its findings.
    ``vertical`` scopes the grounded data to one product vertical (payroll,
    it_device_management, ...) so per-offering questions get per-offering data."""
    path = _package_path(run_id)
    if not path.exists():
        raise KeyError(f"run not found (no data.json): {run_id}")
    pkg = json.loads(path.read_text(encoding="utf-8"))
    comps = pkg.get("companies", [])
    competitor_id = comps[0].get("company_id") if comps else None
    cross = cross_competitor_summaries(run_id, competitor_id)
    context = build_context(pkg, cross=cross, vertical=vertical)
    if vertical:
        context = (
            f"FOCUS: the user has scoped this conversation to the '{vertical}' product "
            "vertical — the sources/classifications/evidence/posts below are filtered to it. "
            "Gaps/opportunities/dominant-message remain corpus-wide; say so if you cite them.\n\n"
            + context
        )

    from .config import get_config, get_settings
    from .model_gateway import build_gateway

    gateway = build_gateway(execution_mode, get_settings(), get_config())  # type: ignore[arg-type]
    convo = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in (history or [])[-8:])
    user_content = (
        f"RUN FINDINGS:\n{context}\n\n"
        + (f"CONVERSATION SO FAR:\n{convo}\n\n" if convo else "")
        + f"USER QUESTION: {question}"
    )
    try:
        result = await gateway.generate_structured(
            CHAT_TASK, CHAT_SYSTEM, user_content, ChatResponse,
            prompt_name="analysis_chat", prompt_version="v1",
        )
        out = result.output
        return {
            "answer": out.answer,
            "suggested_followups": out.suggested_followups,
            "grounded_in": out.grounded_in,
            "needs_deeper_research": out.needs_deeper_research,
            "clarifying_question": out.clarifying_question,
            "confidence": out.confidence,
        }
    except Exception as exc:  # never crash the chat surface
        return {
            "answer": "I couldn't reach the analysis model for this question. "
            "Set ANTHROPIC_API_KEY in .env (live mode) to enable chat.",
            "suggested_followups": [],
            "grounded_in": [],
            "clarifying_question": None,
            "needs_deeper_research": False,
            "confidence": "low",
            "error": type(exc).__name__,
        }
