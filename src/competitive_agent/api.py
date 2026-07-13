"""Minimal local API for the React UI (§40.6).

The UI consumes the SAME validated JSON package that the Markdown/JSON
renderers produce — it implements no analytical logic of its own. This server
only lists runs and serves their stored packages, briefs, and traces.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
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
# and the UI polls /api/jobs + /api/runs. The DB is the durable record: a run
# survives page refreshes AND server restarts, and an orphaned run (its thread
# died with a restart) is derived as "interrupted" and can be resumed from its
# last checkpoint via POST /api/runs/{run_id}/resume.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
# run_ids currently driven by a thread in THIS process (mutated under _JOBS_LOCK).
_ACTIVE_RUNS: set[str] = set()
_ALLOWED_MODES = {"snapshot", "longitudinal", "comparative"}
_ALLOWED_EXEC = {"fixture", "cached", "live"}
# A run not driven by this process is still "running" if its DB checkpoint is
# this fresh — covers CLI-driven runs sharing the same database.
_HEARTBEAT_SECONDS = 240
_TERMINAL_STATUSES = {"complete", "failed"}

# Humanized node names for progress display — the UI never shows raw node ids.
_PHASE_LABELS = {
    "initialize_run": "Setting up the run",
    "resolve_companies": "Identifying the companies",
    "load_or_create_time_windows": "Setting the time windows",
    "load_focal_state": "Loading the focal company",
    "assess_coverage": "Assessing what we know so far",
    "identify_unresolved_questions": "Listing open questions",
    "propose_actions": "Planning the next research step",
    "score_actions": "Scoring candidate research steps",
    "select_next_action": "Choosing the next research step",
    "execute_action": "Collecting sources",
    "normalize_and_deduplicate": "Cleaning and de-duplicating pages",
    "extract_and_classify": "Classifying pages",
    "validate_evidence": "Verifying quotes against sources",
    "update_coverage": "Updating coverage",
    "refresh_claims": "Re-judging strategic claims",
    "check_contradictions": "Checking for contradictions",
    "verify_temporal_changes": "Verifying changes over time",
    "build_matrices": "Building the comparison matrices",
    "run_focal_mirror_check": "Running the focal-company mirror",
    "generate_opportunities": "Drafting marketing plays",
    "critique_opportunities": "Stress-testing the plays",
    "decide_continue_or_stop": "Deciding whether to keep digging",
    "render_outputs": "Writing the report",
    "render_outputs_done": "Report written",
    "await_followup": "Waiting for follow-up",
    "awaiting_user": "Needs your input",
    "process_feedback_or_retry": "Processing feedback",
    "stopped": "Stopped",
}


def _phase_label(node: str | None) -> str:
    return _PHASE_LABELS.get(node or "", (node or "working").replace("_", " "))


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
    import asyncio

    from .runner import create_run, drive

    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = "running"
    run_id: str | None = None
    try:
        # create_run first so the run_id lands in the job (and the DB) right
        # away — the UI can attach its live view before the first page lands.
        state, ctx = create_run(
            req.company,
            mode=req.mode,
            execution_mode=req.execution_mode,  # type: ignore[arg-type]
            compare_to=(req.compare_to or None),
            lookback_days=req.lookback_days,
            current_days=req.current_days,
            include_linkedin=req.include_linkedin,
        )
        run_id = state.run_id
        with _JOBS_LOCK:
            _JOBS[job_id]["run_id"] = run_id
            _ACTIVE_RUNS.add(run_id)
        state = asyncio.run(drive(state, ctx))
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="done", run_id=state.run_id, stop_reason=state.stop_reason)
    except Exception as exc:  # surfaced to the UI, never crashes the server
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        if run_id:
            with _JOBS_LOCK:
                _ACTIVE_RUNS.discard(run_id)


def _supersede_paused_jobs(run_id: str) -> None:
    """Mark stale 'paused' jobs for this run as superseded (caller holds
    _JOBS_LOCK). A new job now owns the run's outcome; without this the UI
    would treat the run as paused forever."""
    for job in _JOBS.values():
        if job.get("run_id") == run_id and job.get("status") == "paused":
            job["status"] = "superseded"


def _job_end_status(state: Any) -> str:
    """Honest terminal job status: a run that stopped to ask the user a
    mid-run decision did NOT finish — reporting "done" would make the UI
    claim research finished (and drop its decision affordance) while the run
    is actually paused in awaiting_user."""
    if getattr(state, "pending_decision", None):
        return "paused"
    return "done"


def _resume_job(job_id: str, run_id: str) -> None:
    from .runner import resume_run

    try:
        state = resume_run(run_id)
        with _JOBS_LOCK:
            _JOBS[job_id].update(status=_job_end_status(state), stop_reason=state.stop_reason)
    except Exception as exc:  # surfaced to the UI, never crashes the server
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        with _JOBS_LOCK:
            _ACTIVE_RUNS.discard(run_id)


def _count_artifacts(run_id: str) -> int:
    """Real artifact count for a run, straight from the DB (never estimated)."""
    repo = _open_repo()
    return repo.conn.execute(
        "SELECT COUNT(*) FROM artifacts WHERE run_id = ?", (run_id,)
    ).fetchone()[0]


def _research_job(
    job_id: str, run_id: str, focus: str, sources: list[str], execution_mode: str | None
) -> None:
    """Drive an in-place research pass on the SAME run in a background thread."""
    from .conversation import research_in_place

    try:
        # Snapshot the artifact count before/after driving so the completion
        # note can state a REAL found/didn't-find delta (UI contract:
        # artifacts_before / artifacts_added), on both end states.
        artifacts_before = _count_artifacts(run_id)
        state = research_in_place(
            run_id, focus=focus, sources=sources, execution_mode=execution_mode
        )
        artifacts_added = _count_artifacts(run_id) - artifacts_before
        with _JOBS_LOCK:
            _JOBS[job_id].update(
                status=_job_end_status(state),
                stop_reason=state.stop_reason,
                artifacts_before=artifacts_before,
                artifacts_added=artifacts_added,
            )
    except Exception as exc:  # surfaced to the UI, never crashes the server
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        with _JOBS_LOCK:
            _ACTIVE_RUNS.discard(run_id)


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


def _runs_dir() -> Path:
    return Path(get_settings().outputs_dir) / "runs"


def _open_repo():
    from .storage.repository import Repository

    return Repository.open(get_settings().db_path)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _inflight_rows(conn) -> list[dict[str, Any]]:
    """Non-terminal runs from the DB, with the state fields the UI needs."""
    rows = conn.execute(
        """
        SELECT run_id, company, mode, status, current_node, execution_mode,
               created_at, updated_at,
               json_extract(state_json, '$.compare_to')            AS compare_to,
               json_extract(state_json, '$.iteration')             AS iteration,
               json_extract(state_json, '$.focal_run_id')          AS focal_run_id,
               json_extract(state_json, '$.pending_user_question') AS pending_user_question
        FROM runs
        WHERE status NOT IN ('complete', 'failed')
        ORDER BY updated_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _derive_live_status(row: dict[str, Any], active_or_child: set[str]) -> str:
    """running | needs_input | interrupted — from thread ownership + heartbeat.

    A run is "running" if a thread in this process drives it (or drives its
    parent — the focal mirror is executed inside the parent's thread), or if
    its DB checkpoint is fresh (a CLI process sharing the database). Otherwise
    its thread is gone and it is resumable from the last checkpoint.
    """
    if (row.get("current_node") or "") == "awaiting_user":
        return "needs_input"
    if row["run_id"] in active_or_child:
        return "running"
    updated = _parse_ts(row.get("updated_at"))
    if updated is not None and (utcnow() - updated).total_seconds() < _HEARTBEAT_SECONDS:
        return "running"
    return "interrupted"


