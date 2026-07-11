# Evaluation Report

> **Status: methodology defined; scored numbers pending the Phase 6 hard-stop
> sign-off.** No accuracy number is reported here until the ground-truth set and
> correctness criteria are signed off (per the execution contract). The harness,
> scorers, and dataset structure are in place; see `evals/adjudication_guide.md`.

## Method (locked)

- **Dataset**: 50 artifacts — Workday 16 / Deel 18 / Gusto 16 — across homepage,
  product, pricing, comparison, customer proof, ad, organic post, exec/employee
  post, launch, and ≥1 historical page per company.
- **Split**: dev(20) / held-out(30). Prompts iterate on dev only; **reported
  numbers come from held-out.**
- **Independence**: ground truth is produced by a labeling harness separate from
  the production classifier prompts; the adjudicator sees only the artifact and
  `evals/adjudication_guide.md`, never the system's own output for that artifact.

## Layers (blueprint §39.12)

- **A — Contracts** (target 100%): schema validity, id resolution, timestamps,
  source badges, report renders, no secret leakage, no private-URL access, no
  population claims. Enforced by `tests/contract/` + `evals.grounding`.
- **B — Classification** (`evals.classification`): per-field category accuracy
  (primary message, segment, persona, CEP, claim/proof type, competitive
  stance), P/R/F1 for multi-label fields, ±1-band for ordinals, exact-excerpt
  validity. Low-sample fields get spot checks, not aggregate claims.
- **C — Grounding** (`evals.grounding`): material-claim citation coverage
  (target 100%), broken references (target 0), both-period evidence on temporal
  claims, opportunity support.
- **D — Behavior**: adaptive source choice, skip-with-reason, failure→fallback,
  targeted deepening, budget/runtime stop, challenge, follow-up reuse,
  multi-company isolation. Enforced by `tests/behavior/`.
- **E — Strategic usefulness**: human 1–5 rubric on the top-3 actions
  (specificity, evidence, focal credibility, testability, novelty,
  defensibility, backfire awareness). Target ≥4/5 average.
- **F — Model-route suitability**: per task family, the selected model's schema
  success, grounding, error types, latency, and cost — validating the routes in
  `config/model_routes.yaml` (no vendor bake-off required).

## Known weak spots (updated as scoring runs)

- Fixture mode intentionally has no temporal delta and no claims (evidence ids
  are per-run random) — temporal/claim accuracy is measured in live/cached mode.
- Product §38 depth and optional ad/LinkedIn sources are Phase 2–3; their eval
  rows are marked provider-dependent.
