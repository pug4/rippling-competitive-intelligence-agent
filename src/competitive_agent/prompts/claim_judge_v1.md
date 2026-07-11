---
name: claim_judge
version: 1.0.0
purpose: Independently judge whether a claim is supported by the supplied exact evidence excerpts.
output_schema: ClaimJudgeVerdict
---
Judge whether the supplied claim is fully supported, partially supported,
unsupported, or contradicted by the supplied exact evidence excerpts. Do not
use outside knowledge. Distinguish direct observation from strategic
inference.

A claim is fully supported only when its wording does not exceed the evidence.
A strategic pivot requires evidence from more than one point in time. A claim
about company-wide strategy should not be accepted from a single isolated
post.

Return:
- verdict: fully_supported | partially_supported | unsupported | contradicted | context_dependent
- if partially supported: the narrowed claim wording that the evidence DOES support
- the specific evidence IDs that drive the verdict
- missing evidence that would change the verdict
- a one-sentence reason

CLAIM UNDER JUDGMENT:
{{ claim_statement }}

EVIDENCE EXCERPTS (id · date · source · exact excerpt):
{{ evidence_block }}
