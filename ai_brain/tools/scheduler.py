"""Tool-level scheduler helpers that agents can call to enqueue future work."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

# Simple in-process job store shared with the Scheduler component
_pending_jobs: list[dict] = []


def schedule_job(name: str, fn: Callable, delay_seconds: int = 0,
                 interval_seconds: int | None = None, args: tuple = (), kwargs: dict | None = None) -> str:
    """
    Register a job to run after `delay_seconds` and optionally repeat every `interval_seconds`.
    Returns a job id string.
    """
    import uuid
    job_id = str(uuid.uuid4())[:8]
    run_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
    _pending_jobs.append({
        "id": job_id,
        "name": name,
        "fn": fn,
        "run_at": run_at,
        "interval_seconds": interval_seconds,
        "args": args,
        "kwargs": kwargs or {},
        "status": "pending",
    })
    return job_id


def list_jobs() -> list[dict]:
    return [
        {"id": j["id"], "name": j["name"], "run_at": str(j["run_at"]), "status": j["status"]}
        for j in _pending_jobs
    ]


def get_pending_jobs() -> list[dict]:
    """Return jobs that are due to run. Called by the Scheduler."""
    now = datetime.utcnow()
    return [j for j in _pending_jobs if j["status"] == "pending" and j["run_at"] <= now]


def mark_job_done(job_id: str) -> None:
    for j in _pending_jobs:
        if j["id"] == job_id:
            if j["interval_seconds"]:
                j["run_at"] = datetime.utcnow() + timedelta(seconds=j["interval_seconds"])
            else:
                j["status"] = "done"
            break
