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

Keyword intelligence (KEYWORDS contract + Gemini SERP addendum; seam in
``tools/keywords.py``):

- PRIMARY — when ``GEMINI_API_KEY`` is present, OBSERVED live-SERP
  intelligence runs in TWO stages sharing ONE per-draft budget of
  ``_MAX_SERP_KEYWORDS`` Gemini calls: (1) a PRE-FETCH of up to
  ``_MAX_PROMPT_SERP_PHRASES`` humanized buying-trigger (CEP) phrases whose
  observations feed the prompt's keyword-intelligence block (the model never
  drafts blind while the block advertises live-SERP data), and (2) cluster
  enrichment (``cluster["serp_intel"]``: real People-Also-Ask questions,
  related searches, ranking formats, SERP features, grounding-source URLs)
  which REUSES pre-fetched rows for matching seed keywords and spends only
  the remaining allowance on new ones. This path attaches NO volumes and NO
  scores — Gemini does not report search demand, and we never rank on
  invented numbers.
- Volume seam — when only ``SEMRUSH_API_KEY`` is present, seed keywords are
  batch-enriched with provider-reported volume/CPC/competition
  (``keyword_metrics``), an ``opportunity_score`` is computed (sum of known
  volumes weighted by focal proof status) and clusters are sorted by it.
- Provenance (envelope ``keyword_provider`` + ``disclaimer``) reflects what
  ACTUALLY attached: ``"gemini_serp"`` when SERP observations informed the
  draft, ``"semrush"`` when real volumes did, ``"gemini_serp+semrush"`` (with
  a combined disclaimer) when both did — a provider that produced nothing is
  never claimed.
- Neither -> ``keyword_provider`` null and NO metrics/scores attached —
  nothing is ever estimated.

Mode isolation (accuracy review): a draft with ``execution_mode ==
"fixture"`` NEVER makes provider network calls just because real keys sit in
the developer's environment — only a test-injected provider (module-level
monkeypatch of the ``tools.keywords`` seam) may enrich a fixture draft.

Results are cached at ``outputs/runs/<run_id>/paid_search.json`` so repeat
views never re-spend model budget (``force=True`` regenerates).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import get_config, get_settings
from .schemas.common import utcnow
from .schemas.keywords import SerpIntel
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

# Softened variant used ONLY when a real keyword API supplied the numbers.
_DISCLAIMER_WITH_METRICS = (
    "Search volume, CPC, and competition shown are provider-reported estimates "
    "({provider}), not measured auction truth; commercial ad spend remains not "
    "publicly knowable — validate final bids in the live auction."
)

# Variant used ONLY when live-SERP intelligence (gemini_serp) was attached:
# the observations are real, but this provider returns no volumes/CPC.
_DISCLAIMER_SERP = (
    "SERP intelligence is observed live from Google results at draft time; "
    "volumes/CPC are not returned by this provider — validate free in Google "
    "Keyword Planner / Search Console."
)

# Variant used ONLY when BOTH real volume metrics AND live-SERP intelligence
# informed the draft — each claim covers a provider that actually produced.
_DISCLAIMER_COMBINED = (
    "Search volume, CPC, and competition are provider-reported estimates "
    "({provider}) and the SERP intelligence is observed live from Google "
    "results at draft time; commercial ad spend remains not publicly "
    "knowable — validate final bids in the live auction."
)

# Documented in method_note whenever SERP intelligence is attached. Counts
# cover BOTH stages (prompt pre-fetch + cluster enrichment). PAA questions
# are real questions buyers ask — ad copy angles + landing-page H2s.
_SERP_NOTE = (
    "Live SERP intelligence via Google search grounding (gemini_serp): "
    "{enriched} keyword(s) enriched across the prompt pre-fetch and cluster "
    "stages, {skipped} skipped (per-draft call cap / rate limit / ungrounded "
    "answers discarded). People-Also-Ask questions are real buyer questions "
    "observed on the results page — use them as ad copy angles and "
    "landing-page H2s. No volumes and no scores are attached; this provider "
    "observes the results page, not search demand."
)

