# Systems Guide — Competitive Marketing Intelligence Agent

A complete, high-level tour of how the agent works: every tool it can call, the
agentic decision loop, the reasoning layers, the multi-agent orchestrators, the
conversational layer, the honesty engineering, the eval system, the data
sources, and where it goes next. Written so a technical reviewer can map any
claim in the Loom to code.

> **The one design principle everything follows:** *models do bounded per-item
> work (classify one page, judge one claim, pick the next action, draft one
> play); Python does everything comparative and numeric (shares, ownership,
> gaps, temporal reconciliation, rankings, every chart).* That split is why a
> re-render is deterministic, why verdicts can't drift between reads, and why a
> hallucinated number physically cannot reach the brief.

---

## 1. The shape of the system

```
input: competitor name or domain
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  RESEARCH DIRECTOR  — a checkpointed 25-node state machine          │
│  assess coverage → propose actions → SCORE + LLM-pick next action   │
│  → execute a typed tool → normalize → classify → verify evidence    │
│  → update coverage → judge claims/contradictions/temporal changes   │
│  → build matrices → run the Rippling MIRROR → generate + critique   │
│  opportunities → decide continue / deepen / stop → render outputs   │
└───────────────────────────────────────────────────────────────────┘
        │                                   │
        ▼                                   ▼
  deterministic outputs               conversational layer
  (brief.md · data.json · trace)      (grounded chat · Ask-AI panel ·
                                       in-place deeper research ·
                                       tool-called graphs)
```

Everything is one Python package (`src/competitive_agent/`), driven by a custom
typed state machine (`graph.py` + `nodes.py`) over a checkpointed
`DirectorState` — killable and resumable at any node. Storage is a lean SQLite
store (`storage/`) with versioned Pydantic JSON payloads. A React UI
(`ui/`, bonus per the rubric) and a self-contained HTML dashboard both render
the *same* validated JSON package — they implement no analytics of their own.

---

## 2. The agentic loop — how it decides what to research

This is the heart of the "architecture" rubric line: it is a real agent that
makes decisions, not a linear script.

**The cycle (one iteration):**
1. **`assess_coverage`** — score every research *dimension* (current messaging,
   pricing, paid media, social/LinkedIn, launches, temporal baseline, focal
   proof, …) as `high` / `medium` / `low` / `unavailable` / `not_attempted`
   from what has actually been collected and classified so far.
2. **`propose_actions`** — generate candidate research actions (rule-based) for
   the thin dimensions: map the site, fetch priority pages, sample Wayback,
   search news/launches, pull ad creatives, enrich Similarweb, research
   LinkedIn, mine reviews, etc. Each candidate is a typed `ResearchAction` with
   a source, parameters, and a rule rationale.
3. **`score_actions`** — a deterministic utility function ranks the candidates
   (reliability × coverage-need × freshness, minus cost/latency), with an
   iteration-ramped floor so never-attempted Level-B sources (reviews, ads)
   stop starving. **Then the reasoning model enters the loop:** the
   `plan_research` route (Sonnet) receives the coverage state, the top-K scored
   candidates, and the *recent tool outcomes* (what just succeeded / came back
   empty / failed) and **picks / reorders / skips the next action with a
   written rationale**, or decides to **stop** when the goal is met. The model
   may only choose from the deterministically-generated candidate set (it can
   never invent an action), and the **deterministic scorer is the fallback** on
   any model failure/timeout/invalid output. The chosen action, the model's
   rationale, and the alternatives it passed over are all written to
   `trace.jsonl` (`decision_by: "model" | "heuristic"`). *This is why two
   different competitors produce genuinely different research paths.*
4. **`select_next_action` → `execute_action`** — run the chosen tool through the
   typed tool boundary (§3). A provider failure or empty source is a *typed
   result*, not a crash; it is recorded and the loop reacts to it next cycle.
