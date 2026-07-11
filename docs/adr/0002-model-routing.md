# ADR 0002: Model routing

**Status:** accepted · 2026-07-11

**Context.** §28/§37.28: two-tier routing, model IDs are configuration, task
suitability is validated by quality gates, no vendor bake-off required.

**Decision.** `config/model_routes.yaml` maps task families to tiers:
- **tier1 = claude-haiku-4-5** — high-volume bounded extraction and the four
  staged classifier families. Rationale: repeated constrained structured
  output where schema adherence and cost dominate; the eval (Phase 6)
  validates schema validity, excerpt support, and field accuracy on this
  route.
- **tier2 = claude-sonnet-5** — research planning, temporal judgment,
  contradiction resolution, commercial/product-motion inference, opportunity
  generation and critique, challenge mode. Rationale: ambiguous high-impact
  reasoning; latency/cost acceptable at low call volume.
- **escalation = tier2** — a tier1 output that fails validation after one
  repair retry may be retried once on tier2 before rejection.

Routes are re-validated by eval Layer F (§39.12) with per-route schema
success, grounding, latency, and cost reported in EVAL_REPORT.md.
