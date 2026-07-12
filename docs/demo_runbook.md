# Demo runbook

A reviewer should be able to run this without knowing the internals. Every
command is copy-paste. The demo is designed so no single live provider can break
it (§32, §36.12).

## 0. One-time setup

```bash
make install                 # uv sync
cp .env.example .env         # add ANTHROPIC_API_KEY (required for live) and EXA_API_KEY
```

## 1. Deterministic, zero-key demo (always works)

```bash
uv run competitive-agent demo-check --mode fixture
```

Runs the full pipeline on synthetic `example-hr.com` fixtures: resolve →
plan → collect → classify → focal mirror → message–proof gaps → opportunities →
Markdown/JSON/trace. Prints `DEMO CHECK OK`. Output in
`outputs/runs/<RUN_ID>/`.

## 2. Live single-source proof

```bash
uv run competitive-agent analyze deel.com --mode snapshot --execution-mode live
```

Shows a real Exa + website + Anthropic run producing a live brief.

## 3. Full live comparative + longitudinal (the reference scenario)

```bash
uv run competitive-agent analyze deel.com \
  --compare rippling.com --mode comparative --include-longitudinal \
  --execution-mode live
```

Produces the Deel-vs-Rippling brief: current positioning, Wayback historical
comparison, the Rippling mirror, message–proof gaps, and three defensible
actions with backfire risk.

## 4. Inspect the agentic loop (the important part)

```bash
RUN=$(ls -t outputs/runs | head -1)
grep action_selected outputs/runs/$RUN/trace.jsonl   # each choice + alternatives considered
grep -E "fallback_selected|stop_selected" outputs/runs/$RUN/trace.jsonl
cat outputs/runs/$RUN/brief.md
```

The trace shows: a source chosen because of a coverage gap, a fallback when a
source returns nothing, temporal verification, the focal mirror, and a
structured stop reason.

## 4b. Conversation: follow-ups, challenge, retry (preserves the original)

```bash
RUN=$(ls -t outputs/runs | head -1)
uv run competitive-agent ask "$RUN" "why do you believe this? show evidence"
uv run competitive-agent challenge "$RUN"          # child run; adversarial re-read
uv run competitive-agent retry "$RUN" --mode reanalyze_same_evidence
```

`ask` answers from stored evidence first. `challenge`/`retry` create a **child
run** with a difference report and reuse the parent's evidence non-destructively
— the original run is never mutated.

## 4c. Multi-competitor portfolio (isolated + leak-checked)

```bash
uv run competitive-agent portfolio deel.com gusto.com workday.com \
  --compare rippling.com --mode comparative --execution-mode fixture
```

Runs each competitor in its own pipeline, prints `isolation_verified: True`
(a leak would exit non-zero), and synthesizes a cross-company view.

## 4d. Benchmark (grounding + validity + provisional agreement)

```bash
uv run competitive-agent eval --suite all                              # pytest suites
uv run competitive-agent eval-benchmark --package-run "$RUN" --split heldout
cat evals/reports/benchmark_report.md
```

Objective layers (schema/excerpt validity, grounding) are final and pass; the
classification layer is inter-model agreement, clearly marked **provisional**
until human adjudication (`evals/adjudication_guide.md`).

## 5. Failure recovery & safety (deterministic)

Every source adapter converts exceptions into a typed `ToolResult` at the
`tools/base.py` boundary, so a provider failure is a finding, not a crash: it
produces a typed status (`failed_retryable`/`failed_terminal`/`unsupported`/
`skipped_disabled`), a trace event, a coverage/limitation change, and — where a
fallback exists — a fallback action on the next planning cycle. The report still
renders. You can see this in any live run's trace:

```bash
RUN=$(ls -t outputs/runs | head -1)
grep -E "tool_failed|fallback_selected|skipped_disabled" outputs/runs/$RUN/trace.jsonl
```

- **Disabled/unavailable sources** (e.g. Meta/LinkedIn ads, off by default)
  return a traceable non-blocking status — verified in `tests/behavior/`.
- **Prompt injection** in fetched public content is defused by application-level
  gates (verbatim excerpt verification drops fabricated quotes, the URL policy
  blocks SSRF/metadata/loopback URLs, secrets are redacted) — verified in
  `tests/security/test_prompt_injection.py`.

## 6. Optional UI

```bash
make api          # terminal 1 — FastAPI on :8000
make ui-install   # one-time
make ui-dev       # terminal 2 — Vite on :5173
```

Open http://localhost:5173 — Action Board, positioning, coverage, evidence
drill-down over the same JSON packages.

## Demo safety notes

- The Loom should use fixture mode for the guaranteed path, plus one short live
  call for authenticity (§36.12 demo continuity).
- Execution mode is printed on every brief; a synthetic fixture run is never
  presented as live evidence.
