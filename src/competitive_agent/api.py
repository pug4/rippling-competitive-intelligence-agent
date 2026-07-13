"""Minimal local API for the React UI (§40.6).

The UI consumes the SAME validated JSON package that the Markdown/JSON
renderers produce — it implements no analytical logic of its own. This server
only lists runs and serves their stored packages, briefs, and traces.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .config import get_settings
from .schemas.common import utcnow

app = FastAPI(title="Competitive Marketing Intelligence Director", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev only
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# In-memory job tracker for UI-launched runs. A run is driven in a background
# thread (fixture is seconds; live is minutes) so the request returns at once
# and the UI polls /api/jobs + /api/runs.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_ALLOWED_MODES = {"snapshot", "longitudinal", "comparative"}
_ALLOWED_EXEC = {"fixture", "cached", "live"}


class NewRunRequest(BaseModel):
    company: str
    compare_to: str | None = None
    mode: str = "comparative"
    execution_mode: str = "fixture"
    lookback_days: int | None = None
    # How many trailing days count as "recent" (the current window); the rest
    # of the lookback becomes the prior/comparison window.
    current_days: int | None = None
    # Collect individual LinkedIn posts via the Exa Agent (costs Exa credits).
    include_linkedin: bool = True


def _validate_windows(lookback_days: int | None, current_days: int | None) -> None:
    if lookback_days is not None and lookback_days <= 0:
        raise HTTPException(status_code=400, detail="lookback_days must be positive")
    if current_days is not None and current_days <= 0:
        raise HTTPException(status_code=400, detail="current_days must be positive")
    if lookback_days is not None and current_days is not None and current_days >= lookback_days:
        raise HTTPException(
            status_code=400,
            detail="current_days must be smaller than lookback_days "
            "(the recent window is a slice of the total history)",
        )


def _run_job(job_id: str, req: NewRunRequest) -> None:
    from .runner import run_analysis

    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = "running"
    try:
        state = run_analysis(
            req.company,
            mode=req.mode,
            execution_mode=req.execution_mode,  # type: ignore[arg-type]
            compare_to=(req.compare_to or None),
            lookback_days=req.lookback_days,
            current_days=req.current_days,
            include_linkedin=req.include_linkedin,
        )
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="done", run_id=state.run_id, stop_reason=state.stop_reason)
    except Exception as exc:  # surfaced to the UI, never crashes the server
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=f"{type(exc).__name__}: {exc}")


@app.post("/api/runs")
def create_run(req: NewRunRequest) -> dict[str, Any]:
    company = (req.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="company is required")
    if req.mode not in _ALLOWED_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(_ALLOWED_MODES)}")
    if req.execution_mode not in _ALLOWED_EXEC:
        raise HTTPException(
            status_code=400, detail=f"execution_mode must be one of {sorted(_ALLOWED_EXEC)}"
        )
    _validate_windows(req.lookback_days, req.current_days)
    job_id = "job-" + uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "company": company,
            "compare_to": req.compare_to,
            "mode": req.mode,
            "execution_mode": req.execution_mode,
            "status": "pending",
            "started_at": utcnow().isoformat(),
        }
        snapshot = dict(_JOBS[job_id])
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return snapshot


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        return sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)[:20]


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


class ChatRequest(BaseModel):
    question: str
    history: list[dict[str, str]] = []
    execution_mode: str = "live"
    # Scope the grounded data to one product vertical (payroll, hris_core_hr, ...)
    vertical: str | None = None


@app.post("/api/runs/{run_id}/chat")
async def chat(run_id: str, req: ChatRequest) -> dict[str, Any]:
    """Grounded analysis chatbot for a run — answers follow-ups from its findings."""
    from .chat import chat_about_run

    if not (_runs_dir() / run_id / "data.json").exists():
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if not (req.question or "").strip():
        raise HTTPException(status_code=400, detail="question is required")
    return await chat_about_run(
        run_id,
        req.question.strip(),
        history=req.history,
        execution_mode=req.execution_mode,
        vertical=(req.vertical or None),
    )


class RewindowRequest(BaseModel):
    lookback_days: int
    current_days: int


@app.post("/api/runs/{run_id}/rewindow")
def rewindow(run_id: str, req: RewindowRequest) -> dict[str, Any]:
    """Deterministic re-count of the temporal views under CUSTOM windows.

    Pure counting over the run's persisted classifications/artifacts — no LLM
    calls, no persistence: the saved report keeps the run's original windows.
    Windows are anchored at the run's ORIGINAL current-window end (never
    now()), so artifacts can't fall out of windows just because time passed.
    """
    from datetime import timedelta

    from . import synthesis
    from .graph import GraphContext, load_state
    from .processing.temporal import reconcile_change_events
    from .report import _load
    from .schemas.common import new_id
    from .schemas.company import TimeWindow
    from .storage.repository import Repository

    if req.lookback_days <= 0 or req.current_days <= 0 or req.current_days >= req.lookback_days:
        raise HTTPException(status_code=422, detail="need 0 < current_days < lookback_days")
    repo = Repository.open(get_settings().db_path)
    try:
        state = load_state(repo, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
    cur = next((w for w in state.time_windows if w.purpose == "current"), None)
    anchor = cur.end_at if cur else utcnow()
    windows = [
        TimeWindow(
            window_id=new_id("TW"),
            label="current",
            purpose="current",
            start_at=anchor - timedelta(days=req.current_days),
            end_at=anchor,
        ),
        TimeWindow(
            window_id=new_id("TW"),
            label="comparison",
            purpose="comparison",
            start_at=anchor - timedelta(days=req.lookback_days),
            end_at=anchor - timedelta(days=req.current_days),
        ),
    ]
    # Minimal read-only context: _load touches only ctx.repository, and gives
    # exact parity with the package build (junk-ads filter, merged family,
    # change events already as dicts).
    ctx = GraphContext(repository=repo, trace=None, config=None, settings=None)
    data = _load(ctx, state)
    tb = synthesis.temporal_baseline(
        data["classification_models"], data["artifact_models"], windows
    )
    events, notes = reconcile_change_events(
        data["change_events"], data["classification_models"], data["artifact_models"], windows
    )
    return {
        "time_windows": [json.loads(w.model_dump_json()) for w in windows],
        "temporal_baseline": tb,
        "change_events": events,
        "reconciliation_notes": notes,
    }


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
