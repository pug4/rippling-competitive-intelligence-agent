# Implementation Status

Honest operational-status manifest (§36.12). Updated at every phase boundary.
Statuses: not_started | interface_only | fixture_only | partial | working |
complete | provider_dependent | unsupported_publicly | blocked

| Capability | Status | Mode | Tested | Report-critical | Known limitation | Next action |
|---|---|---|---:|---:|---|---|
| Schemas + storage + trace | complete | all | yes | yes | — | — |
| Research Director loop | working | all | yes | yes | rule-based proposals; LLM planner deferred | Phase 4 |
| Execution modes (live/cached/fixture) | working | all | yes | yes | — | — |
| Model gateway (Anthropic, forced tool use) | working | live/fixture | yes | yes | — | — |
| Current website (map + fetch) | working | live/fixture | yes | yes | static fetch; JS pages may need browser | Phase 2 |
| Wayback historical | working | live/fixture | yes | yes | archive coverage varies | — |
| Exa web/news/comparison | working | live/fixture | yes | yes | indexed subset (key validated 2026-07-11) | — |
| Classification (4 staged families) | working | live/fixture | yes | yes | product §38 depth in Phase 3 | Phase 3 |
| Claims + grounding + judge | working | live/fixture | yes | yes | fixture mode has no claims (random EV ids) | — |
| Temporal engine | working | live/fixture | yes | yes | scalar dims; fixture mode has no delta | Phase 3 |
| Focal (Rippling) mirror | working | live/fixture | yes | yes | isolated sub-run; matrices in Phase 5 | Phase 5 |
| Message–proof gaps + opportunities | working | live/fixture | yes | yes | adversarial critic pass deferred | Phase 5 |
| Exa LinkedIn | not_started | — | no | no | public indexed corpus only | Phase 2 |
| Similarweb via Exa | not_started | — | no | no | estimated fields vary | Phase 2 |
| Google Ads Transparency | not_started | — | no | no | no public API; creative repository only | Phase 2 |
| Meta / LinkedIn Ads | not_started | — | no | no | API coverage limits (§39.7) | Phase 2 |
| Reviews / jobs / events / OOH | not_started | — | no | no | discoverable-partial sources | Phase 2 |
| Exa Monitors | not_started | — | no | no | local demo = manual trigger/fixture | Phase 2 |
| Product intelligence (§38) | interface_only | — | no | yes | schemas present; extraction Phase 3 | Phase 3 |
| Opportunity critic | not_started | — | no | yes | §37.26 rejection rules | Phase 5 |
| Conversation / feedback / retry | interface_only | — | no | yes | CLI stubs exit code 2 | Phase 4 |
| Portfolio multi-competitor isolation | interface_only | — | no | yes (test) | schemas present | Phase 5 |
| 50-artifact eval | not_started | — | no | yes | dev/held-out split per decision log #8 | Phase 6 |
| React UI | not_started | — | no | no | user directive 2026-07-11 | Phase 7 |

## Explicitly deferred (decision, not gap)

- Live scheduled Exa Monitor webhooks on local machine (manual trigger + fixtures instead; §38.17)
- Playwright browser collection (flagged off; §27.5)
- Newsletter/nurture capture, podcast analysis (Level C, interface later)
- Learned opportunity scoring, calibrated probabilistic confidence (production hardening; §31)
