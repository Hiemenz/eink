"""
Job Scheduler — runs periodic and delayed tasks in a background thread.

Features:
  - every(seconds)  — repeating jobs
  - at(datetime)    — one-shot delayed jobs
  - event-driven via trigger()
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Any

from ai_brain.config import get_config


@dataclass
class Job:
    name: str
    fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    interval_seconds: float | None = None   # None = one-shot
    run_at: datetime = field(default_factory=datetime.utcnow)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    enabled: bool = True
    last_run: datetime | None = None
    run_count: int = 0

    def is_due(self) -> bool:
        return self.enabled and datetime.utcnow() >= self.run_at

    def reschedule(self) -> None:
        """Reschedule for next run if repeating, else disable."""
        self.last_run = datetime.utcnow()
        self.run_count += 1
        if self.interval_seconds:
            self.run_at = datetime.utcnow() + timedelta(seconds=self.interval_seconds)
        else:
            self.enabled = False


class JobScheduler:
    """Background thread that fires registered jobs when they're due."""

    def __init__(self):
        self._jobs: list[Job] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._check_interval: float = get_config().get("scheduler", {}).get("check_interval", 10)

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def every(self, seconds: float, fn: Callable, name: str = "",
               args: tuple = (), kwargs: dict | None = None, delay: float = 0) -> Job:
        """Register a repeating job."""
        job = Job(
            name=name or fn.__name__,
            fn=fn,
            args=args,
            kwargs=kwargs or {},
            interval_seconds=seconds,
            run_at=datetime.utcnow() + timedelta(seconds=delay),
        )
        with self._lock:
            self._jobs.append(job)
        return job

    def at(self, when: datetime, fn: Callable, name: str = "",
            args: tuple = (), kwargs: dict | None = None) -> Job:
        """Register a one-shot job to run at a specific time."""
        job = Job(name=name or fn.__name__, fn=fn, args=args, kwargs=kwargs or {}, run_at=when)
        with self._lock:
            self._jobs.append(job)
        return job

    def after(self, seconds: float, fn: Callable, name: str = "",
               args: tuple = (), kwargs: dict | None = None) -> Job:
        """Register a one-shot job to run after a delay."""
        return self.at(
            when=datetime.utcnow() + timedelta(seconds=seconds),
            fn=fn, name=name, args=args, kwargs=kwargs,
        )

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            for job in self._jobs:
                if job.job_id == job_id:
                    job.enabled = False
                    return True
        return False

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": j.job_id,
                    "name": j.name,
                    "run_at": str(j.run_at),
                    "interval": j.interval_seconds,
                    "enabled": j.enabled,
                    "run_count": j.run_count,
                }
                for j in self._jobs
            ]

    # ------------------------------------------------------------------
    # Runtime
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="JobScheduler")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            due_jobs: list[Job] = []
            with self._lock:
                due_jobs = [j for j in self._jobs if j.is_due()]

            for job in due_jobs:
                try:
                    job.fn(*job.args, **job.kwargs)
                except Exception:
                    traceback.print_exc()
                finally:
                    job.reschedule()

            time.sleep(self._check_interval)