# Opportunity-score formula (KEYWORDS contract) — documented verbatim in the
# envelope's method_note whenever scores are computed.
_PROOF_WEIGHTS: dict[str, float] = {"available": 1.0, "partial": 0.6, "missing": 0.3}
_SCORING_NOTE = (
    "Opportunity score = sum of provider-reported search volumes known for the "
    "cluster's seed keywords ({provider}), weighted by focal proof status "
    "(available=1.0, partial=0.6, missing=0.3); keywords without provider data "
    "contribute 0 (never estimated). Clusters are sorted by this score, "
    "descending."
)

# Batch cap for cluster seed-keyword enrichment (deduped across clusters).
_MAX_METRIC_KEYWORDS = 40

# Per-draft cap on Gemini SERP calls (one call per keyword), shared by the
# prompt pre-fetch AND cluster enrichment TOGETHER — never exceeded.
_MAX_SERP_KEYWORDS = 12

# Bounded number of observed buying-trigger (CEP) phrases looked up for the
# prompt's real-metrics block.
_MAX_PROMPT_PHRASES = 12

# Bounded number of CEP phrases pre-fetched as live-SERP observations for the
# prompt block — deliberately half the shared cap so cluster enrichment
# always keeps an allowance.
_MAX_PROMPT_SERP_PHRASES = 6

_NO_KEYWORD_API = "(no keyword API configured)"


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
        meta = metadata or {}
        # Discovery pointers are library NAVIGATION pages (JS app shells) —
        # feeding their chrome to the strategist as "observed ad creatives"
        # would ground clusters in sign-in banners. Only real creative records
        # (or pages with actual creative text) qualify as evidence here.
        creative = " ".join(str(meta.get("creative_body") or "").split())
        if not creative and meta.get("is_discovery_pointer"):
            continue
        text = creative[:400] or " ".join((r["normalized_text"] or "").split())[:400]
        if not text:
            continue
        out.append(f"- [{r['title'] or 'ad'}] {text} (source: {r['url']})")
        if len(out) >= 8:
            break
    return "\n".join(out) or "(none collected on this run)"


def _fmt_focal_proof(run_id: str, pkg: dict[str, Any]) -> str:
    """Focal proof by theme, counted from the focal company's classifications.

    The competitor package's ``classifications`` array carries ONLY the
    competitor's rows — the focal company's live in its MIRROR run. Read them
    from the DB via the parent state's ``focal_run_id`` (falling back to any
    focal rows in the package, which older packages may carry).
    """
    companies = pkg.get("companies") or []
    focal_id = companies[1].get("company_id") if len(companies) > 1 else None
    if not focal_id:
        return "(no focal mirror on this run — treat all focal proof as unverified)"
    by_theme: dict[str, dict[str, Any]] = {}

    def _tally(theme: Any, proof_types: Any) -> None:
        if not theme:
            return
        slot = by_theme.setdefault(str(theme), {"n": 0, "proof": set()})
        slot["n"] += 1
        for p in proof_types or []:
            slot["proof"].add(str(p))

    for c in pkg.get("classifications") or []:
        if c.get("company_id") == focal_id:
            _tally(c.get("primary_theme"), c.get("proof_types"))
    if not by_theme:
        from .storage.repository import Repository

        repo = Repository.open(get_settings().db_path)
        focal_run = repo.conn.execute(
            "SELECT json_extract(state_json, '$.focal_run_id') FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        focal_run_id = focal_run[0] if focal_run else None
        if focal_run_id:
            for row in repo.conn.execute(
                # 'merged' is the per-artifact rollup family — it carries BOTH
                # primary_theme and proof_types ('message' alone lacks proof).
                "SELECT json_extract(payload_json, '$.primary_theme'), "
                "       json_extract(payload_json, '$.proof_types') "
                "FROM classifications WHERE run_id = ? AND family = 'merged'",
                (focal_run_id,),
            ).fetchall():
                try:
                    proof = json.loads(row[1]) if row[1] else []
                except Exception:
                    proof = []
                _tally(row[0], proof)
    lines = [
        f"- {theme}: {v['n']} page(s); proof types observed: "
        + (", ".join(sorted(v["proof"])[:6]) or "none")
        for theme, v in sorted(by_theme.items(), key=lambda kv: -kv[1]["n"])[:12]
    ]
    return "\n".join(lines) or "(no classified focal pages)"


def build_inputs(
    run_id: str, pkg: dict[str, Any], keyword_metrics: str | None = None
) -> dict[str, str]:
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
        "focal_proof_by_theme": _fmt_focal_proof(run_id, pkg),
        # StrictUndefined jinja: this key must ALWAYS be present; the default
        # is the honest no-provider statement, never an empty string.
        "keyword_metrics": keyword_metrics or _NO_KEYWORD_API,
    }


