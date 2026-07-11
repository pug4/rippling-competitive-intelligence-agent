# Implementation Status

Honest operational-status manifest (§36.12). Updated at every phase boundary.
Statuses: not_started | interface_only | fixture_only | partial | working |
complete | provider_dependent | unsupported_publicly | blocked

| Capability | Status | Mode | Tested | Report-critical | Known limitation | Next action |
|---|---|---|---:|---:|---|---|
| Schemas + storage + trace | partial | all | in progress | yes | — | finish Phase 0 integration |
| Research Director loop | partial | all | in progress | yes | research nodes are passthroughs | Phase 1/4 fill nodes |
| Execution modes (live/cached/fixture) | partial | all | in progress | yes | — | contract tests |
| Model gateway (Anthropic, forced tool use) | partial | live/fixture | in progress | yes | — | Phase 1 wiring |
| Current website | not_started | — | no | yes | — | Phase 1 |
| Wayback historical | not_started | — | no | yes | archive coverage varies | Phase 1 |
| Exa web/news | not_started | — | no | yes | indexed subset | Phase 1 (key validated live 2026-07-11) |
| Exa LinkedIn | not_started | — | no | no | public indexed corpus only | Phase 2 |
| Similarweb via Exa | not_started | — | no | no | estimated fields vary | Phase 2 |
| Google Ads Transparency | not_started | — | no | no | no public API; creative repository only | Phase 2 best-effort |
| Meta / LinkedIn Ads | not_started | — | no | no | API coverage limits (§39.7) | Phase 2 interface+fixture |
| Reviews / jobs / events / OOH | not_started | — | no | no | discoverable-partial sources | Phase 2 |
| Exa Monitors | not_started | — | no | no | local demo = manual trigger/fixture | Phase 2 |
| Classification (4 staged families) | not_started | — | no | yes | — | Phase 1 minimal, Phase 3 full |
| Claims + grounding + judge | not_started | — | no | yes | — | Phase 1 |
| Temporal engine | not_started | — | no | yes | — | Phase 1 minimal, Phase 3 full |
| Product intelligence (§38) | not_started | — | no | yes | — | Phase 3 |
| Focal (Rippling) mirror | not_started | — | no | yes | — | Phase 1 minimal, Phase 5 full |
| Opportunities + critic | not_started | — | no | yes | — | Phase 5 |
| Conversation / feedback / retry | interface_only | — | no | yes | CLI stubs exit code 2 | Phase 4 |
| Portfolio multi-competitor isolation | not_started | — | no | yes (test) | — | Phase 5 |
| 50-artifact eval | not_started | — | no | yes | dev/held-out split per decision log #8 | Phase 6 (hard stop for sign-off) |
| React UI | not_started | — | no | no | user directive 2026-07-11 | Phase 7 |

## Explicitly deferred (decision, not gap)

- Live scheduled Exa Monitor webhooks on local machine (manual trigger + fixtures instead; §38.17)
- Playwright browser collection (flagged off; §27.5)
- Newsletter/nurture capture, podcast analysis (Level C, interface later)
- Learned opportunity scoring, calibrated probabilistic confidence (production hardening; §31)
