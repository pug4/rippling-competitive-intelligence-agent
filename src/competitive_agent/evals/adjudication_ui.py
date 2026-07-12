"""Human adjudication UI + scorer (the final step before a reported accuracy).

Generates a self-contained local HTML page from the frozen machine labels
(``evals/labels.jsonl``): for each held-out artifact the adjudicator sees the
SOURCE TEXT (per the guide: read only the artifact + the rubric), then for each
key field the production prediction vs the independent label, and picks which is
correct. Export downloads ``adjudicated.json``; ``score_adjudication`` turns it
into the FINAL per-field production accuracy and appends it to the benchmark
report. No external hosts; plain inline JS.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

# The decision-relevant fields worth human time (guide: low-sample fields get
# spot checks, not aggregate claims).
ADJUDICATED_FIELDS = [
    "primary_message",
    "segment",
    "persona",
    "claim_type",
    "proof_type",
    "competitive_stance",
]

_CHOICES = [
    ("production", "Production correct"),
    ("independent", "Independent correct"),
    ("both", "Both acceptable"),
    ("neither", "Both wrong"),
    ("skip", "Can't judge"),
]


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else "—"))


def build_adjudication_html(labels_path: Path, repo: Any, limit: int = 21) -> str:
    recs = [json.loads(line) for line in labels_path.read_text().splitlines() if line.strip()]
    recs = [r for r in recs if "predicted" in r][:limit]

    cards: list[str] = []
    for i, r in enumerate(recs, 1):
        art = repo.get_artifact(r["artifact_id"])
        text = (art.normalized_text if art else "")[:2500]
        url = art.url if art else ""
        rows = []
        for f in ADJUDICATED_FIELDS:
            pred = r.get("predicted", {}).get(f)
            gold = r.get("independent_label", {}).get(f)
            radios = "".join(
                f"<label><input type='radio' name='{r['artifact_id']}::{f}' value='{v}'> {t}</label>"
                for v, t in _CHOICES
            )
            rows.append(
                f"<tr><td class='fld'>{_esc(f.replace('_', ' '))}</td>"
                f"<td class='pv'><b>P:</b> {_esc(pred)}</td>"
                f"<td class='pv'><b>I:</b> {_esc(gold)}</td>"
                f"<td class='radios'>{radios}</td></tr>"
            )
        cards.append(
            f"<div class='card'><h3>{i}/{len(recs)} · {_esc(r.get('company'))} · "
            f"{_esc(r.get('source_type'))} · <a href='{_esc(url)}' target='_blank'>source ↗</a></h3>"
            f"<div class='text'>{_esc(text)}</div>"
            f"<table>{''.join(rows)}</table></div>"
        )

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Adjudication — held-out labels ({len(recs)} artifacts)</title>
<style>
body {{ font-family: -apple-system, sans-serif; background:#0f1115; color:#e6e8ec; max-width:1000px; margin:0 auto; padding:24px; }}
h1 {{ font-size:20px; }} h3 {{ font-size:14px; color:#6ea8fe; }}
.hint {{ color:#9aa3b2; font-size:13px; }}
.card {{ background:#181b22; border:1px solid #2a2f3a; border-radius:10px; padding:16px; margin:14px 0; }}
.text {{ max-height:180px; overflow-y:auto; background:#0f1115; border:1px solid #2a2f3a; border-radius:8px; padding:10px; font-size:12px; color:#9aa3b2; white-space:pre-wrap; }}
table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:12px; }}
td {{ padding:6px 8px; border-bottom:1px solid #2a2f3a; vertical-align:top; }}
.fld {{ color:#9aa3b2; width:120px; }} .pv {{ max-width:220px; }}
.radios label {{ display:inline-block; margin-right:10px; white-space:nowrap; cursor:pointer; }}
button {{ background:#6ea8fe; color:#0b1020; border:0; border-radius:8px; padding:12px 20px; font-size:14px; font-weight:600; cursor:pointer; margin:18px 0; }}
#status {{ color:#4ade80; font-size:13px; }} a {{ color:#6ea8fe; }}
.sticky {{ position:sticky; top:0; background:#0f1115; padding:8px 0; z-index:5; border-bottom:1px solid #2a2f3a; }}
</style></head><body>
<h1>Held-out label adjudication</h1>
<div class='sticky'><span class='hint'>For each field: is the <b>P</b>roduction prediction or the <b>I</b>ndependent label correct
(read only the source text + <code>evals/adjudication_guide.md</code>)? Unanswered fields are skipped.
When done: </span><button onclick='exportJson()'>Export adjudicated.json</button> <span id='status'></span></div>
{"".join(cards)}
<button onclick='exportJson()'>Export adjudicated.json</button>
<script>
function exportJson() {{
  const out = {{}};
  document.querySelectorAll('input[type=radio]:checked').forEach(el => {{
    const [aid, field] = el.name.split('::');
    (out[aid] = out[aid] || {{}})[field] = el.value;
  }});
  const n = Object.keys(out).length;
  const blob = new Blob([JSON.stringify(out, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'adjudicated.json'; a.click();
  document.getElementById('status').textContent = 'Exported ' + n + ' artifacts — move the file to evals/adjudicated.json';
}}
</script></body></html>"""


def score_adjudication(adjudicated_path: Path, labels_path: Path) -> dict[str, Any]:
    """FINAL per-field production accuracy from human adjudication.

    Production is correct when the human chose 'production' or 'both';
    'skip' rows are excluded from the denominator."""
    choices: dict[str, dict[str, str]] = json.loads(adjudicated_path.read_text())
    per_field: dict[str, dict[str, int]] = {f: {"correct": 0, "n": 0} for f in ADJUDICATED_FIELDS}
    for _aid, fields in choices.items():
        for f, choice in fields.items():
            if f not in per_field or choice == "skip":
                continue
            per_field[f]["n"] += 1
            if choice in ("production", "both"):
                per_field[f]["correct"] += 1
    results = {
        f: {
            "n": v["n"],
            "accuracy": round(v["correct"] / v["n"], 2) if v["n"] else None,
        }
        for f, v in per_field.items()
    }
    n_artifacts = len(choices)
    return {
        "n_artifacts_adjudicated": n_artifacts,
        "per_field_accuracy": results,
        "method": "human adjudication of held-out labels (production correct when the "
        "adjudicator chose 'production' or 'both'); skips excluded from denominators",
    }


def render_final_section(results: dict[str, Any]) -> str:
    lines = [
        "\n\n## FINAL — human-adjudicated held-out accuracy",
        "",
        f"Adjudicated by the user on {results.get('n_artifacts_adjudicated')} held-out "
        "artifacts per `evals/adjudication_guide.md`. These are the REPORTED accuracy "
        "numbers (production classifier vs human judgment); the provisional agreement "
        "table above is superseded for these fields.",
        "",
        "| Field | Accuracy | N |",
        "|---|---:|---:|",
    ]
    for f, v in results["per_field_accuracy"].items():
        acc = f"{v['accuracy']:.2f}" if v["accuracy"] is not None else "—"
        lines.append(f"| {f} | {acc} | {v['n']} |")
    lines.append("")
    lines.append(f"_Method: {results['method']}._")
    return "\n".join(lines)
