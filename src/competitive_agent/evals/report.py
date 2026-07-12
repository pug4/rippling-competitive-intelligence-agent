"""Render the benchmark result as an honest Markdown report.

Leads with the objective layers (schema/excerpt/grounding), which stand on
their own, and clearly quarantines the classification numbers as provisional
inter-model agreement pending the human adjudication defined in
``evals/adjudication_guide.md``. Weak spots are reported, not hidden.
"""

from __future__ import annotations

from typing import Any


def render_report(
    result: dict[str, Any], *, run_id: str | None = None, cost_latency: dict | None = None
) -> str:
    comp = result["composition"]
    d = result["layer_d_classification"]
    lines: list[str] = []
    lines += [
        "# Evaluation report — classification, grounding, and validity",
        "",
        "> **Status: PROVISIONAL.** Objective layers (schema validity, excerpt "
        "validity, grounding) are final. The **classification layer is inter-model "
        "agreement** between an independent Sonnet labeler and the production Haiku "
        "classifier — it is *not* human-adjudicated accuracy and must not be cited "
        "as such until sign-off per `evals/adjudication_guide.md`.",
        "",
        "## Dataset",
        "",
        f"- Total artifacts assembled: **{comp['total']}** (real live/cached only)",
        f"- By company: {comp['by_company']}",
        f"- By source type: {comp['by_source_type']}",
        f"- By split: {comp['by_split']} — scored split: **{result['scored_split']}**, "
        f"n={result['n_scored']} (failed: {result['n_failed']})",
        "",
        "## Layer A — schema validity (objective)",
        "",
        f"- Valid structured record on **{result['layer_a_schema_validity']:.0%}** of "
        "scored artifacts (a malformed record raises; it never enters the report).",
        "",
        "## Layer B — excerpt validity (objective)",
        "",
        f"- Emitted excerpts present verbatim in source: "
        f"**{result['layer_b_excerpt_validity']:.0%}**. The pipeline drops any "
        "unverifiable excerpt by design; this re-checks that guarantee.",
        "",
    ]

    g = result.get("layer_c_grounding")
    lines += ["## Layer C — grounding / referential integrity (objective)", ""]
    if g:
        lines += [
            f"- Material-claim citation coverage: **{g['citation_coverage']:.0%}** "
            f"({g['grounded_claims']}/{g['material_claims']} claims cite evidence)",
            f"- Broken evidence references: **{len(g['broken_evidence_refs'])}** "
            f"{'✅' if not g['broken_evidence_refs'] else '❌ ' + str(g['broken_evidence_refs'][:5])}",
            f"- Opportunities missing supporting claim/gap: **{len(g['opportunities_missing_support'])}** "
            f"{'✅' if not g['opportunities_missing_support'] else '❌'}",
            f"- Temporal changes missing a period's evidence: **{len(g['changes_missing_period'])}** "
            f"{'✅' if not g['changes_missing_period'] else '❌'}",
            f"- Overall grounding gate: {'**PASS** ✅' if g['ok'] else '**FAIL** ❌'}",
        ]
    else:
        lines.append("- (no rendered package supplied; run with `--package` to score grounding)")
    lines.append("")

    lines += [
        "## Layer D — classification agreement (PROVISIONAL — not accuracy)",
        "",
        f"_{d['note']}_",
        "",
        f"Scored on n={d['n_artifacts']} held-out artifacts.",
        "",
        "| Field | Agreement | ",
        "|---|---:|",
    ]
    for name, acc in sorted(d["single_field_agreement"].items()):
        lines.append(f"| {name} | {acc:.2f} |")
    for name, v in sorted(d["ordinal_field_agreement"].items()):
        lines.append(f"| {name} (exact / ±1 band) | {v['exact']:.2f} / {v['within_one']:.2f} |")
    lines += ["", "| Multi-label field | P | R | F1 |", "|---|---:|---:|---:|"]
    for name, v in sorted(d["multi_field_prf"].items()):
        lines.append(f"| {name} | {v['p']:.2f} | {v['r']:.2f} | {v['f1']:.2f} |")
    lines += [
        "",
        f"- Independent-label excerpt validity: **{d['excerpt_validity']:.2f}**",
        f"- Unsupported-inference rate (predicted where no label evidence): "
        f"**{d['unsupported_inference_rate']:.2f}**",
        "",
    ]

    lines += ["## Weak spots & failure analysis", "", _weak_spots(d, result)]

    if cost_latency:
        lines += [
            "",
            "## Cost / latency (from the reference run's trace)",
            "",
            f"- Graph nodes executed: {cost_latency.get('nodes_executed', '?')}",
            f"- Tool calls completed: {cost_latency.get('tool_completed', '?')} "
            f"(failed: {cost_latency.get('tool_failed', '?')})",
        ]

    lines += [
        "",
        "## Model-route rationale",
        "",
        "- Production classifiers run on tier1 (Haiku) — high-volume bounded "
        "extraction. The independent eval labeler runs on tier2 (Sonnet), a "
        "stronger model, so the answer key is not produced by the system under test.",
        "",
        "## What remains for a final accuracy number",
        "",
        "1. Human adjudication of the held-out labels (`evals/labels.jsonl`) per "
        "the rubric in `evals/adjudication_guide.md`.",
        "2. Re-score production predictions against the adjudicated labels.",
        "3. Only then report per-field accuracy as final.",
    ]
    if run_id:
        lines += ["", f"_Reference run: `{run_id}`._"]
    return "\n".join(lines)


def _weak_spots(d: dict[str, Any], result: dict[str, Any]) -> str:
    weak: list[str] = []
    for name, acc in d["single_field_agreement"].items():
        if acc < 0.6:
            weak.append(
                f"- **{name}**: low agreement ({acc:.2f}) — likely genuine "
                "ambiguity or divergent taxonomies; prioritize for adjudication."
            )
    if d["unsupported_inference_rate"] > 0.1:
        weak.append(
            f"- Unsupported-inference rate {d['unsupported_inference_rate']:.2f} "
            "exceeds 0.10 — the classifier is asserting fields the independent "
            "labeler found no evidence for; investigate."
        )
    if result["n_failed"]:
        weak.append(
            f"- {result['n_failed']} artifact(s) failed classification/labeling "
            "(see per-artifact records)."
        )
    return (
        "\n".join(weak) if weak else "- No field fell below the 0.60 agreement floor on this split."
    )