def _active_and_children(rows: list[dict[str, Any]]) -> set[str]:
    with _JOBS_LOCK:
        active = set(_ACTIVE_RUNS)
    by_id = {r["run_id"]: r for r in rows}
    for run_id in list(active):
        focal = (by_id.get(run_id) or {}).get("focal_run_id")
        if focal:
            active.add(focal)
    return active


def _progress_counts(conn, run_id: str) -> dict[str, int]:
    n_artifacts = conn.execute(
        "SELECT COUNT(*) FROM artifacts WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    n_classified = conn.execute(
        "SELECT COUNT(DISTINCT artifact_id) FROM classifications "
        "WHERE run_id = ? AND family = 'message'",
        (run_id,),
    ).fetchone()[0]
    n_quotes = conn.execute(
        "SELECT COUNT(*) FROM classifications WHERE run_id = ? AND family = 'evidence'",
        (run_id,),
    ).fetchone()[0]
    return {"artifacts": n_artifacts, "classified": n_classified, "evidence_quotes": n_quotes}


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        jobs = [dict(j) for j in _JOBS.values()]
    # Enrich in-flight jobs with checkpointed progress so the sidebar can say
    # WHAT the run is doing, not just that it exists.
    running = [j for j in jobs if j.get("status") == "running" and j.get("run_id")]
    if running:
        try:
            repo = _open_repo()
            for j in running:
                row = repo.get_run(j["run_id"])
                if row is None:
                    continue
                node = row["current_node"]
                phase = _phase_label(node)
                iteration = None
                counts_run = j["run_id"]
                if node == "run_focal_mirror_check":
                    # The parent is blocked while the mirror drives — show the
                    # mirror's progress, which is what is actually happening.
                    focal_id = repo.conn.execute(
                        "SELECT json_extract(state_json, '$.focal_run_id') FROM runs "
                        "WHERE run_id = ?",
                        (j["run_id"],),
                    ).fetchone()[0]
                    if focal_id:
                        mirror = repo.get_run(focal_id)
                        if mirror is not None:
                            phase = f"Mirror: {_phase_label(mirror['current_node'])}"
                            counts_run = focal_id
                iter_row = repo.conn.execute(
                    "SELECT json_extract(state_json, '$.iteration') FROM runs WHERE run_id = ?",
                    (j["run_id"],),
                ).fetchone()
                iteration = iter_row[0] if iter_row else None
                j.update(
                    phase=phase, iteration=iteration, **_progress_counts(repo.conn, counts_run)
                )
        except Exception:
            pass  # enrichment is best-effort; the raw job rows still render
    by_id = {j["job_id"]: j for j in jobs}
    for j in running:
        by_id[j["job_id"]] = j
    return sorted(by_id.values(), key=lambda j: j["started_at"], reverse=True)[:20]


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # 1) In-flight runs from the DATABASE — they survive page refreshes and
    #    server restarts even though no report exists yet. Mirror children of
    #    an in-flight parent are folded into the parent's live view.
    try:
        repo = _open_repo()
        rows = _inflight_rows(repo.conn)
        active = _active_and_children(rows)
        inflight_focal_ids = {r["focal_run_id"] for r in rows if r.get("focal_run_id")}
        for r in rows:
            if r["run_id"] in inflight_focal_ids:
                continue  # internal mirror run — shown inside the parent
            if (_runs_dir() / r["run_id"] / "data.json").exists():
                continue  # report already on disk; the block below lists it
            out.append(
                {
                    "run_id": r["run_id"],
                    "company_input": r["company"],
                    "compare_to": r.get("compare_to"),
                    "mode": r["mode"],
                    "execution_mode": r["execution_mode"],
                    "in_progress": True,
                    "live_status": _derive_live_status(r, active),
                    "phase": _phase_label(r.get("current_node")),
                    "iteration": r.get("iteration"),
                    "updated_at": r.get("updated_at"),
                }
            )
    except Exception:
        pass  # a missing/locked DB must never hide the completed reports
    # 2) Completed reports from disk (unchanged source of truth for results).
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


