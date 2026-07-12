# Implementation Status

Honest operational-status manifest (§36.12). Updated at every phase boundary.
Statuses: not_started | interface_only | fixture_only | partial | working |
complete | provider_dependent | unsupported_publicly | blocked

| Capability | Status | Mode | Tested | Report-critical | Known limitation | Next action |
|---|---|---|---:|---:|---|---|
| Schemas + storage + trace | complete | all | yes | yes | — | — |
| Research Director loop | working | all | yes | yes | rule-based proposals (deterministic + inspectable by design); LLM planner is a deferred enhancement, not a gap | — |
| Execution modes (live/cached/fixture) | working | all | yes | yes | — | — |
| Model gateway (Anthropic, forced tool use) | working | live/fixture | yes | yes | — | — |
| Current website (map + fetch) | working | live/fixture | yes | yes | static fetch; JS pages may need browser | Phase 2 |
| Wayback historical | working | live/fixture | yes | yes | archive coverage varies | — |
| Exa web/news/comparison | working | live/fixture | yes | yes | indexed subset (key validated 2026-07-11) | — |
| Classification (4 staged families) | working | live/fixture | yes | yes | + primary_theme, supporting_themes, claim_specificity | — |
| Synthesis quality (authority weighting, dominant msg, corpus skew, proof dist) | working | live/fixture | yes | yes | reviewer R1/R2/R3/R6 fixes applied | — |
| Claims + grounding + judge | working | live/fixture | yes | yes | judging parallelized + capped | — |
| Temporal engine | working | live/fixture | yes | yes | scalar + theme-emergence; deeper Wayback sampling | — |
| Commercial motion (inferred + rendered) | working | live/fixture | yes | yes | public-signal inference only | — |
| Product positioning / CEP ownership / persona×channel matrix | working | live/fixture | yes | yes | deterministic aggregations (#18/#21/#22) | — |
| Focal (Rippling) mirror | working | live/fixture | yes | yes | isolated, budget-bounded, runs late | — |
| Message–proof gaps + opportunities (diverse, staged, critics) | working | live/fixture | yes | yes | genericness + superiority critics live | — |
| Similarweb via Exa | working | live/fixture | yes | no | estimated fields vary; provider-dependent | — |
| Reviews / jobs / events / OOH | working | live/fixture | yes | no | discoverable-partial; non-representative labeled | — |
| Google Ads Transparency | best_effort | live/fixture | yes | no | no public API; creative repository only | — |
| Meta / LinkedIn Ads | interface_only | fixture | yes | no | US commercial UI-only; API political/EU only (§39.7) | — |
| Exa LinkedIn | not_started | — | no | no | public indexed corpus only | Phase 2 remainder |
| Exa Monitors | not_started | — | no | no | local demo = manual trigger/fixture | Phase 2 remainder |
| Product intelligence deep (§38 entities/launch/gaps) | interface_only | — | no | yes | schemas present; deterministic positioning done | Phase 3 remainder |
| Opportunity critic (genericness + superiority) | working | live/fixture | yes | yes | adversarial multi-vote in Phase 5 | Phase 5 |
| Conversation / feedback / retry | working | live/fixture | yes | yes | retry reuses parent evidence non-destructively (run_artifacts junction) | — |
| Portfolio multi-competitor isolation | working | live/fixture | yes | yes (test) | structural isolation + leakage gate; 3-competitor test green | — |
| Benchmark harness (grounding/validity/agreement) | working | live/cached | yes | yes | Layers A/B/C/F objective; Layer D = inter-model agreement, provisional | Human adjudication |
| 50-artifact eval accuracy number | partial | live/cached | yes | yes | dataset+labels frozen; **held-out accuracy pending human adjudication** | User adjudication |
| React UI (Vite) + FastAPI | working | all | build | no | ActionBoard/Positioning/Evidence/Coverage; richer sections deferred | polish |

## Remaining before a final accuracy number can be reported

The benchmark harness runs and produces objective results (schema/excerpt
validity, grounding) plus **provisional** classification agreement. Per the
anti-contamination contract, the reported held-out accuracy requires human
adjudication of `evals/labels.jsonl` against `evals/adjudication_guide.md`
before any per-field accuracy number is presented as final. This is the one
outstanding item; everything else is working.

## Explicitly deferred (decision, not gap)

Surfaced by the coverage audit against the internal blueprints (§36–40); these
are self-imposed stretch items, none of them a take-home requirement.

- **Static `focal_company/` assets** (`rippling_sources.yaml`,
  `rippling_proof_inventory.json`, `rippling_vulnerabilities.json`, §37.4):
  intentionally NOT shipped — focal grounding is done at runtime via the
  focal-mirror re-analysis (fresher and generic to any focal), so pre-baked
  Rippling files would be stale and non-generic. Directory left empty by design.
- **Deep §38 product-entity layer** — `ProductPortfolioSnapshot`/
  `ProductLaunchEvent` schemas exist but are not instantiated end-to-end, so
  `product_portfolios[]`/`launches[]` are empty; product intel ships today via
  `product_positioning` + `category_entry_points`. The launch-validation loop
  (announced-vs-available) is a one-shot news search, not the full validator.
- **§38 product/monitor/portfolio DB tables** — only the 7 core tables exist;
  portfolio runs are in-memory (not persisted). Exa Monitors + webhook receiver
  interfaces are defined, not wired.
- Exa LinkedIn / dedicated social-presence adapter (§38.17, §39.7) — social /
  content-theme coverage is approximated via Exa web+news; no dedicated adapter.
- Playwright browser collection (flagged off; §27.5)
- Newsletter/nurture capture, podcast analysis (Level C, interface later)
- Learned opportunity scoring, calibrated probabilistic confidence (§31)
- Eval Layer E human strategic-usefulness rubric + human-adjudicated accuracy
  number (withheld pending sign-off, by design)
- Richer UI sections (commercial motion, CEPs, persona×channel) — markdown brief
  has all; UI shows the core + Strategy-over-time (user directive: UI last)

## Verified by the coverage audit (Jul 2026)

Original **take-home** requirements: 0 not-covered (all covered or partial). The
gaps above are all from the internal blueprints. Test coverage now includes
`tests/security/` (prompt-injection defenses), `tests/golden/` (output-contract
regression guard — `eval --suite regression` was previously empty), and
`tests/behavior/test_failure_handling.py` (typed non-blocking failures +
skip-with-reason). Docs reconciled: EVAL_REPORT/adjudication guide now state the
real 36-artifact Deel+Rippling set (not the planned 50), and the demo runbook no
longer claims fixtures that don't exist.