5. **`normalize_and_deduplicate` → `extract_and_classify` → `validate_evidence`
   → `update_coverage`** — clean and hash-dedup artifacts, classify each
   page/post, verbatim-verify every extracted quote against stored source text,
   recompute coverage.
6. **`decide_continue_or_stop`** — continue, or stop for an *explainable reason*
   (required coverage reached / budget-bounded / runtime-bounded), disclosed in
   the brief.

**Interactive clarifying questions:** identity resolution asks the user when a
name is ambiguous; the machinery generalizes to source-failure decisions
("couldn't get Meta ads — try LinkedIn instead?") — interactive runs surface a
`pending_decision` and pause (`awaiting_user`); batch runs auto-fall-back and
record the choice in the trace and brief.

**Stop / resume / durability:** every node transition checkpoints the full
typed state to SQLite. A run survives page refreshes and server restarts; an
orphaned run (worker died) is resumable from its last checkpoint; a node crash
is recorded `status='failed'` with the failing node preserved and an honest
error-path brief — a crashed run is never reported "complete."

---

## 3. Every tool the agent can call

All 15 tools implement one `BaseTool` contract (`tools/base.py`) that owns
feature-gating, fixture/cached/live dispatch, per-tool timeouts, bounded
retries, URL/SSRF safety (re-validated per redirect hop), robots.txt respect,
secret redaction, and **typed `ToolResult` conversion of every exception** — so
"the tool failed" is data the loop reasons about, never a crash. A missing
provider degrades to a typed `unsupported`/`skipped` result, never fabricated
data.

| Tool | Source | What it collects | Notes |
|---|---|---|---|
| `WebsiteMapTool` | first-party | sitemap → prioritized page list | robots-respecting |
| `WebpageFetchTool` | first-party | current product/pricing/customer pages | SSRF-safe fetch |
| `WaybackTool` | Wayback CDX | historical snapshots with **real capture dates** | the temporal baseline |
| `ExaSearchTool` | Exa | neural + domain-scoped discovery, news/launches, date-windowed prior sampling, LinkedIn-scoped search | retrieval only |
| `ExaAgentTool` | Exa Agent | per-post LinkedIn extraction (real post URLs) | 190s tool-timeout override |
| `ExaContentsTool` | Exa | crawler fallback when a first-party fetch fails (HTTP failure only, never a robots bypass) | provenance-labeled |
| `SimilarwebTool` | Exa → Similarweb | estimated monthly visits, channel mix, digital competitors | **estimates, labeled** |
| `GoogleAdsTool` | **SerpApi** (preferred) → Exa web (fallback) | **real Google ad creatives** — advertiser-verified, formats, run dates, video-ad copy | Transparency permalinks; image/text copy honestly marked "not machine-readable" |
| `MetaAdsTool` | Meta Ad Library API (token) → Exa web | Meta creatives + EU impression buckets | typed skip without a token |
| `LinkedInAdsTool` | — | LinkedIn ad presence | best-effort; typed skip (no stable API) |
| `ReviewsTool` | Exa | G2/Capterra/TrustRadius buyer-voice pages | feeds review mining |
| `JobsTool` | Exa | hiring signals | Level-B, feature-flagged |
| `EventsTool` | Exa | events/webinars | Level-B |
| `OOHTool` | Exa | out-of-home / brand campaigns | Level-B |
| `KeywordsTool` | Gemini SERP grounding (or SEMrush seam) | live People-Also-Ask + related searches (real SERP observations) | volumes/CPC never invented |