def _humanize_trace_tail(run_id: str, limit: int = 8) -> list[dict[str, str]]:
    """Last few trace events as plain-language activity lines (newest first)."""
    trace = _runs_dir() / run_id / "trace.jsonl"
    if not trace.exists():
        return []
    try:
        lines = trace.read_text().splitlines()[-250:]
    except OSError:
        return []
    out: list[dict[str, str]] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        try:
            ev = json.loads(line)
        except Exception:
            continue
        etype = ev.get("event_type")
        p = ev.get("payload") or {}
        text: str | None = None
        if etype == "action_selected":
            text = f"Chose next step: {p.get('action_type', '?')} via {p.get('source', '?')}"
        elif etype == "tool_completed":
            n = p.get("artifact_count")
            status = p.get("status", "done")
            text = f"{p.get('tool_name', 'tool')}: {status}" + (
                f" — {n} item(s) collected" if n is not None else ""
            )
        elif etype == "tool_failed":
            text = f"{p.get('tool_name', 'tool')} failed — recorded, moving on"
        elif etype == "classification_completed":
            text = f"Classified {p.get('url') or p.get('artifact_id', 'a page')}"
        elif etype == "evidence_extracted":
            text = f"Extracted {p.get('evidence_items', '?')} verbatim quotes"
        elif etype == "coverage_assessed":
            text = "Assessed coverage across dimensions"
        elif etype == "source_skipped":
            text = f"Skipped a source: {p.get('source', p.get('reason', ''))}".strip()
        elif etype == "temporal_change_verified":
            text = "Verified a change-over-time candidate"
        elif etype == "company_pipeline_created":
            text = "Started the focal-company mirror analysis"
        elif etype == "stop_selected":
            text = f"Stopping: {p.get('reason', 'done')}"
        if text:
            out.append({"ts": ev.get("ts", ""), "text": text})
    return out


