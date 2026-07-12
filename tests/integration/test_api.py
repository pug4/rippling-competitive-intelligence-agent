"""API endpoints for the UI (§40.6): listing runs, serving packages, and the
UI-launched run-creation flow (POST /api/runs -> background job -> /api/jobs)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    from competitive_agent.api import app

    yield TestClient(app)
    config_mod.reset_config_cache()


def test_list_runs_empty(client):
    r = client.get("/api/runs")
    assert r.status_code == 200 and r.json() == []


def test_get_missing_run_404(client):
    assert client.get("/api/runs/RUN-nope").status_code == 404


def test_create_run_validates_input(client):
    assert client.post("/api/runs", json={"company": ""}).status_code == 400
    assert client.post("/api/runs", json={"company": "x", "mode": "bogus"}).status_code == 400
    assert (
        client.post("/api/runs", json={"company": "x", "execution_mode": "bogus"}).status_code
        == 400
    )


def test_create_run_starts_job_and_completes(client):
    r = client.post(
        "/api/runs",
        json={"company": "deel.com", "mode": "comparative", "execution_mode": "fixture"},
    )
    assert r.status_code == 200
    job = r.json()
    assert job["company"] == "deel.com" and job["job_id"].startswith("job-")
    assert job["status"] in ("pending", "running")

    # The fixture run finishes quickly; poll the jobs endpoint until done.
    import time

    for _ in range(60):
        jobs = client.get("/api/jobs").json()
        this = next((j for j in jobs if j["job_id"] == job["job_id"]), None)
        if this and this["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert this is not None and this["status"] == "done", this
    # The completed run is now listed and its package is served.
    runs = client.get("/api/runs").json()
    assert any(r2["run_id"] == this["run_id"] for r2 in runs)
    assert client.get(f"/api/runs/{this['run_id']}").status_code == 200
