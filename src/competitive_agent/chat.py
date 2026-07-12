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
    "COMPLETED marketing analysis. Answer ONLY from the RUN FINDINGS provided — never "
    "invent facts, numbers, or sources. If the findings do not contain the answer, say so "
    "plainly and set needs_deeper_research=true (suggest what to research). Ground claims in "
    "the specific findings (theme, proof gap, opportunity, post, or change) they come from. "
    "Be concise and insight-dense — you are talking to a growth marketer who wants to act. "
    "Always propose 2-3 specific, useful follow-up questions the user could ask next to get "
    "more specific. Respond ONLY via the structured tool."
)


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
    confidence: str = Field(default="medium", description="high | medium | low")


def _package_path(run_id: str) -> Path:
    from .config import get_settings

    return Path(get_settings().outputs_dir) / "runs" / run_id / "data.json"


def _fmt_list(items: list[Any], fields: list[str], limit: int = 8) -> str:
    lines = []
    for it in items[:limit]:
        parts = [f"{f}={it.get(f)}" for f in fields if it.get(f) not in (None, "", [])]
        if parts:
            lines.append("  - " + " · ".join(str(p) for p in parts))
    return "\n".join(lines)


def build_context(pkg: dict[str, Any]) -> str:
    """Compact, grounded digest of the run's findings for the chat prompt."""
    companies = pkg.get("companies", [])
    competitor = (companies[0].get("canonical_name") if companies else pkg.get("scope", {}).get("company_input")) or "the competitor"
    focal = companies[1].get("canonical_name") if len(companies) > 1 else "Rippling"
    dom = pkg.get("dominant_message", {}) or {}
    es = pkg.get("eval_summary", {}) or {}
    sw = pkg.get("similarweb", {}) or {}

    sections = [
        f"COMPETITOR: {competitor}   FOCAL COMPANY: {focal}",
        f"Dominant message: {dom.get('label') or 'n/a'} (theme: {dom.get('theme')})",
        f"Corpus: {es.get('n_artifacts', '?')} artifacts, {es.get('n_claims', '?')} claims, "
        f"{es.get('n_proof_gaps', '?')} proof gaps, {es.get('n_opportunities', '?')} opportunities.",
    ]
    if pkg.get("proof_gaps"):
        sections.append(
            "MESSAGE–PROOF GAPS (competitor claim → who can prove it):\n"
            + _fmt_list(pkg["proof_gaps"], ["short_label", "attackability", "proof_strength", "focal_proof_strength", "claim_specificity"])
        )
    if pkg.get("opportunities"):
        sections.append(
            f"{focal} OPPORTUNITIES (recommended actions):\n"
            + _fmt_list(pkg["opportunities"], ["title", "message_angle", "focal_proof_status", "why_this_could_backfire"], 5)
        )
    if pkg.get("change_events"):
        sections.append(
            "STRATEGY OVER TIME (low-confidence emerging signals):\n"
            + _fmt_list(pkg["change_events"], ["dimension", "prior_state", "current_state", "confidence"], 6)
        )
    if pkg.get("linkedin_posts"):
        sections.append(
            f"{competitor} LINKEDIN EMPLOYEE POSTS:\n"
            + _fmt_list(pkg["linkedin_posts"], ["author", "author_role", "theme", "competitive_stance", "excerpt"], 8)
        )
    cm = pkg.get("commercial_motion", {}) or {}
    if cm:
        sections.append(
            f"COMMERCIAL MOTION: {cm.get('primary_motion')} · pricing disclosure {cm.get('pricing_disclosure')} · "
            f"segment focus {cm.get('segment_focus')}"
        )
    if pkg.get("category_entry_points"):
        sections.append(
            "CATEGORY ENTRY POINTS (search intents, ownership):\n"
            + _fmt_list(pkg["category_entry_points"], ["cep", "ownership", "competitor_pages", "focal_pages"], 8)
        )
    if sw.get("metrics"):
        sections.append(f"TRAFFIC (est., {sw.get('data_source')}): {json.dumps(sw['metrics'])[:400]}")
    if pkg.get("limitations"):
        sections.append("LIMITATIONS: " + "; ".join(str(x) for x in pkg["limitations"][:6]))
    if pkg.get("corpus_skew_warnings"):
        sections.append("CORPUS CAVEATS: " + "; ".join(str(x) for x in pkg["corpus_skew_warnings"][:4]))
    return "\n\n".join(sections)


async def chat_about_run(
    run_id: str,
    question: str,
    history: list[dict[str, str]] | None = None,
    execution_mode: str = "live",
) -> dict[str, Any]:
    """Answer a follow-up question about a run, grounded in its findings."""
    path = _package_path(run_id)
    if not path.exists():
        raise KeyError(f"run not found (no data.json): {run_id}")
    pkg = json.loads(path.read_text(encoding="utf-8"))
    context = build_context(pkg)

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
            "confidence": out.confidence,
        }
    except Exception as exc:  # never crash the chat surface
        return {
            "answer": "I couldn't reach the analysis model for this question "
            f"({type(exc).__name__}). Set ANTHROPIC_API_KEY (live) to enable chat.",
            "suggested_followups": [],
            "grounded_in": [],
            "needs_deeper_research": False,
            "confidence": "low",
            "error": type(exc).__name__,
        }
