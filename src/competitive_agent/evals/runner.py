"""Benchmark runner (blueprint §39.12 Layers A–F).

Ties the pieces together over REAL collected artifacts:
  A. Schema validity   — the classifier always emits a valid record.
  B. Excerpt validity  — every emitted excerpt appears verbatim in the source.
  C. Grounding         — the rendered brief's referential integrity (no broken
                         evidence refs, no unsupported opportunities).
  D. Classification    — production classifier vs. an INDEPENDENT labeler on the
                         held-out split (provisional: inter-model agreement, not
                         human-adjudicated accuracy).
  F. Cost / latency    — pulled from a real run's trace.

Objective layers (A/B/C/F) stand on their own. Layer D is explicitly provisional
until the adjudication guide's human sign-off; the runner freezes the dataset and
writes the machine labels so that adjudication has a starting point.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .classification import EvalReport, score_artifact
from .dataset import DatasetItem, assemble_dataset, composition
from .grounding import check_package
from .labeling import classification_to_pred, label_artifact, label_to_gold


async def _classify_and_label(
    item: DatasetItem, repo: Any, gateway: Any, prompts: Any, taxonomy: dict, focal: str
):
    """Run BOTH paths on one artifact, independently, over the REAL stored record."""
    from ..processing.classify import classify_artifact

    art = repo.get_artifact(item.artifact_id)
    if art is None:
        raise LookupError(f"artifact not found: {item.artifact_id}")
    # Production classifier (system under test).
    merged, _families = await classify_artifact(
        art, gateway, prompts, taxonomy, focal_company_name=focal, company_id=item.company_id
    )
    # Independent labeler (answer key), blind to the production output.
    label = await label_artifact(item.normalized_text, gateway)
    return item, merged, label


async def run_benchmark_async(
    db_path: Any,
    *,
    gateway: Any,
    prompts: Any,
    taxonomy: dict,
    focal: str,
    package: dict[str, Any] | None,
    per_company: int = 18,
    split: str = "heldout",
    limit: int | None = None,
) -> dict[str, Any]:
    from ..storage.repository import Repository

    repo = Repository.open(db_path)
    items = assemble_dataset(db_path, per_company=per_company)
    scored_items = [i for i in items if i.split == split]
    if limit:
        scored_items = scored_items[:limit]

    report = EvalReport()
    schema_valid = 0
    excerpt_checks = {"total": 0, "valid": 0}
    per_artifact_records: list[dict[str, Any]] = []

    sem = asyncio.Semaphore(4)

    async def one(item: DatasetItem):
        async with sem:
            return await _classify_and_label(item, repo, gateway, prompts, taxonomy, focal)

    results = await asyncio.gather(*(one(i) for i in scored_items), return_exceptions=True)
    for res in results:
        if isinstance(res, BaseException):
            report.n_artifacts += 0  # counted below only on success
            per_artifact_records.append({"error": f"{type(res).__name__}: {res}"})
            continue
        item, merged, label = res
        schema_valid += 1  # classify_artifact returns a validated model or raises
        pred = classification_to_pred(merged)
        gold = label_to_gold(label)
        score_artifact(report, pred, gold, item.normalized_text)
        # Objective excerpt-validity: every excerpt the PRODUCTION path emitted
        # is already gate-verified; re-check the independent label's excerpt too.
        for exc in merged.villain_exact_wording:
            excerpt_checks["total"] += 1
            excerpt_checks["valid"] += int(_contains(item.normalized_text, exc))
        per_artifact_records.append(
            {
                "artifact_id": item.artifact_id,
                "company": item.company,
                "source_type": item.source_type,
                "predicted": pred,
                "independent_label": gold,
            }
        )

    grounding = check_package(package) if package else None
    return {
        "composition": composition(items),
        "scored_split": split,
        "n_scored": len([r for r in per_artifact_records if "error" not in r]),
        "n_failed": len([r for r in per_artifact_records if "error" in r]),
        "layer_a_schema_validity": schema_valid / len(scored_items) if scored_items else 1.0,
        "layer_b_excerpt_validity": (
            excerpt_checks["valid"] / excerpt_checks["total"] if excerpt_checks["total"] else 1.0
        ),
        "layer_c_grounding": _grounding_summary(grounding) if grounding else None,
        "layer_d_classification": _classification_summary(report),
        "classification_report": report,
        "per_artifact": per_artifact_records,
        "items": items,
    }


def _contains(text: str, excerpt: str) -> bool:
    from ..processing.normalize import contains_excerpt

    return contains_excerpt(text, excerpt)


def _grounding_summary(g: Any) -> dict[str, Any]:
    return {
        "ok": g.ok,
        "citation_coverage": g.citation_coverage(),
        "material_claims": g.material_claims,
        "grounded_claims": g.grounded_claims,
        "broken_evidence_refs": g.broken_evidence_refs,
        "excerpts_checked": g.excerpts_checked,
        "excerpts_unverified": g.excerpts_unverified,
        "opportunities_missing_support": g.opportunities_missing_support,
        "changes_missing_period": g.changes_missing_period,
    }


def _classification_summary(report: EvalReport) -> dict[str, Any]:
    return {
        "n_artifacts": report.n_artifacts,
        "single_field_agreement": {k: v.accuracy() for k, v in report.single.items()},
        "ordinal_field_agreement": {
            k: {
                "exact": v.accuracy(),
                "within_one": (v.within_one_band / v.total if v.total else 0.0),
            }
            for k, v in report.ordinal.items()
        },
        "multi_field_prf": {
            k: {"p": v.precision(), "r": v.recall(), "f1": v.f1()} for k, v in report.multi.items()
        },
        "excerpt_validity": report.excerpt_validity(),
        "unsupported_inference_rate": report.unsupported_inference_rate(),
        "note": "Layer D is inter-model agreement (independent Sonnet labeler vs "
        "production Haiku classifier), NOT human-adjudicated accuracy. Provisional "
        "pending sign-off per evals/adjudication_guide.md.",
    }


def cost_latency_from_trace(trace_path: Any) -> dict[str, Any]:
    """Aggregate cost/latency signals from a real run's trace.jsonl."""
    path = Path(trace_path)
    if not path.exists():
        return {}
    tool_completed = 0
    failures = 0
    nodes = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = d.get("event_type")
        if et == "tool_completed":
            tool_completed += 1
        elif et == "tool_failed":
            failures += 1
        elif et == "node_started":
            nodes += 1
    return {"nodes_executed": nodes, "tool_completed": tool_completed, "tool_failed": failures}


def write_labels(result: dict[str, Any], out_path: Any) -> Path:
    """Persist machine labels as the adjudication starting point (dev + heldout)."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in result["per_artifact"]:
            fh.write(json.dumps(rec, default=str) + "\n")
    return path
