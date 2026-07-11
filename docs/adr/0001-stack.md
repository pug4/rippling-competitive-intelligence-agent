# ADR 0001: Implementation stack

**Status:** accepted · 2026-07-11

**Context.** Blueprint §37.6/§40.4 grant tool freedom bound by behavioral
contracts: inspectable loop, resumable state, validated boundaries, fixture
execution without keys, provider isolation.

**Decisions.**
- **Orchestration: custom typed state machine** (`graph.py`). The §37.13
  transition table is fully specified; a small driver gives named-node
  checkpointing and resume with zero framework opacity. Alternatives:
  LangGraph (rejected: learning/debug cost inside 24h, hides transitions).
- **Models: raw `anthropic` SDK** behind an internal gateway with forced
  tool-use structured output + one repair retry + escalation route.
  Alternatives: LiteLLM (rejected: single provider), Instructor (rejected:
  must control the repair path for fixture/cached modes anyway).
- **Storage: stdlib sqlite3** + repository with versioned Pydantic JSON
  payloads in 7 physical tables (§40.3). Alternatives: SQLModel/SQLAlchemy
  (rejected: ORM fights the payload pattern).
- **Collection: httpx + asyncio semaphores**, stdlib robotparser, Wayback CDX.
- **CLI: Typer. Rendering: Jinja2/deterministic Python. UI: React (user
  directive), consuming the same validated JSON package (§40.6).**

**Consequences.** Fewer dependencies to debug under deadline; every behavior
the blueprint requires observable in plain code; provider SDK objects cannot
leak past adapters (repository/tool contract enforce typed boundaries).