**Honesty boundary on ads (the rubric's first-listed source):** creatives,
formats, regions, run dates, and (Meta) impression *buckets* are claimable when
the library shows them. Exact bid keywords, CPC, commercial spend, and
CTR/CVR/ROAS are **never** claimable — never emitted, never inferred. Google's
Transparency Center exposes no spend/impressions for commercial ads at all, and
we claim none.

---

## 4. Classification & reasoning (the model layers)

**Per-item classification (tier-1, Haiku, schema-forced):** every page/post is
classified in four families — message/argument, audience/funnel/CEP,
product/pricing/launch, competitive/villain/proof — plus buyer-voice mining on
review pages. Every extracted excerpt is **containment-verified verbatim**
against the stored page text; a quote that isn't in the page is dropped and
logged. The application is the accuracy gate, not the model.

**Reasoning / judgment (tier-2, Sonnet):**
- **`plan_research`** — the in-loop planner (§2): picks the next action.
- **`judge_temporal`** — decides whether a change is real; requires evidence in
  **both** periods and is fed real dated evidence *excerpts* (not bare IDs);
  emergence claims are reconciled against the final corpus.
- **`build_claims` / `judge_claim`** — an independent judge decides whether each
  strategic claim is supported by *only* its cited evidence; unsupported claims
  are rejected, not softened. Banned economics (ROAS/CAC/spend) can never ship.
- **`generate_opportunities` / `critique_opportunities`** — draft Rippling-
  relative plays, then adversarially critique them (genericness + superiority +
  backfire filters).
- **`analysis_chat`** — the grounded conversational agent (§6).

---

## 5. Deterministic synthesis & honesty engineering

Everything comparative on screen is **counted, not generated**, in
`synthesis.py` / `comparison.py` / `report.py`:
- **Share-normalized ownership** — a 12-page niche site compares fairly against
  a 116-page corpus; ownership needs both a share ratio *and* a minimum page
  count, else it renders "insufficient sample."
- **Outlier floors** — an ATTACK verdict needs ≥5 pages or ≥15% of the corpus;
  thin themes are flagged `THIN`, never inflated.
- **Temporal reconciliation** — a theme can't be called "emerging" if the run's
  own baseline saw it earlier; it's relabeled "expanding."
- **Disclosed absence** — "we looked and found nothing" is rendered, never
  hidden. Coverage, source quality, and claim confidence are separate fields.
- **Corpus normalization banner** — fires when corpora differ >3× or either side
  is under 20 pages.

**The Rippling mirror:** the focal company runs through the *same* pipeline in
an isolated sub-run, so every "them vs us" number uses symmetric methodology,
and every recommendation is checked against what Rippling already says and can
prove (with backfire/counterattack risk on each play).

---

## 6. The conversational layer

- **Grounded chat** — answers follow-ups from *only* the run's collected
  evidence, cites the sources it grounded in, flags when it needs deeper
  research, and asks a clarifying question when the ask genuinely forks.
- **In-place deeper research** — when the stored data can't answer, the chat
  emits a `research_request`; one click re-drives the **same run** scoped to the
  requested sources (a planner source-allowlist), appends new evidence, re-runs
  classification + verification + reconciliation, and re-renders — the tabs you
  are looking at grow in place. Handles "now dig deeper on pricing" and
  "run this again for Gusto."
- **Ask-AI + tool-called graphs** — an "✦ Ask AI" affordance on every section
  opens a context panel that carries the run's chat history and the clicked
  element's context. The chat can **build new graphs/tables on demand**: the
  model chooses *what* to chart (`viz_request`); Python computes every number
  deterministically from the real package (8 builders: theme distribution, CEP
  ownership, proof gaps, temporal changes, persona×channel, product verticals,
  ad creatives, group-by) — a model-supplied value is ignored, so a fabricated
  chart can't reach the screen.

---

## 7. Multi-agent orchestration patterns

The product itself orchestrates several agents:
- **The focal mirror** — a second, isolated pipeline run on Rippling, joined for
  symmetric comparison.
- **The portfolio coordinator** — `portfolio deel.com gusto.com …` runs each
  competitor in its own isolated pipeline (own run_id / state / budget / trace),
  proves **no cross-company evidence leakage** (a detected leak is a hard
  error), and synthesizes a cross-company view.
- **Retry / challenge / deepen children** — follow-ups create child runs with a
  difference report and reuse the parent's evidence non-destructively (a
  `run_artifacts` junction lets runs share artifacts without reassigning rows).