def _fmt_keyword_metric_line(metric: Any) -> str:
    volume = f"{metric.volume}/mo" if metric.volume is not None else "unknown"
    cpc = f"${metric.cpc_usd:.2f}" if metric.cpc_usd is not None else "unknown"
    competition = f"{metric.competition:.2f}" if metric.competition is not None else "unknown"
    return f'- "{metric.keyword}": volume {volume}, CPC {cpc}, competition {competition}'


@dataclass
class _SerpBudget:
    """Shared Gemini SERP state across the two enrichment stages of one draft.

    The prompt pre-fetch and post-generation cluster enrichment TOGETHER may
    spend at most ``_MAX_SERP_KEYWORDS`` Gemini calls; rows fetched for the
    prompt are cached here and reused for matching cluster seed keywords
    instead of being re-fetched.
    """

    # _norm(keyword) -> grounded row (from either stage).
    rows: dict[str, SerpIntel] = field(default_factory=dict)
    # _norm of every keyword we tried (or budget-capped out of) — the honest
    # denominator for the method note's skipped count.
    requested: set[str] = field(default_factory=set)
    calls_used: int = 0
    # The exact billing message once the first-call 429 degrade tripped —
    # the post stage never re-spends the call.
    quota_note: str | None = None


def _cep_phrases(pkg: dict[str, Any], cap: int) -> list[str]:
    """Humanized observed buying-trigger (CEP) phrases, deduped and capped —
    the single phrase source for BOTH prompt-block lookups."""
    phrases: list[str] = []
    seen: set[str] = set()
    for row in pkg.get("category_entry_points") or []:
        phrase = " ".join(str(row.get("cep") or "").replace("_", " ").split())
        key = _norm(phrase)
        if key and key not in seen:
            seen.add(key)
            phrases.append(phrase)
        if len(phrases) >= cap:
            break
    return phrases


def _prompt_keyword_metrics(pkg: dict[str, Any]) -> tuple[str | None, str | None]:
    """Real provider metrics for the OBSERVED buying-trigger (CEP) phrases.

    Returns ``(block_text, provider_name)`` — ``provider_name`` is set ONLY
    when real metrics actually came back, so the provenance label never
    claims a provider that produced nothing. ``(None, None)`` when no volume
    API is configured (``build_inputs`` then renders the honest default).
    Provider failures degrade to an explicit unavailability line — the model
    is never handed estimated numbers.
    """
    from .tools import keywords as keywords_module

    provider = keywords_module.active_keyword_provider()
    if provider is None:
        return None, None
    phrases = _cep_phrases(pkg, _MAX_PROMPT_PHRASES)
    if not phrases:
        return (
            f"(keyword API '{provider.name}' configured, but no observed buying triggers to look up)",
            None,
        )
    try:
        metrics = provider.fetch(phrases)
    except Exception:
        return (
            f"(keyword API '{provider.name}' configured but unreachable — metrics unavailable)",
            None,
        )
    if not metrics:
        return (
            f"(keyword API '{provider.name}' returned no data for the observed "
            "buying-trigger phrases)",
            None,
        )
    lines = [f"Provider-reported metrics ({provider.name}) for observed buying triggers:"]
    lines.extend(_fmt_keyword_metric_line(m) for m in metrics)
    return "\n".join(lines), provider.name


