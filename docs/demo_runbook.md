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

## 5. Failure recovery (deterministic)

Fixture mode includes empty-source, rate-limit, timeout, malformed-output, and
prompt-injection fixtures. A forced provider failure produces a typed result, a
trace event, a coverage/limitation change, and a fallback — the report still
renders.

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
