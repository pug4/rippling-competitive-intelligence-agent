# Assignment traceability

Maps each Rippling Growth Marketing Assignment requirement to where it is
implemented and how it is verified. Status reflects the current build (updated
per phase). "✔" = implemented + tested; "◐" = implemented, depth continuing;
"○" = scaffolded/deferred with an explicit status.

| Assignment requirement | Where | Verification | Status |
|---|---|---|---|
| Accept competitor name **or** domain | `nodes._resolve_offline`, `cli.analyze` | resolves name and domain; ambiguous input asks the user | ✔ |
| Work for **any** competitor (not hardcoded) | generic schemas/adapters/prompts; taxonomy + focal company in `config/` | runs on Deel, Gusto, Workday, example-hr with no code change | ✔ |
| Public info only | `security.url_is_allowed`, robots, no login/auth | URL-policy unit tests; private/loopback rejected | ✔ |
| Research **multiple sources** | website map+fetch, Wayback, Exa web/news/comparison | a run uses ≥3 source classes; trace records source diversity | ✔ |
| Paid ad creative & messaging | Google/Meta/LinkedIn ad adapters | interface + fixtures; live best-effort | ○ (Phase 2) |
| Website positioning, product pages, pricing | website adapters + product/pricing classifier family | pricing-disclosure + product signals classified | ✔ |
| Social presence & content themes | Exa public-web/LinkedIn discovery | LinkedIn adapter | ○ (Phase 2) |
| Recent launches / press / announcements | Exa news adapter + launch signals | news artifacts classified; launch validation | ◐ |
| **Real agentic loop** (decide what to research, skip, move on) | `graph.py` + `planner.py` + coverage model | trace shows conditional `action_selected` w/ alternatives, skips, stop reason | ✔ |
| Handle "not every source has data" | typed `ToolResult`, non-blocking failures, negative observations | forced-failure behavior tests; empty → negative observation | ✔ |
| **Conversational** (clarifying Qs, follow-ups) | `conversation.py`, `await_followup`, CLI `ask`/`deepen`/`challenge`/`retry`/`feedback` | follow-up router answers from state; retry = child run + diff; behavior tests | ✔ |
| Markdown brief: messaging angles & themes | `report.render_markdown`, message family | brief renders dominant message + villains + stance | ✔ |
| Markdown brief: how they position products | product family + `synthesis.product_positioning` | product-positioning + CEP-ownership sections render | ✔ |
| Markdown brief: what changed recently | temporal engine + change events | both-period change events; theme-emergence w/ caveats | ✔ |
| Markdown brief: **positioning gaps/opportunities for Rippling** | focal mirror + message–proof gaps + opportunity engine | gaps + 3 actions with backfire risk cite both companies | ✔ |
| JSON: sources, claims, confidence, timestamps | `report.build_json_package` | JSON validates; claims carry status/confidence/evidence | ✔ |
| Multi-competitor comparison | `portfolio.py`, CLI `portfolio` | isolated pipelines + leakage gate; 3-competitor test | ✔ |
| Run locally | `make install`, `demo-check`, CLI | fixture demo runs with zero keys from clean clone | ✔ |
| Code deliverable (repo, runnable) | whole repo | `make quality` green | ✔ |
| Agent output for ≥1 competitor | reference Deel-vs-Rippling run `RUN-60b37e044954` | saved brief + JSON + trace in `docs/reference_scenario/` | ✔ |
| Loom: architecture, tools/models, evals, live demo | `docs/loom_script.md`, `ARCHITECTURE.md`, ADRs, `docs/demo_runbook.md`, eval report | script + runbook + trace + evals ready (recording is the user's step) | ◐ (materials ready) |
| Graceful tool failures | `tools/base.py` boundary | exception → typed result; fallback in trace | ✔ |
| Eval system | `evals/` (dataset, labeling, runner, report, grounding, classification) | benchmark runs; Layers A/B/C objective + pass; Layer D provisional pending adjudication | ◐ (accuracy # pending human sign-off) |

## Release gate

A requirement row is submission-ready when it has a passing test + user-visible
artifact, OR an explicit deferred status that does not block the required
report. Rows marked ○/◐ are tracked in `IMPLEMENTATION_STATUS.md` with their
next action.