def _maybe_json(value: Any) -> Any:
    """json_extract returns objects as JSON text — parse them for the UI."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _live_snapshot(repo, run_id: str, *, include_mirror: bool = True) -> dict[str, Any]:
    row = repo.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    conn = repo.conn
    st = conn.execute(
        """
        SELECT json_extract(state_json, '$.iteration')             AS iteration,
               json_extract(state_json, '$.compare_to')            AS compare_to,
               json_extract(state_json, '$.focal_run_id')          AS focal_run_id,
               json_extract(state_json, '$.pending_user_question') AS pending_user_question,
               json_extract(state_json, '$.pending_decision')      AS pending_decision,
               json_extract(state_json, '$.spent_usd')             AS spent_usd,
               json_extract(state_json, '$.model_cost_usd')        AS model_cost_usd,
               json_extract(state_json, '$.budget_usd')            AS budget_usd,
               json_extract(state_json, '$.stop_reason')           AS stop_reason
        FROM runs WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    st = dict(st) if st is not None else {}
    if row["status"] in _TERMINAL_STATUSES:
        live_status = row["status"] if row["status"] == "failed" else "complete"
    else:
        rows = _inflight_rows(conn)
        live_status = _derive_live_status(
            {
                "run_id": run_id,
                "current_node": row["current_node"],
                "updated_at": row["updated_at"],
            },
            _active_and_children(rows),
        )
    source_mix = [
        {"source": r[0] or "unknown", "n": r[1]}
        for r in conn.execute(
            "SELECT source_type, COUNT(*) FROM artifacts WHERE run_id = ? "
            "GROUP BY source_type ORDER BY 2 DESC",
            (run_id,),
        ).fetchall()
    ]
    top_themes = [
        {"theme": r[0], "n": r[1]}
        for r in conn.execute(
            "SELECT json_extract(payload_json, '$.primary_theme') AS t, COUNT(*) "
            "FROM classifications WHERE run_id = ? AND family = 'message' "
            "GROUP BY t ORDER BY 2 DESC LIMIT 8",
            (run_id,),
        ).fetchall()
        if r[0]
    ]
    latest = [
        {
            "url": r[0],
            "title": r[1],
            "source_type": r[2] or "unknown",
            "created_at": r[3],
        }
        for r in conn.execute(
            "SELECT url, json_extract(payload_json, '$.title'), source_type, created_at "
            "FROM artifacts WHERE run_id = ? ORDER BY created_at DESC LIMIT 8",
            (run_id,),
        ).fetchall()
    ]
    snap: dict[str, Any] = {
        "run_id": run_id,
        "company": row["company"],
        "compare_to": st.get("compare_to"),
        "mode": row["mode"],
        "execution_mode": row["execution_mode"],
        "status": live_status,
        "current_node": row["current_node"],
        "phase": _phase_label(row["current_node"]),
        "iteration": st.get("iteration"),
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
        "pending_question": st.get("pending_user_question"),
        "pending_decision": _maybe_json(st.get("pending_decision")),
        "spend": {
            "tool_usd": st.get("spent_usd"),
            "model_usd": st.get("model_cost_usd"),
            "budget_usd": st.get("budget_usd"),
        },
        "counts": _progress_counts(conn, run_id),
        "source_mix": source_mix,
        "top_themes": top_themes,
        "latest_artifacts": latest,
        "recent_activity": _humanize_trace_tail(run_id),
        "report_ready": (_runs_dir() / run_id / "data.json").exists(),
        "mirror": None,
    }
    focal_id = st.get("focal_run_id")
    if include_mirror and focal_id and repo.get_run(focal_id) is not None:
        mirror = _live_snapshot(repo, focal_id, include_mirror=False)
        # Only surface the mirror while it is itself in flight — once done it
        # is an internal detail of the parent's report.
        if mirror["status"] in ("running", "interrupted", "needs_input"):
            snap["mirror"] = mirror
    return snap


