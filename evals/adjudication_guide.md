# Adjudication guide — evaluation set (36 real Deel + Rippling artifacts)

This guide defines the ground-truth labeling rubric for the evaluation set. It
is **independent of the production classifier prompts** (per the execution
contract's anti-contamination rule): labels are produced by a separate labeling
harness with its own instructions, and the person adjudicating should read only
this guide and the artifact text — never the system's own classifier output.

## Set composition (blueprint §29, §37.34) — AS SHIPPED

The originally planned set was 50 artifacts (Workday 16 / Deel 18 / Gusto 16).
**As shipped, the set is 36 real artifacts — Deel 18 / Rippling 18** — pivoted to
the Deel + Rippling corpus already collected live, to avoid spending budget on
fresh collection purely for the benchmark (decision_log #25). The Workday/Gusto
extension remains an available, documented option, not a silent omission.

- **Deel**: 18 artifacts (newer high-growth platform, the competitor)
- **Rippling**: 18 artifacts (the focal company)
- Spanning homepage, product page, pricing/packaging, comparison, customer proof,
  a paid ad, news, reviews, and ≥1 historical (Wayback) page per company.
- Only **real live/cached** artifacts are used (fixtures are synthetic).

## Dev / held-out split (anti-contamination)

- The split is **deterministic and order-independent**: a hash of the artifact id
  assigns held-out (≈60%) vs dev, so it is stable across rebuilds. Recorded in
  `evals/dataset.jsonl` (`split: dev|heldout`) and frozen before any scoring.
- Prompts may iterate against **dev** only; **held-out numbers are the reported
  accuracy** and prompts are never tuned against held-out.

## Fields to hand-label per artifact

For each, record the label AND the smallest exact excerpt that justifies it
(or `not_observed`). Never infer a capability from silence.

| Field | Definition | Correctness criterion |
|---|---|---|
| primary_message | The single argument given the most prominence | category match |
| secondary_messages | Supporting arguments | set overlap (P/R) |
| salience_band | high / medium / low prominence of the primary | band match ±1 |
| segment | smb / mid_market / enterprise / … | category match |
| persona | organizational role targeted | category match |
| buyer_job | outcome the persona is trying to achieve | human-judged match |
| category_entry_point | the buying trigger/situation | category match |
| funnel_stage | awareness … decision | band match ±1 |
| claim_type | capability/outcome/status/fear/identity/cost/risk/category | category match |
| proof_type | strongest proof offered | category match |
| villain_exact_wording | verbatim status-quo/enemy phrase | exact-substring present |
| competitive_stance | ignores … direct_attack | category match |
| exact_supporting_excerpt | verbatim text backing the primary message | must appear in source |

## What "correct" means (locked before scoring)

- **Category match**: predicted label equals the adjudicated label (case/space
  normalized). Multi-label fields (secondary_messages, proof types) use
  precision/recall/F1.
- **Band ±1**: for ordinal fields (salience, funnel) a one-band difference is
  counted correct but flagged.
- **Excerpt validity**: the predicted excerpt must appear verbatim in the
  normalized source text (the system already enforces this; the eval
  double-checks it).
- **Unsupported-inference rate**: fraction of predicted fields with no
  adjudicated evidence — reported, not hidden.

## Good-enough thresholds (proposed — require sign-off before any number ships)

- Schema validity: 100%.
- Exact-excerpt validity: 100% (system drops unverifiable excerpts by design).
- Primary message, segment, competitive stance: ≥0.75 category accuracy on
  held-out to "move forward"; below that = "needs rework", and the field is
  barred from driving high-confidence opportunities.
- Low-sample fields (< 5 examples per class): spot-check + error analysis only,
  never an aggregate accuracy claim.

## Adjudicator workflow

1. Read the artifact text only (and this guide).
2. Fill each field + excerpt.
3. Flag anything genuinely ambiguous → those go to the reviewer sample.
4. Do not look at the system's classification for the same artifact until after
   you have committed your label.
