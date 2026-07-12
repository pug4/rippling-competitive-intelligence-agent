# Decision Log

Running log of non-trivial judgment calls, per the execution contract: every
classification rule, excluded source, threshold, or deviation gets an entry.
Format: date · decision · rationale · reversibility.

## 2026-07-11

1. **Stack: custom typed state machine over LangGraph.** The §37.13 transition
   table is fully specified; a ~100-line driver gives inspectable transitions,
   per-node checkpointing, and resume without framework abstraction (which
   §37.6 warns against). Reversible (nodes are plain async functions).
2. **Model gateway: raw `anthropic` SDK + forced tool use for structured
   output**, one repair retry, optional escalation. No LiteLLM (single
   provider) or Instructor (30 lines we must control anyway for fixture mode).
3. **Storage: stdlib `sqlite3` + repository layer**, 7 physical tables with
   versioned Pydantic JSON payloads (§40.3). SQLModel rejected: fights the
   payload pattern, adds a dependency.
4. **URL fields are `str`, not `pydantic.HttpUrl`.** Real public URLs (odd
   ports, unicode, tracking params, archive prefixes) fail strict parsers;
   collection must never drop evidence over URL pedantry. URL *safety* is
   enforced separately at fetch time (security.py).
5. **`RipplingProof`/`RipplingVulnerability` renamed `FocalProof`/
   `FocalVulnerability`; graph nodes named `load_focal_state` /
   `run_focal_mirror_check`.** User directive: focal company and competitors
   must be swappable across industries. Rippling remains the configured
   default (`config/default.yaml → focal_company`).
6. **Old Exa key (May 2025) validated live on 2026-07-11** with one 1-result
   search call (HTTP 200). Anthropic key provided by user; both stored only in
   gitignored `.env` (mode 600).
7. **UI will be a React app (user directive), built in Phase 7** after the
   intelligence package is stable. It consumes the same validated
   `CompanyResearchPackage` JSON as Markdown/JSON renderers — no separate
   analytical logic (§40.6 data rule).
8. **Benchmark anti-contamination:** 50-artifact set split dev(20)/held-out(30);
   prompts iterate against dev only; held-out scored at checkpoints and is the
   reported accuracy. Ground truth produced by a labeling harness independent
   of production classifier prompts; user adjudicates ~15 + all flagged items.
   (User-approved 2026-07-11.)
9. **Eval-set artifact collection is a separate code path** from research
   collection prompt development, so benchmark construction never feeds
   classifier tuning context.
10. **Checkpoint style (user-approved):** hard stops only after Phase 1
    (vertical-slice accuracy review) and at Phase 6 (eval methodology
    sign-off); other phases report-and-continue.
11. **Schema transcription deviations (Phase 0 builders):** `company` →
    `company_id` in CommercialMotionProfile/CategoryEntryPoint/CreativeCluster/
    OOHEvidence for consistency with §37.9 models; EventPresence gained
    `presence_id` + `company_id` (blueprint §14.3 had no identifier — persisted
    records must be traceable); `rippling_*` field names kept verbatim inside
    MarketingOpportunity/MessageProofGap/CategoryEntryPoint (focal-generic
    rename applied only to focal.py + graph node names — the JSON contract
    keeps blueprint field names; four touchpoints documented if we globalize
    later); salience score is computed by the application, classifiers return
    raw salience evidence (§37.19); enums use StrEnum (py3.12+).
12. **Anthropic key initially rejected (401, truncated paste); user re-sent
    full key, validated live 2026-07-11.** Both providers confirmed working.

### Phase 1 judgment calls (2026-07-11)

13. **Classify a normalized `primary_theme` (from taxonomy) alongside the
    free-form `primary_message`.** The gap engine needs a claim observed ≥2×;
    free-form messages are unique per page so nothing ever repeated (live Deel
    run produced 0 gaps from 19 classifications). Gaps + temporal now group on
    the theme. Threshold: a theme must appear in ≥2 artifacts to be a gap.
14. **A theme's proof strength = MODAL per-page strength, not the union.**
    Unioning proof types across a theme's pages made every theme "strong" (one
    quantified-outcome page inflated the whole theme), which the old gap builder
    then skipped → 0 gaps. Modal per-page strength is honest and ties break
    toward the weaker rating.
15. **Surface all repeated themes as gaps, ranked by attackability — don't drop
    strongly-proven ones (§22.9).** A well-proven competitor theme is still an
    opportunity to reframe around a structural advantage; the interpretation
    tells the reader to reframe/concede rather than attack head-on.
