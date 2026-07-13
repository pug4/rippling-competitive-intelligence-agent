# Competitive Marketing Intelligence Agent

A **conversational AI agent** that takes a competitor's **name or domain** and
produces an evidence-grounded analysis of their **public marketing strategy and
positioning** — how they message and position now, how it changed, how it
compares to a configurable focal company (default **Rippling**), and what
defensible campaigns that surfaces. Built for the Rippling GTM take-home.

**Works for any competitor in the space — not hardcoded to one company.** Public
information only; no logins, no paywalls. The design bias throughout: *show the
evidence, or don't make the claim.*

> New here? Read **`docs/SYSTEMS_GUIDE.md`** for the complete architecture tour
> (every tool, the agentic loop, the reasoning layers, the orchestrators, the
> eval system, the data sources) and **`docs/loom_script.md`** for the demo
> walkthrough.

---

## Quick start

```bash
make install                      # uv sync
cp .env.example .env              # keys are documented inside; fixture needs NONE

# 1) Deterministic, ZERO-KEY demo (synthetic fixtures — proves the pipeline):
uv run competitive-agent demo-check --mode fixture

# 2) Live analysis (real public sources + models — needs ANTHROPIC_API_KEY + EXA_API_KEY):
uv run competitive-agent analyze deel.com --compare rippling.com --execution-mode live

uv run competitive-agent render RUN_ID        # re-render a stored run
```

Outputs land in `outputs/runs/<RUN_ID>/`: **`brief.md`** (the markdown brief),
**`data.json`** (the structured package — sources, claims, confidence,
timestamps), and `trace.jsonl` (every decision + its rationale).

**Provider keys** (all documented in `.env.example`): `ANTHROPIC_API_KEY` +
`EXA_API_KEY` are required for a live run. Optional, each degrading to a typed
honest skip when absent: `SERPAPI_API_KEY` (real Google ad creatives),
`GEMINI_API_KEY` (live SERP intelligence), `META_ADS_ACCESS_TOKEN` (Meta
creatives). **Fixture mode needs no keys at all.**

---

## What it does

- **A real agentic loop (not a script).** A checkpointed 25-node state machine
  assesses coverage, proposes candidate actions, and — this is the point — a
  **reasoning model picks the next action** given the coverage state, the scored
  candidates, and what just succeeded or came back empty, with a written
  rationale (`decision_by: model` in the trace); a deterministic scorer is the
  fallback. Two different competitors produce genuinely different research
  paths. It stops for an explainable reason.
- **Multi-source public collection.** Current website (sitemap → prioritized
  fetch), Wayback historical snapshots (real capture dates), Exa web/news/
  comparison discovery, LinkedIn post extraction, **real Google ad creatives via
  the Ads Transparency Center (SerpApi)**, Similarweb traffic estimates, review
  buyer-voice, and an adversarial **market-context** sweep (litigation, funding,
  M&A, launches — what's happening *to* the competitor, not just what they say).
- **The application is the accuracy gate, not the model.** Every quoted excerpt
  is verbatim-verified against stored source text; a grounding gate rejects any
  claim not supported by its cited evidence; spend/CPC/ROAS are banned as not
  publicly knowable. A hallucinated number physically cannot reach the brief.
- **Rippling-relative, corpus-normalized.** The focal company runs through the
  *same* pipeline in an isolated mirror, so every "them vs us" number is
  symmetric; comparisons are share-normalized so a 12-page niche site reads
  fairly against a 116-page corpus; a **focal-claims gate** verifies any "Rippling
  lacks X" recommendation against Rippling's own corpus before it ships.
- **Conversational.** A grounded chat answers follow-ups from only the run's
  evidence, asks clarifying questions, **builds new charts/tables on demand**
  (the model picks what to chart; Python computes every number), and can launch
  **scoped deeper research in-place** ("now dig deeper on pricing" / "run this
  again for Gusto").
- **Structured, downloadable output.** A markdown brief answering the four
  assignment questions (messaging themes · product positioning · what changed ·
  gaps/plays for Rippling) with every claim cited, plus the JSON package — both
  downloadable from the top of any completed run.

---

## Deliverables produced per run

- **`brief.md`** — the competitive brief (Action Board + the four-question
  "assignment deliverable" section, every row cited to a source artifact).
- **`data.json`** — the full structured package (sources, extracted claims,
  confidence levels, timestamps, all classifications, `assignment_answers`).
- **`trace.jsonl`** — the agent's decision log (rationale + alternatives per step).
- A **UI** (bonus) and a self-contained **HTML dashboard**, both rendering the
  same validated package.

A reference brief for a real competitor lives in `docs/reference_scenario/`.

---

## Conversation, portfolio, retries

```bash
uv run competitive-agent ask RUN_ID "what is their most attackable message-proof gap?"
uv run competitive-agent deepen RUN_ID --focus pricing        # scoped deeper collection
uv run competitive-agent challenge RUN_ID --claim CLAIM_ID     # adversarial re-read
uv run competitive-agent portfolio deel.com gusto.com workday.com --compare rippling.com
```

Each portfolio competitor runs in an isolated pipeline with a proven
**no-cross-company-leakage** guarantee.

---

## Visuals (UI + dashboard)

```bash
uv run competitive-agent visualize RUN_ID   # self-contained dashboard.html (no server)
make api                                    # FastAPI on :8000 serving stored packages
make ui-install && make ui-dev              # React UI on :5173 (proxies /api → :8000)
```

The UI adds an "✦ Ask AI" panel on every section (context-aware chat + on-demand
charts) and a top-of-run download bar for the brief + JSON.

---

## The eval system (honest about which layers are objective)

```bash
uv run competitive-agent eval --suite all                    # pytest suites
uv run competitive-agent eval-benchmark --package-run RUN_ID  # grounding/validity/agreement
```

Schema/excerpt validity and the grounding gate are **objective and final**.
Classification quality is **inter-model agreement pending human adjudication** —
the report says so and never presents it as final accuracy. Known measurement
issues are logged in `docs/eval_harness_issues.md`, not silently fixed, and the
whole system was hardened by an independent red-team loop over a **frozen** test
surface (`docs/improvement_log.md`) — grades only move when the product improves.

---

## Honesty rules the system enforces

- Observed-corpus language, never population claims.
- Coverage, source quality, and claim confidence are separate fields.
- Archive/collection absence is never treated as real-world absence.
- Not-publicly-knowable fields (spend, CPC, impressions, performance) are marked
  unavailable, never estimated. Ad libraries show creatives + run status, never
  the keyword buy or the budget.
- A node crash is recorded `failed` (never "complete") with an honest error brief.

## Execution modes

- `live` — calls enabled providers, writes fresh artifacts.
- `cached` — replays previously stored provider results by args hash.
- `fixture` — deterministic synthetic data; **runs with no API keys.**

## Development

```bash
make quality     # format, lint (ruff), typecheck (mypy), tests, eval-smoke, demo-fixture
```
