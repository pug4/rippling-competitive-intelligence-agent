# Improvement log — red-team remediation campaign

Governance: the measuring stick is FROZEN. `git diff` on tests/** and evals/** must be
additions-only; eval datasets/labels/thresholds/split/grading logic untouched. Grades may
move only because the product improved. Measurement bugs go to docs/eval_harness_issues.md,
never silently fixed. No fixture results cited as quality evidence; verification is live.

Baseline (pre-campaign): red-team grade C+ (2026-07-13). Verified-true findings:
report.py assignment-composer dict-slice crash (Gusto run, error-path render recovered);
crashed runs can show status=complete; exa_agent 150s poll inside 60s tool boundary (live
research_linkedin dead); temporal judge fed bare artifact IDs (everything low-confidence);
plan_research route has zero call sites (no LLM in the planning loop); no adversarial news
layer (Rippling–Deel litigation invisible); focal-lacks-X claims unverified against the
focal corpus (3 false premises in flagship recommendations); zero live ad creatives
(Meta needs token; Google Transparency needs a browser path; Exa can't index facebook.com).

| date | item | outcome | evidence | spend |
|---|---|---|---|---|