16. **Page-fetch bounding (quality + cost):** deprioritize non-English locale
    paths (/es/, /sv-se/…, score 0.05), seed canonical English paths, fetch only
    priority pages (score ≥0.4), cap total fetched at 12/company. Without these
    the agent drowned in localized blog posts and never reached Wayback/mirror.
17. **Focal mirror runs LATE (after competitor coverage sufficient) and
    budget-bounded** (½ remaining runtime, 12 iterations, 40 tool calls). It ran
    on iteration 1 before and starved the competitor's own collection.
18. **Claim judging parallelized (semaphore), capped at 10 evidence-richest
    candidates.** Sequential judging over 24 evidence items stalled the loop.
19. **Model gateway repair retry re-sends the task as one clean user turn** with
    the validation error appended — replaying the assistant tool_use turn
    without a matching tool_result block caused an Anthropic 400 on every repair
    path. Added a 90s per-request asyncio timeout (a hung call had frozen a run).
20. **Live runtime budget raised to 1200s**; the sitemap URL-list artifact is
    never classified (a 500-URL blob wasted the whole budget on one nonsense
    call); classifier input capped at 12k chars (excerpt verification still uses
    full raw text, so grounding is unaffected).
21. **JSON deliverable drops full raw HTML** (keeps provenance + a 1500-char
    normalized excerpt): the reference package went 21MB → 294KB.
22. **Retry reuses parent evidence via a `run_artifacts` junction table, never
    by re-saving.** Artifacts are keyed by `artifact_id` with a single `run_id`
    column, so the original implementation's `save_artifact(child, parent_art)`
    silently REASSIGNED the parent's rows to the child (parent 14 → 0 artifacts
    on retry — it destroyed the analysis being retried). Fix: a junction table
    lets multiple runs reference one artifact; `list_artifacts` unions owned +
    linked; a `reuse_evidence` graph action links (not copies) the parent's rows
    and the planner proposes no new collection under `reuse_evidence_only`.
    Regression test asserts the parent's artifact count is unchanged after a
    retry.
23. **Portfolio isolation is structural, verified after the fact — not assumed.**
    Each company run already gets its own `run_id`/`DirectorState`/`GraphContext`
    (fresh `scratch`)/trace/budget, and every read filters by `run_id`. Rather
    than trust that, `assert_no_cross_company_leakage` checks every package
    references only its own `company_id`; a leak is a hard error (non-zero exit).
    Added `PRAGMA busy_timeout=5000` so concurrent company writes wait rather
    than erroring under WAL.
24. **Benchmark: objective layers stand; classification is provisional until
    human adjudication.** The independent labeler is a from-scratch prompt on a
    STRONGER tier (Sonnet) than the classifier under test (Haiku), and never
    sees production output — but machine-vs-machine numbers are *agreement*, not
    accuracy. The eval report leads with the objective layers (schema/excerpt
    validity, grounding) and loudly marks Layer D provisional pending sign-off
    per `evals/adjudication_guide.md`. Dataset built from REAL live/cached
    artifacts only (fixtures are synthetic); dev/held-out split is a stable hash
    of the artifact id, frozen before scoring.
25. **Eval set pivoted from Workday/Deel/Gusto to the real Deel + Rippling
    corpus** (179 live artifacts already collected) to avoid burning budget on
    fresh collection for the benchmark; the composition still spans homepage /
    product / pricing / comparison / proof / ads / news / reviews / historical
    (wayback). The Workday/Gusto extension is a documented, uncommitted option,
    not a silent omission.
26. **Benchmark finding: `segment` inter-model agreement (0.06) is a
    taxonomy/convention mismatch, not a classifier defect.** Production commits
    to a *primary* segment and uses finer labels (`startups`, `global_teams`)
    while the independent labeler collapses multi-audience pages to `mixed`
    (11/20 held-out). The only truly vocabulary-aligned agreement signals are
    `claim_type` (0.71) and `competitive_stance` (0.67). Free-form fields
    (`primary_message`, `persona`, CEP, `proof_type`) score ~0 by construction
    (exact match on paraphrases). Consequence: the reported classification
    numbers are agreement, not accuracy; human adjudication (or semantic
    scoring) is required before any accuracy claim — the report says so and the
    weak-spots section only flags aligned closed-vocab fields as real.
