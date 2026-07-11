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
