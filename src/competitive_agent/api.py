"""Minimal local API for the React UI (§40.6).

The UI consumes the SAME validated JSON package that the Markdown/JSON
renderers produce — it implements no analytical logic of its own. This server
only lists runs and serves their stored packages, briefs, and traces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from .config import get_settings

app = FastAPI(title="Competitive Marketing Intelligence Director", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev only
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _runs_dir() -> Path:
    return Path(get_settings().outputs_dir) / "runs"


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    runs = _runs_dir()
    if not runs.exists():
        return out
    for d in sorted(runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        data = d / "data.json"
        if not data.exists():
            continue
        try:
            pkg = json.loads(data.read_text())
        except Exception:
            continue
        run = pkg.get("run", {})
        scope = pkg.get("scope", {})
        out.append(
            {
                "run_id": run.get("run_id", d.name),
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
        )
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    data = _runs_dir() / run_id / "data.json"
    if not data.exists():
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return json.loads(data.read_text())


@app.get("/api/runs/{run_id}/brief", response_class=PlainTextResponse)
def get_brief(run_id: str) -> str:
    brief = _runs_dir() / run_id / "brief.md"
    if not brief.exists():
        raise HTTPException(status_code=404, detail=f"brief not found: {run_id}")
    return brief.read_text()


@app.get("/api/runs/{run_id}/trace")
def get_trace(run_id: str) -> list[dict[str, Any]]:
    trace = _runs_dir() / run_id / "trace.jsonl"
    if not trace.exists():
        raise HTTPException(status_code=404, detail=f"trace not found: {run_id}")
    return [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
