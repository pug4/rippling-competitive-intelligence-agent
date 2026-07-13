#!/usr/bin/env python3
"""Bundle REAL completed runs into ui/public/demo/ for the static Vercel build.

The dashboard (ui/) normally fetches its data from the local FastAPI backend at
/api/... . Vercel has no backend, so the UI falls back to these bundled static
files when the backend is unreachable (see DEMO_MODE in ui/src/App.jsx). This
script emits, for a hand-picked set of REAL runs from outputs/runs/:

  ui/public/demo/<run_id>.json       the run package (data.json, re-dumped
                                     COMPACT to cut size) -> served for
                                     GET /api/runs/<id>
  ui/public/demo/<run_id>.brief.md   the run's brief.md -> served for
                                     GET /api/runs/<id>/brief
  ui/public/demo/runs.json           the array GET /api/runs returns, built in
                                     the SAME shape as api.list_runs() produces
                                     for completed disk runs.

Nothing is fabricated: every file is a verbatim (compacted) copy of a real
completed analysis. Run with:  uv run python scripts/build_demo_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "outputs" / "runs"
OUT_DIR = REPO / "ui" / "public" / "demo"

# The runs bundled into the static demo. Chosen to show breadth:
#   - RUN-0bce45e711b2  deel.com vs rippling.com  (ideal: model decisions, claims, 12 ad creatives, 59 LinkedIn)
#   - RUN-f4f7b2e276f6  vanta.com                 (industry adaptivity / compliance)
#   - RUN-aa58e6d812da  remofirst.com             (niche; share-normalization)
DEMO_RUNS = [
    "RUN-0bce45e711b2",
    "RUN-f4f7b2e276f6",
    "RUN-aa58e6d812da",
]


def run_list_entry(pkg: dict) -> dict:
    """Mirror api.list_runs()'s completed-disk-run entry for one package."""
    run = pkg.get("run", {})
    scope = pkg.get("scope", {})
    return {
        "run_id": run.get("run_id"),
        "company_input": scope.get("company_input"),
        "compare_to": scope.get("compare_to"),
        "mode": run.get("mode"),
        "execution_mode": run.get("execution_mode"),
        "generated_at": run.get("generated_at"),
        "stop_reason": run.get("stop_reason"),
        "opportunities": len(pkg.get("opportunities", [])),
        "proof_gaps": len(pkg.get("proof_gaps", [])),
        "change_events": len(pkg.get("change_events", [])),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    runs_index: list[dict] = []
    total = 0

    for run_id in DEMO_RUNS:
        run_dir = RUNS_DIR / run_id
        data_path = run_dir / "data.json"
        brief_path = run_dir / "brief.md"
        if not data_path.exists():
            print(f"  ! skip {run_id}: no data.json", file=sys.stderr)
            continue

        pkg = json.loads(data_path.read_text())
        entry = run_list_entry(pkg)
        # File names are keyed on the SAME run_id the runs.json list carries so
        # the UI's /api/runs/<id> -> /demo/<id>.json rewrite resolves cleanly.
        rid = entry["run_id"] or run_id

        compact = json.dumps(pkg, separators=(",", ":"), ensure_ascii=False)
        (OUT_DIR / f"{rid}.json").write_text(compact)
        size = len(compact.encode("utf-8"))
        total += size

        if brief_path.exists():
            (OUT_DIR / f"{rid}.brief.md").write_text(brief_path.read_text())
        else:
            print(f"  ! {run_id}: no brief.md", file=sys.stderr)

        runs_index.append(entry)
        print(
            f"  + {rid}  {entry['company_input']}"
            f"{' vs ' + entry['compare_to'] if entry['compare_to'] else ''}"
            f"  ({size / 1e6:.2f} MB, {entry['opportunities']} actions /"
            f" {entry['proof_gaps']} gaps / {entry['change_events']} changes)"
        )

    (OUT_DIR / "runs.json").write_text(json.dumps(runs_index, ensure_ascii=False))
    print(f"\nWrote {len(runs_index)} run(s) to {OUT_DIR} ({total / 1e6:.2f} MB total).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
