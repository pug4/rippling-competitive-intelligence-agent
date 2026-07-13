# Eval harness issues — reported, deliberately NOT fixed by the improvement loop

Per campaign guardrails, measurement bugs are logged here for the maintainer; fixing them
inside the loop would move grades without product improvement.

1. Segment-field vocabulary mismatch (red-team finding): the eval labeler and the taxonomy
   use different segment vocabularies, producing 0.06 agreement that the report card
   misdiagnoses as classifier noise rather than harness vocabulary drift. Needs a manual
   harness fix + relabel; do not touch evals/** inside the improvement loop.
2. Human adjudication has been run on 0 of 21 held-out labels — the adjudicated-accuracy
   line is pending a human pass (evals/adjudication.html exists for this).
3. The strategic-usefulness rubric layer was never executed; only validity/grounding layers
   have scores.