@app.get("/api/runs/{run_id}/live")
def get_live(run_id: str) -> dict[str, Any]:
    """Cheap progress snapshot of an in-flight run — pure DB/trace reads.

    The UI polls this every few seconds while a run works, so users watch the
    corpus, source mix, and early themes GROW instead of staring at a spinner.
    No LLM calls, no writes; numbers are checkpoint-fresh, not final — the
    report's deterministic render remains the source of truth.
    """
    return _live_snapshot(_open_repo(), run_id)


@app.post("/api/runs/{run_id}/resume")
def resume_run_endpoint(run_id: str) -> dict[str, Any]:
    """Resume an interrupted run from its last checkpoint in a background job."""
    repo = _open_repo()
    row = repo.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if (_runs_dir() / run_id / "data.json").exists():
        raise HTTPException(status_code=409, detail="run already has a report")
    with _JOBS_LOCK:
        if run_id in _ACTIVE_RUNS:
            raise HTTPException(status_code=409, detail="run is already being driven")
        job_id = "job-" + uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id,
            "company": row["company"],
            "compare_to": None,
            "mode": row["mode"],
            "execution_mode": row["execution_mode"],
            "status": "running",
            "run_id": run_id,
            "resumed": True,
            "started_at": utcnow().isoformat(),
        }
        _ACTIVE_RUNS.add(run_id)
        snapshot = dict(_JOBS[job_id])
    threading.Thread(target=_resume_job, args=(job_id, run_id), daemon=True).start()
    return snapshot


class ResearchBody(BaseModel):
    # What to find out (becomes the run's user_focus for the pass).
    focus: str
    # Canonical user-facing source names (conversation.SOURCE_NAME_MAP):
    # web, wayback, ads, reviews, similarweb, linkedin, news, keywords.
    sources: list[str]
    execution_mode: str | None = None


@app.post("/api/runs/{run_id}/research")
def research_endpoint(run_id: str, req: ResearchBody) -> dict[str, Any]:
    """Deeper research on demand INSIDE an existing run (no child run).

    Re-drives the same run_id scoped to the requested sources in a background
    job; new artifacts append to the run and its report is rewritten in place.
    The existing jobs poller + /live endpoint stream progress to the UI.
    """
    from .conversation import expand_sources

    if not (req.focus or "").strip():
        raise HTTPException(status_code=400, detail="focus is required")
    if not req.sources:
        raise HTTPException(status_code=400, detail="sources is required (canonical names)")
    if req.execution_mode is not None and req.execution_mode not in _ALLOWED_EXEC:
        raise HTTPException(
            status_code=400, detail=f"execution_mode must be one of {sorted(_ALLOWED_EXEC)}"
        )
    try:
        expand_sources(req.sources)  # validate names up front: 400, not a failed job
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    repo = _open_repo()
    row = repo.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    focus = req.focus.strip()
    with _JOBS_LOCK:
        if run_id in _ACTIVE_RUNS:
            raise HTTPException(status_code=409, detail="run is already being driven")
        _supersede_paused_jobs(run_id)
        job_id = "job-" + uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id,
            "kind": "research",
            "company": row["company"],
            "compare_to": None,
            "mode": row["mode"],
            "execution_mode": req.execution_mode or row["execution_mode"],
            "status": "running",
            "run_id": run_id,
            "focus": focus,
            "sources": list(req.sources),
            "started_at": utcnow().isoformat(),
        }
        _ACTIVE_RUNS.add(run_id)
        snapshot = dict(_JOBS[job_id])
    threading.Thread(
        target=_research_job,
        args=(job_id, run_id, focus, list(req.sources), req.execution_mode),
        daemon=True,
    ).start()
    return snapshot


class AnswerBody(BaseModel):
    choice: str  # option id from state.pending_decision["options"]