*(Engineering note: this codebase was also built and hardened with a multi-agent
workflow harness — parallel builder agents on disjoint file sets under a shared
interface contract, then independent adversarial verifier agents that recompute
every claim against reality and re-grade. The eval/test surface is frozen so
grades can only move when the product actually improves.)*

---

## 8. The eval system (what the Loom explains)

Layered, and **honest about which layers are objective**:
- **Objective & final:** schema/excerpt validity and the grounding gate (a
  material claim with no verbatim evidence, or a dangling evidence id, fails the
  build). These verify the pipeline's own invariants.
- **Provisional:** classification quality is measured by inter-model agreement
  (an independent Sonnet labeler vs. the production Haiku classifier) and is
  labeled **pending human adjudication** — the report never presents it as final
  accuracy. Known measurement issues (e.g. a segment-vocabulary mismatch
  between labeler and taxonomy) are logged in `docs/eval_harness_issues.md`, not
  silently "fixed," so any grade movement reflects real product improvement.
- **Red-team loop:** independent adversarial reviewers fact-check brief content
  against the live web and recompute every displayed number; findings drive a
  frozen-surface improvement backlog (`docs/improvement_log.md`).

---

## 9. Data sources (all public)

First-party website (sitemap + pages + pricing) · Wayback Machine CDX
(historical snapshots, real capture dates) · Exa (neural search, domain-scoped
discovery, news/launches, date-windowed prior sampling, LinkedIn post
extraction) · SerpApi Google Ads Transparency Center (real Google ad creatives)
· Meta Ad Library API (Meta creatives, when a token is present) · Similarweb via
Exa (estimated traffic/channels/competitors) · Gemini + Google Search grounding
(live SERP intelligence: People-Also-Ask, related searches) · G2/Capterra/
TrustRadius review pages (buyer voice) · public news/press. **Public information
only — no logins, no paywalls, no personal data beyond public posts by named
employees.**

---

## 10. Future improvements

- **Scheduled re-runs + run-over-run diffs as alerts** ("Deel added decision
  content in benefits this week") — the child-run/diff machinery already exists.
- **Deeper keyword/SEO joins** (real search volumes via a paid API behind the
  existing provider seam) so paid-search plays carry real demand numbers.
- **CRM win/loss joins** so plays close the loop to revenue.
- **Broader ad coverage** — Meta token by default; a browser path for Google
  Transparency image-ad OCR; more third-party ad providers behind the seam.
- **Dynamic taxonomy inference** — auto-adapt the vertical/CEP taxonomy to a
  brand-new industry rather than extending the config.
- **A real React error boundary** and streaming (SSE) for the live view.

---

## 11. Things to think through before shipping something like this

- **Make the application the accuracy gate, not the model** — verbatim
  containment + grounding gates mean a hallucination can't reach the output.
  This is the single highest-leverage decision.
- **Separate "the model decides" from "the code computes"** — it makes every
  number reproducible and every verdict stable across reads.
- **Design for absence** — "we looked and found nothing" is a finding; never let
  a collection gap read as a real-world fact.
- **Normalize for corpus size everywhere** — raw counts lie across a 12-page vs
  116-page comparison.
- **Type your tool failures** — a provider 402/timeout/empty is data the loop
  reasons about; a circuit breaker skips a dead provider's remaining tools.
- **Freeze your measuring stick** — if the eval can be edited to pass, the grade
  is meaningless; additions-only + independent graders keep it honest.
- **Bound your spend** — per-run budget, runtime, iteration, and tool-call caps,
  with an explainable stop reason.
- **Never claim what isn't public** — spend, CPC, impressions, performance are
  not publicly knowable for commercial ads; say so instead of estimating.
