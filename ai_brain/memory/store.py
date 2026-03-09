"""
DuckDB-backed persistent memory store.

Tables
------
events    — timestamped log of every agent action
tasks     — task registry with status tracking
knowledge — long-term facts learned by the brain
thoughts  — brain's internal reasoning journal
objectives — user-set goals the brain is pursuing
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from ai_brain.config import get_config

_memory_instance: MemoryStore | None = None


class MemoryStore:
    def __init__(self, db_path: str = "brain.db"):
        self._db_path = db_path
        self._conn = duckdb.connect(db_path)
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY,
                timestamp   TIMESTAMP DEFAULT current_timestamp,
                agent       VARCHAR,
                action      VARCHAR,
                result      TEXT
            )
        """)
        self._conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS events_id_seq START 1;
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id     VARCHAR PRIMARY KEY,
                description TEXT,
                priority    INTEGER DEFAULT 5,
                status      VARCHAR DEFAULT 'pending',
                assigned_to VARCHAR,
                result      TEXT,
                created_at  TIMESTAMP DEFAULT current_timestamp,
                updated_at  TIMESTAMP DEFAULT current_timestamp
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id         INTEGER PRIMARY KEY,
                topic      VARCHAR,
                summary    TEXT,
                source     VARCHAR,
                created_at TIMESTAMP DEFAULT current_timestamp
            )
        """)
        self._conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS knowledge_id_seq START 1;
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS thoughts (
                id         INTEGER PRIMARY KEY,
                timestamp  TIMESTAMP DEFAULT current_timestamp,
                reasoning  TEXT,
                context    TEXT
            )
        """)
        self._conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS thoughts_id_seq START 1;
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS objectives (
                id          INTEGER PRIMARY KEY,
                objective   TEXT,
                source      VARCHAR DEFAULT 'user',
                status      VARCHAR DEFAULT 'active',
                created_at  TIMESTAMP DEFAULT current_timestamp
            )
        """)
        self._conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS objectives_id_seq START 1;
        """)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def log_event(self, agent: str, action: str, result: str | Any = "") -> None:
        if not isinstance(result, str):
            result = json.dumps(result)
        self._conn.execute(
            "INSERT INTO events (id, agent, action, result) VALUES (nextval('events_id_seq'), ?, ?, ?)",
            [agent, action, result],
        )

    def recall_events(self, limit: int | None = None) -> list[dict]:
        n = limit or get_config().get("memory", {}).get("max_events_recalled", 20)
        rows = self._conn.execute(
            "SELECT timestamp, agent, action, result FROM events ORDER BY timestamp DESC LIMIT ?",
            [n],
        ).fetchall()
        return [{"timestamp": str(r[0]), "agent": r[1], "action": r[2], "result": r[3]} for r in rows]

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def save_task(self, task_id: str, description: str, priority: int = 5,
                  status: str = "pending", assigned_to: str = "") -> None:
        # Check if task already exists; upsert manually for DuckDB compatibility
        existing = self._conn.execute(
            "SELECT task_id FROM tasks WHERE task_id = ?", [task_id]
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE tasks SET status = ?, assigned_to = ? WHERE task_id = ?",
                [status, assigned_to, task_id],
            )
        else:
            self._conn.execute(
                "INSERT INTO tasks (task_id, description, priority, status, assigned_to) VALUES (?, ?, ?, ?, ?)",
                [task_id, description, priority, status, assigned_to],
            )

    def update_task(self, task_id: str, status: str, result: str | Any = "") -> None:
        if not isinstance(result, str):
            result = json.dumps(result)
        self._conn.execute(
            "UPDATE tasks SET status = ?, result = ?, updated_at = current_timestamp WHERE task_id = ?",
            [status, result, task_id],
        )

    def get_tasks(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT task_id, description, priority, status, assigned_to, result FROM tasks WHERE status = ? ORDER BY priority ASC",
                [status],
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT task_id, description, priority, status, assigned_to, result FROM tasks ORDER BY priority ASC"
            ).fetchall()
        keys = ["task_id", "description", "priority", "status", "assigned_to", "result"]
        return [dict(zip(keys, r)) for r in rows]

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    def save_knowledge(self, topic: str, summary: str, source: str = "") -> None:
        self._conn.execute(
            "INSERT INTO knowledge (id, topic, summary, source) VALUES (nextval('knowledge_id_seq'), ?, ?, ?)",
            [topic, summary, source],
        )

    def recall_knowledge(self, topic: str | None = None, limit: int | None = None) -> list[dict]:
        n = limit or get_config().get("memory", {}).get("max_knowledge_recalled", 10)
        if topic:
            rows = self._conn.execute(
                "SELECT topic, summary, source, created_at FROM knowledge WHERE topic ILIKE ? ORDER BY created_at DESC LIMIT ?",
                [f"%{topic}%", n],
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT topic, summary, source, created_at FROM knowledge ORDER BY created_at DESC LIMIT ?",
                [n],
            ).fetchall()
        return [{"topic": r[0], "summary": r[1], "source": r[2], "created_at": str(r[3])} for r in rows]

    # ------------------------------------------------------------------
    # Thoughts
    # ------------------------------------------------------------------

    def log_thought(self, reasoning: str, context: str | dict = "") -> None:
        if not isinstance(context, str):
            context = json.dumps(context)
        self._conn.execute(
            "INSERT INTO thoughts (id, reasoning, context) VALUES (nextval('thoughts_id_seq'), ?, ?)",
            [reasoning, context],
        )

    def recall_thoughts(self, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT timestamp, reasoning FROM thoughts ORDER BY timestamp DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [{"timestamp": str(r[0]), "reasoning": r[1]} for r in rows]

    # ------------------------------------------------------------------
    # Objectives (user-set goals from Discord or CLI)
    # ------------------------------------------------------------------

    def add_objective(self, objective: str, source: str = "user") -> int:
        row = self._conn.execute(
            "INSERT INTO objectives (id, objective, source) VALUES (nextval('objectives_id_seq'), ?, ?) RETURNING id",
            [objective, source],
        ).fetchone()
        return row[0]

    def get_objectives(self, status: str = "active") -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, objective, source, status, created_at FROM objectives WHERE status = ? ORDER BY created_at ASC",
            [status],
        ).fetchall()
        return [{"id": r[0], "objective": r[1], "source": r[2], "status": r[3], "created_at": str(r[4])} for r in rows]

    def complete_objective(self, objective_id: int) -> None:
        self._conn.execute("UPDATE objectives SET status = 'completed' WHERE id = ?", [objective_id])

    # ------------------------------------------------------------------
    # Summary for brain context window
    # ------------------------------------------------------------------

    def build_context_summary(self) -> str:
        events = self.recall_events(5)
        objectives = self.get_objectives("active")
        knowledge = self.recall_knowledge(limit=5)
        thoughts = self.recall_thoughts(3)

        parts = ["## Current Brain Context\n"]

        if objectives:
            parts.append("### Active Objectives")
            for o in objectives:
                parts.append(f"- [{o['id']}] {o['objective']} (from {o['source']})")

        if thoughts:
            parts.append("\n### Recent Thoughts")
            for t in thoughts:
                parts.append(f"- {t['reasoning'][:120]}")

        if events:
            parts.append("\n### Recent Events")
            for e in events:
                parts.append(f"- [{e['agent']}] {e['action']}: {str(e['result'])[:80]}")

        if knowledge:
            parts.append("\n### Recent Knowledge")
            for k in knowledge:
                parts.append(f"- {k['topic']}: {k['summary'][:100]}")

        return "\n".join(parts)

    def close(self) -> None:
        self._conn.close()


def get_memory(force_new: bool = False) -> MemoryStore:
    """Return singleton MemoryStore."""
    global _memory_instance
    if _memory_instance and not force_new:
        return _memory_instance
    db_path = get_config().get("memory", {}).get("database", "brain.db")
    _memory_instance = MemoryStore(db_path)
    return _memory_instance
