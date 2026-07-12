# Evaluation report — classification, grounding, and validity

> **Status: PROVISIONAL.** Objective layers (schema validity, excerpt validity, grounding) are final. The **classification layer is inter-model agreement** between an independent Sonnet labeler and the production Haiku classifier — it is *not* human-adjudicated accuracy and must not be cited as such until sign-off per `evals/adjudication_guide.md`.

## Dataset

- Total artifacts assembled: **36** (real live/cached only)
- By company: {'rippling.com': 18, 'deel.com': 18}
- By source type: {'exa_web': 6, 'reviews': 6, 'news': 6, 'comparison': 3, 'webpage': 6, 'google_ads': 6, 'wayback': 3}
- By split: {'dev': 15, 'heldout': 21} — scored split: **heldout**, n=21 (failed: 0)

## Layer A — schema validity (objective)

- Valid structured record on **100%** of scored artifacts (a malformed record raises; it never enters the report).

## Layer B — excerpt validity (objective)

- Emitted excerpts present verbatim in source: **100%**. The pipeline drops any unverifiable excerpt by design; this re-checks that guarantee.

## Layer C — grounding / referential integrity (objective)

- Material-claim citation coverage: **100%** (10/10 claims cite evidence)
- Broken evidence references: **0** ✅
- Opportunities missing supporting claim/gap: **0** ✅
- Temporal changes missing a period's evidence: **0** ✅
- Overall grounding gate: **PASS** ✅

## Layer D — classification agreement (PROVISIONAL — not accuracy)

_Layer D is inter-model agreement (independent Sonnet labeler vs production Haiku classifier), NOT human-adjudicated accuracy. Provisional pending sign-off per evals/adjudication_guide.md._

Scored on n=21 held-out artifacts.

**Read this table correctly.** Agreement is computed by exact category match. That is meaningful only for **closed-vocabulary** fields (`claim_type`, `competitive_stance`, ordinal bands). For **free-form** fields (`primary_message`, `persona`, `category_entry_point`, `proof_type`, `secondary_messages`) exact-match agreement is near-zero *by construction* — two correct paraphrases of the same message score as a disagreement. A 0.00 there is a scoring-metric limitation, **not** evidence the classifier is wrong; those fields require human adjudication (or semantic scoring), which is exactly why the number is provisional.

| Field | Kind | Agreement | 
|---|---|---:|
| category_entry_point | free-form | 0.00 |
| claim_type | closed-vocab | 0.71 |
| competitive_stance | closed-vocab | 0.67 |
| persona | free-form | 0.00 |
| primary_message | free-form | 0.00 |
| proof_type | free-form | 0.00 |
| segment | closed-vocab | 0.06 |
| funnel_stage (exact / ±1 band) | ordinal | 0.14 / 0.52 |
| salience_band (exact / ±1 band) | ordinal | 0.19 / 0.19 |

| Multi-label field | Kind | P | R | F1 |
|---|---|---:|---:|---:|
| proof_types | free-form | 0.00 | 0.00 | 0.00 |
| secondary_messages | free-form | 0.00 | 0.00 | 0.00 |

**Interpretable signal (closed-vocabulary only):** claim_type 0.71, competitive_stance 0.67, segment 0.06.
- Independent-label excerpt validity: **1.00**
- Unsupported-inference rate (predicted where no label evidence): **0.07**

## Weak spots & failure analysis

- **segment** (closed-vocab): low agreement (0.06) — a real signal here; prioritize for adjudication and error analysis.
- Free-form fields (category_entry_point, persona, primary_message, proof_type) show near-zero exact-match agreement — expected for paraphrasable text; needs semantic scoring or human adjudication, not a classifier fix.

## Cost / latency (from the reference run's trace)

- Graph nodes executed: 257
- Tool calls completed: 13 (failed: 1)

## Model-route rationale

- Production classifiers run on tier1 (Haiku) — high-volume bounded extraction. The independent eval labeler runs on tier2 (Sonnet), a stronger model, so the answer key is not produced by the system under test.

## What remains for a final accuracy number

1. Human adjudication of the held-out labels (`evals/labels.jsonl`) per the rubric in `evals/adjudication_guide.md`.
2. Re-score production predictions against the adjudicated labels.
3. Only then report per-field accuracy as final.

_Reference run: `RUN-60b37e044954`._