@app.post("/api/runs/{run_id}/answer")
def answer_decision(run_id: str, req: AnswerBody) -> dict[str, Any]:
    """Apply the user's choice to a paused run's pending decision, then resume.

    Appends to decision_log, extends source_allowlist / clears the failure
    block when the chosen option names a source, clears pending_decision,
    saves, and resumes the run in a background job (same shape as /resume).
    """
    from .graph import load_state

    repo = _open_repo()
    row = repo.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    with _JOBS_LOCK:
        if run_id in _ACTIVE_RUNS:
            raise HTTPException(status_code=409, detail="run is already being driven")
    state = load_state(repo, run_id)
    pending = getattr(state, "pending_decision", None)
    if not pending:
        raise HTTPException(status_code=409, detail="run has no pending decision")
    choice = (req.choice or "").strip()
    options = {str(o.get("id")): o for o in (pending.get("options") or [])}
    if options and choice not in options:
        raise HTTPException(status_code=400, detail=f"choice must be one of {sorted(options)}")
    state.decision_log = [
        *state.decision_log,
        {"question": pending.get("question"), "choice": choice, "via": "user"},
    ]
    source = (options.get(choice) or {}).get("source")
    if source:
        # The user opted into this source: allow it and clear its failure block.
        if state.source_allowlist is not None and source not in state.source_allowlist:
            state.source_allowlist = [*state.source_allowlist, source]
        state.failed_actions = {
            key: rec for key, rec in state.failed_actions.items() if key.split(":", 1)[0] != source
        }
    state.pending_decision = None
    with _JOBS_LOCK:
        if run_id in _ACTIVE_RUNS:  # re-check: another thread may have started
            raise HTTPException(status_code=409, detail="run is already being driven")
        # Save INSIDE the lock: saving before the re-check left a window where
        # the decision was recorded but a concurrent driver's checkpoints
        # clobbered it (and this request 409'd after mutating state).
        repo.update_run_state(run_id, current_node=state.current_node, state=state)
        _supersede_paused_jobs(run_id)
        job_id = "job-" + uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id,
            "kind": "answer",
            "company": row["company"],
            "compare_to": None,
            "mode": row["mode"],
            "execution_mode": row["execution_mode"],
            "status": "running",
            "run_id": run_id,
            "choice": choice,
            "resumed": True,
            "started_at": utcnow().isoformat(),
        }
        _ACTIVE_RUNS.add(run_id)
        snapshot = dict(_JOBS[job_id])
    threading.Thread(target=_resume_job, args=(job_id, run_id), daemon=True).start()
    return snapshot


@app.get("/api/runs/{run_id}/briefing")
def get_briefing(run_id: str) -> dict[str, Any]:
    """Deterministic run briefing for the chat panel — pure compose, no model.

    Every figure is read verbatim from the stored data.json; absences are
    stated honestly ("none collected"), never filled in.
    """
    from .chat import build_briefing

    data = _runs_dir() / run_id / "data.json"
    if not data.exists():
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    pkg = json.loads(data.read_text())
    return {"briefing": build_briefing(pkg), "generated_at": utcnow().isoformat()}


@app.post("/api/runs/{run_id}/dismiss")
def dismiss_run(run_id: str) -> dict[str, Any]:
    """Mark an interrupted/stuck run as failed so it leaves the queue.

    Refused while a thread is driving the run. Reversible in the DB (status
    field only); collected artifacts and the trace are never deleted.
    """
    repo = _open_repo()
    row = repo.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    with _JOBS_LOCK:
        if run_id in _ACTIVE_RUNS:
            raise HTTPException(status_code=409, detail="run is actively being driven")
    if row["status"] in _TERMINAL_STATUSES:
        return {"run_id": run_id, "status": row["status"]}
    repo.update_run_state(run_id, status="failed")
    return {"run_id": run_id, "status": "failed"}


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


class PaidSearchRequest(BaseModel):
    execution_mode: str = "live"
    force: bool = False


@app.get("/api/runs/{run_id}/paid-search")
def get_paid_search(run_id: str) -> dict[str, Any]:
    """Cached paid-search targeting draft, if one was generated for this run."""
    from .paid_search import _cache_path

    cache = _cache_path(run_id)
    if not cache.exists():
        return {"generated": False}
    return {"generated": True, **json.loads(cache.read_text())}


@app.post("/api/runs/{run_id}/paid-search")
async def draft_paid_search(run_id: str, req: PaidSearchRequest) -> dict[str, Any]:
    """Draft grounded paid-search keyword clusters for a completed run.

    One bounded model call over the run's OBSERVED evidence; volumes/CPCs are
    never estimated (not publicly knowable) and every cluster ships with
    validate-before-spend forced on. Cached — repeats are free unless force.
    """
    from .paid_search import generate_paid_search_targets

    if req.execution_mode not in _ALLOWED_EXEC:
        raise HTTPException(
            status_code=400, detail=f"execution_mode must be one of {sorted(_ALLOWED_EXEC)}"
        )
    try:
        result = await generate_paid_search_targets(
            run_id, execution_mode=req.execution_mode, force=req.force
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
    return {"generated": True, **result}


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