def _prefetch_serp_observations(pkg: dict[str, Any], budget: _SerpBudget) -> str | None:
    """OBSERVED live-SERP intelligence for the prompt's keyword block.

    With only ``GEMINI_API_KEY`` set the model must not draft blind while the
    prompt block advertises live-SERP observations: pre-fetch up to
    ``_MAX_PROMPT_SERP_PHRASES`` humanized CEP phrases (the same phrase
    source the volume block uses) and format them as observations — per
    phrase the top PAA questions, related searches, and the intent note;
    explicitly no volumes. Rows and spent calls land on ``budget`` so
    ``_attach_serp_intel`` reuses them under the SHARED per-draft cap. A
    first-call quota 429 records the exact billing note on ``budget`` and
    returns None — the draft proceeds with the no-provider block.
    """
    from .tools import keywords as keywords_module

    if keywords_module.active_serp_provider() is None:
        return None
    phrases = _cep_phrases(pkg, _MAX_PROMPT_SERP_PHRASES)
    if not phrases:
        return None
    # Count the batch BEFORE the call: on a mid-batch failure we cannot know
    # how many calls went out, and the shared cap must never be exceeded.
    budget.requested.update(_norm(p) for p in phrases)
    budget.calls_used += len(phrases)
    try:
        intel = keywords_module.fetch_serp_intel(phrases)
    except keywords_module.GeminiSerpQuotaError as exc:
        budget.quota_note = str(exc)  # exact billing message; envelope stays null
        return None
    except Exception:
        return None  # provider unreachable — draft proceeds without observations
    for row in intel or []:
        budget.rows[_norm(row.keyword)] = row
    if not intel:
        return None
    lines = [
        "Live-SERP observations (gemini_serp) for observed buying triggers "
        "(no volumes — observations only; never turn these into numbers):"
    ]
    for row in intel:
        lines.append(f'- "{row.keyword}":')
        lines.extend(f"  - People also ask: {q}" for q in row.paa_questions[:3])
        if row.related_searches:
            lines.append("  - Related searches: " + "; ".join(row.related_searches[:3]))
        if row.intent_note:
            lines.append(f"  - Intent: {row.intent_note}")
    return "\n".join(lines)


def _build_prompt_keyword_intel(
    pkg: dict[str, Any], budget: _SerpBudget
) -> tuple[str | None, str | None]:
    """The prompt's keyword-intelligence block: real volume metrics and/or
    pre-fetched live-SERP observations — whatever the configured providers
    actually supplied, never anything estimated.

    Returns ``(block_text, volume_provider_name)``; ``(None, None)`` when
    neither provider produced a block (``build_inputs`` renders the honest
    no-provider default).
    """
    metrics_block, volume_provider = _prompt_keyword_metrics(pkg)
    serp_block = _prefetch_serp_observations(pkg, budget)
    parts = [part for part in (metrics_block, serp_block) if part]
    return ("\n\n".join(parts) or None), volume_provider


def _attach_keyword_metrics(clusters: list[dict[str, Any]]) -> str | None:
    """Enrich clusters with REAL provider metrics + opportunity scores, in place.

    Returns the provider name when metrics were attached, else None (no
    provider configured, or the provider failed — in both cases clusters are
    left untouched: no ``keyword_metrics`` keys, no scores, no re-sort, so the
    no-provider envelope stays exactly shaped like earlier runs).
    """
    from .tools import keywords as keywords_module

    provider = keywords_module.active_keyword_provider()
    if provider is None:
        return None
    seeds: list[str] = []
    seen: set[str] = set()
    for cluster in clusters:
        for kw in cluster.get("seed_keywords") or []:
            if not isinstance(kw, str):
                continue
            key = _norm(kw)
            if key and key not in seen and len(seeds) < _MAX_METRIC_KEYWORDS:
                seen.add(key)
                seeds.append(kw)
    try:
        metrics = keywords_module.fetch_keyword_metrics(seeds)
    except Exception:
        metrics = None
    if metrics is None:
        # Provider unreachable/failed: degrade honestly to the no-provider
        # shape rather than shipping a provider label with no real numbers.
        return None
    by_keyword = {_norm(m.keyword): m for m in metrics}
    for cluster in clusters:
        cluster_metrics = [
            by_keyword[_norm(kw)]
            for kw in cluster.get("seed_keywords") or []
            if isinstance(kw, str) and _norm(kw) in by_keyword
        ]
        cluster["keyword_metrics"] = [m.model_dump(mode="json") for m in cluster_metrics]
        known_volume = sum(m.volume for m in cluster_metrics if m.volume is not None)
        weight = _PROOF_WEIGHTS.get(str(cluster.get("focal_proof_status")), 0.3)
        cluster["opportunity_score"] = round(known_volume * weight, 1)
    clusters.sort(key=lambda c: float(c.get("opportunity_score") or 0.0), reverse=True)
    return provider.name


