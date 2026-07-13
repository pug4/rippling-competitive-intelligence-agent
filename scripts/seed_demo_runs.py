#!/usr/bin/env python3
"""Seed the tracked demo run packages into outputs/runs/ for a hosted container.

The hosted public demo (DEMO_PUBLIC=1) cannot run new live analyses, so the
three bundled, REAL demo packages under ``ui/public/demo/`` ARE the corpus a
fresh container serves. For every run listed in ``ui/public/demo/runs.json``
this copies, verbatim::

    ui/public/demo/<run_id>.json       -> outputs/runs/<run_id>/data.json
    ui/public/demo/<run_id>.brief.md   -> outputs/runs/<run_id>/brief.md

``api.list_runs()`` scans ``outputs/runs/*/data.json`` and the read + chat
endpoints read ``data.json`` directly, so browse + chat work with NO database.

Idempotent: a run whose ``data.json`` already exists on disk is left untouched
(re-running on a warm volume never clobbers or duplicates). Run with::

    python scripts/seed_demo_runs.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def _demo_dir() -> Path:
    """The tracked demo package directory (ui/public/demo)."""
    try:
        from competitive_agent.config import REPO_ROOT

        return REPO_ROOT / "ui" / "public" / "demo"
    except Exception:
        # Fallback when the package is not importable (bare checkout): resolve
        # relative to this script — scripts/ sits at the repo root.
        return Path(__file__).resolve().parent.parent / "ui" / "public" / "demo"


def _runs_dir() -> Path:
    """The outputs/runs directory the API serves reports from."""
    try:
        from competitive_agent.config import get_settings

        return Path(get_settings().outputs_dir) / "runs"
    except Exception:
        return Path(__file__).resolve().parent.parent / "outputs" / "runs"


def main() -> int:
    demo = _demo_dir()
    index = demo / "runs.json"
    if not index.exists():
        print(f"! no demo index at {index}; nothing to seed", file=sys.stderr)
        return 0

    entries = json.loads(index.read_text())
    runs_dir = _runs_dir()
    seeded = skipped = 0

    for entry in entries:
        run_id = (entry or {}).get("run_id")
        if not run_id:
            continue
        src_json = demo / f"{run_id}.json"
        if not src_json.exists():
            print(f"! skip {run_id}: no {src_json.name} in {demo}", file=sys.stderr)
            continue

        dest_dir = runs_dir / run_id
        dest_data = dest_dir / "data.json"
        if dest_data.exists():
            skipped += 1
            print(f"= {run_id}: data.json already present — skipping")
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_json, dest_data)
        src_brief = demo / f"{run_id}.brief.md"
        if src_brief.exists():
            shutil.copyfile(src_brief, dest_dir / "brief.md")
        else:
            print(f"  ! {run_id}: no {src_brief.name} (brief will 404)", file=sys.stderr)
        seeded += 1
        print(f"+ {run_id}: seeded -> {dest_data}")

    print(f"\nSeeded {seeded} run(s), skipped {skipped} already present, into {runs_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
