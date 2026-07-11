"""Evaluation harness: contract, grounding, classification, and behavior checks.

The classification scorer here is deliberately independent of the production
classifier code path — it compares a system output record against a
hand-adjudicated label with locked correctness criteria (see
``evals/adjudication_guide.md``).
"""
