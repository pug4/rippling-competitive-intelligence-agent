"""Field-level classification scoring against hand-adjudicated labels.

Correctness criteria are locked in evals/adjudication_guide.md:
- single-label fields: exact category match (normalized);
- multi-label fields: precision / recall / F1 over the label set;
- ordinal fields: exact + within-one-band;
- excerpts: verbatim presence in the source (double-checks the pipeline gate).

Reported numbers must come from the held-out split only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..processing.normalize import contains_excerpt, normalize_text

SINGLE_LABEL_FIELDS = (
    "primary_message",
    "segment",
    "persona",
    "category_entry_point",
    "claim_type",
    "proof_type",
    "competitive_stance",
)
MULTI_LABEL_FIELDS = ("secondary_messages", "proof_types")
ORDINAL_FIELDS = {
    "salience_band": ["low", "medium", "high"],
    "funnel_stage": ["awareness", "consideration", "evaluation", "decision", "retention_expansion"],
}


def _norm(v: Any) -> str:
    return normalize_text(str(v)).casefold()


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0
    within_one_band: int = 0
    confusions: list[tuple[str, str]] = field(default_factory=list)

    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class EvalReport:
    single: dict[str, FieldScore] = field(default_factory=dict)
    multi: dict[str, PRF] = field(default_factory=dict)
    ordinal: dict[str, FieldScore] = field(default_factory=dict)
    excerpt_valid: int = 0
    excerpt_total: int = 0
    unsupported_fields: int = 0
    scored_fields: int = 0
    n_artifacts: int = 0

    def excerpt_validity(self) -> float:
        return self.excerpt_valid / self.excerpt_total if self.excerpt_total else 1.0

    def unsupported_inference_rate(self) -> float:
        return self.unsupported_fields / self.scored_fields if self.scored_fields else 0.0


def score_single(report: EvalReport, name: str, predicted: Any, gold: Any) -> None:
    fs = report.single.setdefault(name, FieldScore())
    if gold in (None, "", "not_observed"):
        return  # no adjudicated label -> not scored
    fs.total += 1
    if _norm(predicted) == _norm(gold):
        fs.correct += 1
    else:
        fs.confusions.append((_norm(gold), _norm(predicted)))


def score_multi(report: EvalReport, name: str, predicted: list, gold: list) -> None:
    prf = report.multi.setdefault(name, PRF())
    pred = {_norm(x) for x in (predicted or [])}
    goldset = {_norm(x) for x in (gold or [])}
    prf.tp += len(pred & goldset)
    prf.fp += len(pred - goldset)
    prf.fn += len(goldset - pred)


def score_ordinal(report: EvalReport, name: str, predicted: Any, gold: Any) -> None:
    order = ORDINAL_FIELDS[name]
    fs = report.ordinal.setdefault(name, FieldScore())
    if gold in (None, "", "not_observed"):
        return
    fs.total += 1
    try:
        gi, pi = order.index(_norm(gold)), order.index(_norm(predicted))
    except ValueError:
        fs.confusions.append((_norm(gold), _norm(predicted)))
        return
    if gi == pi:
        fs.correct += 1
        fs.within_one_band += 1
    elif abs(gi - pi) == 1:
        fs.within_one_band += 1
        fs.confusions.append((_norm(gold), _norm(predicted)))


def score_artifact(report: EvalReport, predicted: dict, gold: dict, source_text: str) -> None:
    report.n_artifacts += 1
    for name in SINGLE_LABEL_FIELDS:
        if name in gold:
            report.scored_fields += 1
            score_single(report, name, predicted.get(name), gold.get(name))
            if predicted.get(name) and gold.get(name) in (None, "", "not_observed"):
                report.unsupported_fields += 1
    for name in MULTI_LABEL_FIELDS:
        if name in gold:
            score_multi(report, name, predicted.get(name, []), gold.get(name, []))
    for name in ORDINAL_FIELDS:
        if name in gold:
            report.scored_fields += 1
            score_ordinal(report, name, predicted.get(name), gold.get(name))
    excerpt = gold.get("exact_supporting_excerpt")
    if excerpt:
        report.excerpt_total += 1
        if contains_excerpt(source_text, excerpt):
            report.excerpt_valid += 1


def render_markdown(report: EvalReport, split: str = "heldout") -> str:
    lines = [
        f"## Classification accuracy ({split}, n={report.n_artifacts})",
        "",
        "| Field | Accuracy | N |",
        "|---|---:|---:|",
    ]
    for name, fs in sorted(report.single.items()):
        lines.append(f"| {name} | {fs.accuracy():.2f} | {fs.total} |")
    for name, fs in sorted(report.ordinal.items()):
        band = fs.within_one_band / fs.total if fs.total else 0.0
        lines.append(f"| {name} (exact / ±1) | {fs.accuracy():.2f} / {band:.2f} | {fs.total} |")
    lines += ["", "| Multi-label field | P | R | F1 |", "|---|---:|---:|---:|"]
    for name, prf in sorted(report.multi.items()):
        lines.append(f"| {name} | {prf.precision():.2f} | {prf.recall():.2f} | {prf.f1():.2f} |")
    lines += [
        "",
        f"- Exact-excerpt validity: {report.excerpt_validity():.2f}",
        f"- Unsupported-inference rate: {report.unsupported_inference_rate():.2f}",
    ]
    return "\n".join(lines)