def _provider_enrichment_allowed(execution_mode: str) -> bool:
    """Mode isolation (accuracy review): may this draft call keyword providers?

    Fixture drafts must never make live provider network calls just because a
    real key sits in the developer's environment (.env). The ONLY exception is
    a test-injected provider — the unit tests monkeypatch the ``tools.keywords``
    seam at module level (e.g. ``setattr(keywords_module,
    "active_keyword_provider", lambda: fake)``), which leaves a callable whose
    ``__module__`` is not the keywords module.
    """
    if execution_mode != "fixture":
        return True
    from .tools import keywords as keywords_module

    seam = (
        keywords_module.active_keyword_provider,
        keywords_module.active_serp_provider,
        keywords_module.fetch_keyword_metrics,
        keywords_module.fetch_serp_intel,
    )
    return any(
        getattr(fn, "__module__", keywords_module.__name__) != keywords_module.__name__
        for fn in seam
    )


def _serp_seed_candidates(clusters: list[dict[str, Any]]) -> list[str]:
    """Seed keywords to enrich, best-first: every cluster's TOP seed keyword,
    then each cluster's 2nd and 3rd seeds — so the ``[:_MAX_SERP_KEYWORDS]``
    slice gives every cluster its best shot before any cluster gets depth."""
    rounds: list[list[str]] = [[], [], []]
    for cluster in clusters:
        seeds = [kw for kw in (cluster.get("seed_keywords") or []) if isinstance(kw, str)]
        for rank in range(3):
            if len(seeds) > rank:
                rounds[rank].append(seeds[rank])
    out: list[str] = []
    seen: set[str] = set()
    for keyword in (kw for rnd in rounds for kw in rnd):
        key = _norm(keyword)
        if key and key not in seen:
            seen.add(key)
            out.append(keyword)
    return out


