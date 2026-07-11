# Architecture

## One agent, many tools

The system has exactly **one** top-level agent — the **Research Director** — plus
typed tools and structured model calls. This is deliberate: the blueprint and
the assignment both reward a real agentic loop over a "linear script", but
naming every function an agent is neither credible nor maintainable.

```
User → Research Director (stateful loop over DirectorState)
        ├── proposes actions from coverage gaps        (planner.py)
        ├── executes them through typed tools          (tools/*)
        ├── extracts + classifies evidence             (processing/*)
        ├── builds + judges grounded claims            (processing/claim_*)
        ├── verifies temporal change (both periods)    (processing/temporal.py)
        ├── runs the SAME pipeline on the focal company as an isolated sub-run
        │                                              (comparison.py → runner)
        ├── joins message ↔ proof into gaps + actions  (comparison.py, opportunity_engine.py)
        └── renders Markdown / JSON / trace            (report.py)
```

## The loop (graph.py + nodes.py)

A custom typed state machine, not a framework. `NODE_ORDER` lists all 24
blueprint nodes; each is an async `(state, ctx) -> (state, next_node)`. The
driver:

- persists `DirectorState` to `runs.state_json` **after every node** →
  killable and resumable at `state.current_node`;
- appends a trace event per node;
- converts a node crash into a recorded stop reason instead of losing the run.

Why custom over LangGraph: the transition table is fully specified, so a ~100-
line driver gives inspectable transitions, per-node checkpointing, and resume
with zero framework opacity (ADR-0001).

## Deterministic planning (planner.py)

Actions are proposed by rules over coverage state (map site → fetch priority
pages → Wayback for history → Exa news/comparison), scored by a transparent
utility (strategic importance, reliability, cost, latency, recent-failure and
duplicate penalties), and the selection is traced in natural language with the
alternatives considered. A reasoning-model planner is a Phase 4 upgrade behind
the same interface — the loop shape does not change.

## Tool contract (tools/base.py)

Every adapter implements `capabilities()`, `supports()`, `_execute_live()`. The
concrete `execute()` boundary owns feature-gating, fixture/cached/live
dispatch, timeouts, bounded retries on retryable results only, URL safety,
secret redaction, tool-call recording, tracing, and **conversion of every
exception into a typed `ToolResult`** — provider exceptions never propagate.
This is what makes optional sources non-blocking: a failure changes coverage
and limitations, never the run's ability to complete.

## Grounding is enforced in code, not trusted from the model

`processing/normalize.py` is the single normalization path used for both
storing artifact text and verifying excerpts. Extraction and every classifier
family re-check each quoted excerpt with `contains_excerpt` against the
artifact's own text; anything not found verbatim is dropped and counted. The
claim judge sees only a claim's cited evidence. Performance/economics claims
(ROAS, CAC, spend) are rejected as not publicly knowable. Temporal changes
require evidence from both periods.

## Storage (storage/) — lean physical, rich logical

Seven physical SQLite tables (`runs, artifacts, classifications, claims,
opportunities, tool_calls, feedback_events`). Analytical breadth lives in
**versioned Pydantic JSON payloads** inside those tables; the repository
accepts only `BaseModel` instances and validates reads through a
`(schema_name, schema_version)` registry. `tool_calls` doubles as the trace
source and the cached-mode replay key. This keeps the full annotation schema
without migration churn and leaves a clean path to normalized tables later
(ADR-0001, blueprint §40.3).

## Focal-company isolation

A comparative run launches the focal company (default Rippling) as a **separate
`DirectorState` / run_id / trace / artifact namespace** via the same
`create_run` → `drive` path. Cross-company reasoning (gaps, opportunities)
happens only after both sides classify, and references company-qualified IDs.
Multi-competitor portfolio isolation (Phase 5) generalizes this with bounded
concurrency and per-company budgets.

## Model routing (model_gateway.py, config/model_routes.yaml)

A provider-independent gateway does structured output via forced tool-use
(tool `input_schema` = the Pydantic model's JSON schema), one repair retry with
the validation errors, optional escalation to the stronger model, then reject +
trace. Routes map task families to tiers: a fast model for the four staged
classifier families and extraction, a stronger model for planning, temporal
judgment, and opportunity work (ADR-0002). A `FixtureGateway` with the same
interface makes fixture mode key-free and deterministic.
