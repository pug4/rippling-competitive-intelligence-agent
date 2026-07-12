# Evaluation Report

> **Status: benchmark RUN; objective layers final, classification layer
> provisional pending human adjudication.** Objective results (schema/excerpt
> validity, grounding) are reported below. The classification-accuracy number is
> deliberately withheld until the held-out labels are human-adjudicated per the
> execution contract. The generated, always-current report is
> `evals/reports/benchmark_report.md` (produced by `competitive-agent
> eval-benchmark`); this file is the human-readable methodology + summary.

## Method (as shipped)

- **Dataset**: **36 real artifacts — Deel 18 / Rippling 18** — spanning homepage,
  product, pricing, comparison, customer proof, paid ad, news, reviews, and
  historical (Wayback) pages. This is a **pivot from the originally planned 50
  (Workday 16 / Deel 18 / Gusto 16)** to the real Deel + Rippling corpus already
  collected live, to avoid spending budget on fresh collection purely for the
  benchmark (recorded in `docs/decision_log.md` #25). Only real live/cached
  artifacts are used — fixtures are synthetic and would not test the classifier
  on real language. The dataset is frozen to `evals/dataset.jsonl`.
- **Split**: deterministic, order-independent by a hash of the artifact id
  (held-out ≈ 60%). Prompts iterate on dev only; **reported numbers come from
  held-out.**
- **Independence**: ground-truth candidate labels are produced by an independent
  labeling harness (`evals/labeling.py`) with a from-scratch prompt on a
  *stronger* model tier (Sonnet) than the classifier under test (Haiku); it never
  references the production classifier prompts or sees production output for the
  artifact it labels. Final ground truth requires human adjudication per
  `evals/adjudication_guide.md`.

## Results (held-out, this run — see `evals/reports/benchmark_report.md`)

- **Layer A — schema validity: 100%.** Every scored artifact produced a valid
  structured record.
- **Layer B — excerpt validity: 100%.** Every emitted excerpt appears verbatim in
  source (the pipeline drops any that don't).
- **Layer C — grounding: PASS.** 100% material-claim citation coverage, 0 broken
  evidence references, 0 unsupported opportunities, 0 temporal changes missing a
  period's evidence, on the live Deel-vs-Rippling reference brief.
- **Layer D — classification: PROVISIONAL (inter-model agreement, NOT accuracy).**
  Meaningful only on closed-vocabulary fields: `claim_type` 0.71,
  `competitive_stance` 0.67. Free-form fields (primary_message, persona, CEP,
  proof_type) score ~0 by construction (exact match on paraphrases) and require
  human adjudication or semantic scoring — this is a scoring-metric limitation,
  not a classifier defect. **No accuracy number is claimed here.**

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