def _attach_serp_intel(
    clusters: list[dict[str, Any]], budget: _SerpBudget
) -> tuple[str | None, str | None]:
    """Enrich clusters with OBSERVED live-SERP intelligence, in place.

    Shares ``budget`` with the prompt pre-fetch: cached rows are REUSED for
    matching cluster seed keywords, and only the remaining allowance
    (``_MAX_SERP_KEYWORDS - calls_used``) is spent on new keywords — the two
    stages together never exceed the per-draft cap.

    Returns ``(provider_label, note)``: ``("gemini_serp", method-note with
    both stages' enriched/skipped counts)`` when grounded observations
    informed this draft (prompt block and/or cluster ``serp_intel``);
    ``(None, billing-note)`` on the quota degrade — pre-fetch or first-call
    here — carrying the exact spec message while the envelope stays null;
    ``(None, None)`` otherwise (no key / provider failed / nothing grounded)
    with clusters left untouched. This path NEVER attaches volumes or scores
    and never re-sorts (no ranking on invented numbers).
    """
    from .tools import keywords as keywords_module

    if keywords_module.active_serp_provider() is None:
        return None, None
    if budget.quota_note is not None:
        # The pre-fetch already tripped the billing 429 — degrade exactly as
        # a direct quota would (null provider, exact note), never call again.
        return None, budget.quota_note
    candidates = _serp_seed_candidates(clusters)
    budget.requested.update(_norm(kw) for kw in candidates)
    remaining = max(0, _MAX_SERP_KEYWORDS - budget.calls_used)
    to_fetch = [kw for kw in candidates if _norm(kw) not in budget.rows][:remaining]
    quota_suffix: str | None = None
    if to_fetch:
        # Count the batch BEFORE the call (see _prefetch_serp_observations).
        budget.calls_used += len(to_fetch)
        intel: list[SerpIntel] | None
        try:
            intel = keywords_module.fetch_serp_intel(to_fetch)
        except keywords_module.GeminiSerpQuotaError as exc:
            if not budget.rows:
                return None, str(exc)  # exact billing message; envelope stays null
            # Pre-fetched rows are real and already informed the prompt —
            # keep them, attach what matches, and surface the billing note.
            quota_suffix = str(exc)
            intel = None
        except Exception:
            # Provider unreachable/failed: nothing new, but honest about what
            # the pre-fetch DID observe (which may be nothing).
            intel = None
        for row in intel or []:
            budget.rows[_norm(row.keyword)] = row
    if not budget.rows:
        return None, None
    for cluster in clusters:
        rows = [
            budget.rows[_norm(kw)].model_dump(mode="json")
            for kw in cluster.get("seed_keywords") or []
            if isinstance(kw, str) and _norm(kw) in budget.rows
        ]
        if rows:
            cluster["serp_intel"] = rows
    enriched = len(budget.rows)
    skipped = sum(1 for key in budget.requested if key not in budget.rows)
    note = _SERP_NOTE.format(enriched=enriched, skipped=skipped)
    if quota_suffix:
        note = f"{note} {quota_suffix}".strip()
    return "gemini_serp", note


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
    # Mode isolation: fixture drafts never touch provider networks unless a
    # test explicitly injected a provider into the keywords seam.
    enrichment_allowed = _provider_enrichment_allowed(execution_mode)
    # One SERP call budget for the whole draft: the prompt pre-fetch below and
    # the post-generation cluster enrichment share it (and its row cache).
    serp_budget = _SerpBudget()
    prompt_block: str | None = None
    prompt_volume_provider: str | None = None
    if enrichment_allowed:
        prompt_block, prompt_volume_provider = _build_prompt_keyword_intel(pkg, serp_budget)
    inputs = build_inputs(run_id, pkg, keyword_metrics=prompt_block)
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
    clusters = _apply_guards(result.output, evidence_blob)
    method_note = result.output.method_note
    disclaimer = _DISCLAIMER
    keyword_provider: str | None = None
    if enrichment_allowed:
        from .tools import keywords as keywords_module

        serp_label: str | None = None
        volume_label: str | None = None
        if keywords_module.active_serp_provider() is not None:
            # PRIMARY: live SERP intelligence (no volumes, no scores, no
            # re-sort), reusing the pre-fetch cache under the shared cap. A
            # quota degrade keeps the serp label null and surfaces the exact
            # billing note; there is NO silent fallback to volumes.
            serp_label, serp_note = _attach_serp_intel(clusters, serp_budget)
            if serp_note:
                method_note = f"{method_note} {serp_note}".strip()
            # Real volumes (when a volume key is ALSO configured) fed the
            # prompt block only on this path — labeled ONLY if they came back.
            volume_label = prompt_volume_provider
        else:
            # Volume seam (only a volume key configured): metrics + scores +
            # sort, exactly as verified.
            volume_label = _attach_keyword_metrics(clusters)
            if volume_label is not None:
                scoring_note = _SCORING_NOTE.format(provider=volume_label)
                method_note = f"{method_note} {scoring_note}".strip()
            else:
                # Cluster enrichment failed but real volumes may still have
                # informed the prompt — label them, without a scoring note.
                volume_label = prompt_volume_provider
        # Provenance: label + disclaimer reflect what ACTUALLY attached — a
        # provider that produced nothing is never claimed.
        if serp_label and volume_label:
            keyword_provider = f"{serp_label}+{volume_label}"
            disclaimer = _DISCLAIMER_COMBINED.format(provider=volume_label)
        elif serp_label:
            keyword_provider = serp_label
            disclaimer = _DISCLAIMER_SERP
        elif volume_label:
            keyword_provider = volume_label
            disclaimer = _DISCLAIMER_WITH_METRICS.format(provider=volume_label)
    envelope: dict[str, Any] = {
        "run_id": run_id,
        "focal_company": inputs["focal_company"],
        "competitor": inputs["competitor"],
        "generated_at": utcnow().isoformat(),
        "prompt_name": prompt.name,
        "prompt_version": prompt.version,
        "model_id": result.model_id,
        "keyword_provider": keyword_provider,
        "disclaimer": disclaimer,
        "method_note": method_note,
        "clusters": clusters,
    }
    cache.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return envelope
