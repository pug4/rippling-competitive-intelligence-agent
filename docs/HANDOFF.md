# Handoff — read this first (pre-Loom)

Autonomous overnight work against the red-team review. Everything below is
**committed and verified** unless explicitly marked partial. The guiding rule
all night: *claim only what's true and greppable* — that's what the rubric
rewards and what sank the old version.

## ⚠️ Two things only YOU can unblock (do these before recording)

1. **Raise the Anthropic usage limit.** Your account hit its monthly API usage
   limit mid-session ("regain access on 2026-08-01"), so **live runs and the
   live chat are blocked right now.** Go to **console.anthropic.com → Settings →
   Limits** and raise the monthly cap (or confirm billing). Without this, the
   Loom's *live* demo and the chat won't function — you'd demo the already-
   completed runs instead (which are real and look great, but aren't "watch it
   run live").
2. **GitHub push needs your auth.** There's no remote configured and the `gh`
   token is invalid, so I couldn't push. The code is fully committed locally.
   When you're ready:
   ```bash
   gh auth login                       # 30 seconds, browser
   bash scripts/ship.sh github         # creates the repo + pushes main
   ```
   A **zip fallback already exists** (the assignment accepts either):
   `bash scripts/ship.sh zip` → `outputs/dist/competitive-agent.zip` (no secrets).

## The ideal demo run (do this after step 1)

Re-run `deel.com` live ONCE so a single run has *everything* — model decisions,
real ad creatives, the full corpus, the assignment deliverable:
```bash
uv run competitive-agent analyze deel.com --compare rippling.com --execution-mode live
```
The polished flagship `RUN-b256fab1c1dd` predates the LLM-in-loop, so its trace
has no `decision_by: model`. Grep that on the fresh run (or on the post-loop
runs already in `outputs/`). `docs/loom_script.md` has the full shot list.

## What got done tonight (committed, verified)

| Area | What | Rubric line |
|---|---|---|
| P0 correctness | render-crash, truthful failure states, tool-timeout, temporal-judge-evidence — 4 fixes, both crashed runs re-render | does it work |
| **Architecture** | **LLM in the planning loop** — the model picks the next action with written rationale; verified live on workday (diverged from the scorer 4×); deterministic fallback survived a real gateway outage | **agentic loop** |
| **Tool usage** | **real Google ad creatives via SerpApi** (12 Deel creatives, verified permalinks); Meta = honest typed skip (no dev token) | **ad libraries** |
| Accuracy | (batch 2b) market-context/lawsuit sweep · focal-claims gate that kills "Rippling lacks X" false premises · industry-context inference | does it work |
| Output | four-question assignment brief section, every row cited · downloadable brief+JSON at the top of each run | output quality |
| Conversational | grounded chat · Ask-AI panel on every section · **charts/tables built on demand** (model picks, Python computes) · in-place deeper research | be conversational |
| Decisions | honest Loom rewrite (every claim greppable), systems guide, honest eval framing | decisions/trade-offs |
| Governance | frozen eval surface (additions-only), independent verifier per batch, `docs/improvement_log.md`, `docs/eval_harness_issues.md` | — |

## Known gaps (disclosed, not hidden — own these on camera)

- **Meta ad creatives**: no Facebook dev token → honest typed skip. Google
  creatives are real (SerpApi).
- **Image/text ad copy**: those creatives are rendered images; we mark them
  "copy not machine-readable" rather than OCR-guess.
- **Classification accuracy**: inter-model agreement, labeled *provisional
  pending human adjudication* (0/21 adjudicated). We don't ship an unearned
  accuracy number. `evals/adjudication*` is set up for the human pass.
- **A datetime bug** surfaced on a live workday.com run (offset-naive vs
  offset-aware comparison in the focal-mirror path) — logged in
  `docs/improvement_log.md`; the P0 fix reports it truthfully as `failed`. Deel/
  Gusto/Vanta render clean; use those for the demo.

## Run it (reviewer)
```bash
make install && cp .env.example .env      # fixture needs no keys
uv run competitive-agent demo-check --mode fixture   # zero-key proof
uv run competitive-agent analyze <domain> --compare rippling.com --execution-mode live
make api && make ui-dev                    # UI on :5173
```

## Read next
- `README.md` — overview + run commands
- `docs/SYSTEMS_GUIDE.md` — full architecture (tools, loop, orchestrators, eval, data sources)
- `docs/loom_script.md` + `docs/loom_talking_points.md` — the demo + Q&A
