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
