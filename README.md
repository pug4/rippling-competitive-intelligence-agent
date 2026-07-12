# Competitive Marketing Intelligence Director

A conversational research agent that takes a **competitor name or domain** and
produces a structured, evidence-grounded analysis of that company's **public
marketing strategy and positioning** — how they message and position now, how
that has changed over time, how it compares to a configurable **focal company**
(default: Rippling), and which defensible marketing actions that surfaces.

Built for the Rippling GTM take-home. Public information only. Not hardcoded to
any competitor — the focal company, competitor list, and taxonomy are all
configuration.

## What it does

- **One stateful Research Director** (not a linear script): assess coverage →
  identify gaps → propose actions → score & select → execute a typed tool →
  update evidence/coverage → decide continue / deepen / fall back / stop. Every
  decision is recorded in `trace.jsonl` with its rationale and the alternatives
  considered.
- **Public evidence collection**: current website (sitemap map → prioritized
  page fetch), Wayback historical snapshots (real capture dates), Exa web / news
  / comparison-page discovery. Optional adapters (Similarweb, ad libraries,
  LinkedIn, reviews, jobs, events, OOH, monitors) are feature-flagged and
  non-blocking — a provider failure never breaks the report.
- **Full-width classification** in four staged families (message/argument;
  audience/funnel/CEP; product/pricing/launch; competitive/villain/proof). Every
  quoted excerpt is re-verified verbatim against stored source text — the
  application is the accuracy gate, not the model.
- **Grounded claims**: an independent judge decides whether each claim is
  supported by only its cited evidence; unsupported claims are rejected, not
  softened. Performance/economics (ROAS, CAC, spend) are rejected as not
  publicly knowable.
- **Temporal analysis** that requires evidence from **both** periods before
  asserting a change, with lifecycle states and alternative explanations.
- **Focal (Rippling) mirror**: the same pipeline runs on the focal company in an
  isolated sub-run; message–proof gaps and opportunities are always checked
  against what the focal company already says and can prove, with backfire and
  counterattack risk on every recommendation.
- **Deterministic outputs**: Markdown brief (Action Board first), JSON package,
  and trace — all rendered from validated stored records.

## Quick start

```bash
make install                      # uv sync
cp .env.example .env              # add ANTHROPIC_API_KEY and EXA_API_KEY

# Deterministic, zero-key demo (synthetic fixtures):
uv run competitive-agent demo-check --mode fixture

# Live analysis (real public sources + models):
uv run competitive-agent analyze deel.com \
  --compare rippling.com --mode comparative --include-longitudinal \
  --execution-mode live

uv run competitive-agent render RUN_ID      # re-render a stored run
```

Outputs land in `outputs/runs/<RUN_ID>/` (`brief.md`, `data.json`, `trace.jsonl`).

### Follow-ups, challenges, and retries (conversation)

Every follow-up preserves the original run — a retry creates a **child run** with
a difference report, and reuses the parent's evidence non-destructively (a
`run_artifacts` junction lets runs share artifacts without reassigning rows):

```bash
# Grounded analysis chatbot — answers follow-ups ONLY from the run's findings,
# flags when it needs deeper research, and suggests next questions:
uv run competitive-agent ask RUN_ID "what is their most attackable message-proof gap?"
uv run competitive-agent deepen RUN_ID --focus pricing      # collect deeper evidence
uv run competitive-agent challenge RUN_ID --claim CLAIM_ID   # adversarial re-read
uv run competitive-agent retry RUN_ID --mode reanalyze_same_evidence
uv run competitive-agent feedback RUN_ID --thumbs-down --reason "too generic"
```

### Multi-competitor portfolio (isolated pipelines)

```bash
uv run competitive-agent portfolio deel.com gusto.com workday.com \
  --compare rippling.com --mode comparative
```

Each competitor runs in its own isolated pipeline (own run_id / state / budget /
trace); the coordinator validates each package against quality gates, proves
**no cross-company evidence leakage**, and synthesizes a cross-company view. A
detected leak is a hard error (non-zero exit).

### Benchmark (grounding + validity + provisional classification agreement)

```bash
uv run competitive-agent eval-benchmark --package-run RUN_ID --split heldout
uv run competitive-agent eval --suite all     # pytest suites
```

Writes `evals/{dataset.jsonl,labels.jsonl,reports/benchmark_report.md}`. Objective
layers (schema/excerpt validity, grounding) are final; the classification layer
is inter-model agreement (independent Sonnet labeler vs. production Haiku
classifier) and is **provisional pending human adjudication** per
`evals/adjudication_guide.md` — the report says so loudly and never presents it
as final accuracy.

### Visuals (graphs of the data & gaps)

Two ways to see the analysis as charts — both from the same validated JSON
package, no separate analytics (§40.6):

```bash
# 1. Self-contained HTML dashboard for any run (opens anywhere, no server):
uv run competitive-agent visualize RUN_ID     # writes outputs/runs/RUN_ID/dashboard.html

# 2. Interactive React UI:
make api          # FastAPI on :8000 serving stored packages
make ui-install   # one-time: npm install in ui/
make ui-dev       # Vite dev server on :5173 (proxies /api → :8000)
```

Both render: **source mix**, **top message themes**, **competitive-stance**
distribution, the **message–proof gaps** (competitor vs. Rippling proof strength,
color-coded by attackability), and the **persona × channel coverage heatmap**,
plus the Action Board, positioning, strategy-over-time, coverage, and evidence
drill-down.

## Execution modes

- `live` — calls enabled providers, writes fresh artifacts.
- `cached` — replays previously stored provider results by args hash.
- `fixture` — deterministic synthetic data; **runs with no API keys**. Clearly
  labeled as synthetic in the brief.

## Architecture (one paragraph)

A custom typed state machine (`graph.py` + `nodes.py`) drives 24 explicit nodes
over a checkpointed `DirectorState` — killable and resumable at any node. Source
adapters implement one `BaseTool` contract (`tools/`) that owns feature-gating,
fixture/cached/live dispatch, timeouts, bounded retries, URL safety, secret
redaction, and typed-`ToolResult` conversion of every exception. A provider-
independent model gateway (`model_gateway.py`) does structured output via forced
tool-use with one repair retry; prompts are versioned files (`prompts/`).
Persistence is a lean 7-table SQLite store with versioned Pydantic JSON payloads
(`storage/`) — analytical breadth lives in the schema, not the table count. See
`ARCHITECTURE.md`, `docs/adr/`, and `IMPLEMENTATION_STATUS.md`.

## Honesty rules the system enforces

- Observed-corpus language, never population claims ("observed in 8 of 42
  artifacts", not "42% of spend").
- Coverage, source quality, and claim confidence are separate fields, never one
  score.
- Archive/collection absence is never treated as real-world absence.
- Campaign persistence is never reported as performance.
- Fields that are not publicly knowable are marked unavailable, not estimated.

## Development

```bash
make quality     # format, lint (ruff), typecheck (mypy), tests, eval-smoke, demo-fixture
make test        # pytest
```
