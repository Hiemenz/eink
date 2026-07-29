"""
Unit tests for modules/brain_status.py — text truncation, duration formatting,
Cursor layout tracking, and BrainReader's read-only DuckDB queries.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import brain_status


class TestTrunc:
    def test_short_text_unchanged(self):
        assert brain_status._trunc("hello", 10) == "hello"

    def test_long_text_truncated_with_ellipsis(self):
        result = brain_status._trunc("a very long string here", 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_newlines_replaced_with_spaces(self):
        assert brain_status._trunc("line1\nline2", 20) == "line1 line2"

    def test_non_string_input_coerced(self):
        assert brain_status._trunc(12345, 10) == "12345"

    def test_strips_whitespace(self):
        assert brain_status._trunc("  padded  ", 20) == "padded"


class TestFmtDelta:
    def test_seconds(self):
        assert brain_status._fmt_delta(45) == "45s"

    def test_minutes(self):
        assert brain_status._fmt_delta(125) == "2m"

    def test_hours_with_minutes(self):
        assert brain_status._fmt_delta(3900) == "1h 5m"

    def test_exact_hours_no_minutes(self):
        assert brain_status._fmt_delta(7200) == "2h"

    def test_zero(self):
        assert brain_status._fmt_delta(0) == "0s"


class TestCursor:
    def test_initial_position(self):
        cur = brain_status.Cursor(x=10, y=20, max_y=100, max_w=200)
        assert cur.x == 10
        assert cur.y == 20

    def test_advance_moves_y_and_returns_bool(self):
        cur = brain_status.Cursor(x=0, y=0, max_y=50, max_w=100)
        within = cur.advance(20)
        assert cur.y == 20
        assert within is True

    def test_advance_past_max_y_returns_false(self):
        cur = brain_status.Cursor(x=0, y=0, max_y=50, max_w=100)
        within = cur.advance(60)
        assert within is False

    def test_fits_true_when_room_available(self):
        cur = brain_status.Cursor(x=0, y=0, max_y=100, max_w=100)
        assert cur.fits(rows=2, row_h=15) is True

    def test_fits_false_when_no_room(self):
        cur = brain_status.Cursor(x=0, y=90, max_y=100, max_w=100)
        assert cur.fits(rows=2, row_h=15) is False


class TestBrainReader:
    """Exercise BrainReader against a real (temporary) DuckDB database file."""

    @pytest.fixture
    def db_path(self, tmp_path):
        import duckdb
        path = str(tmp_path / "brain_test.db")
        conn = duckdb.connect(path)
        conn.execute(
            "CREATE TABLE tasks (task_id INTEGER, description VARCHAR, "
            "assigned_to VARCHAR, status VARCHAR, updated_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO tasks VALUES "
            "(1, 'Fix bug', 'AgentA', 'in_progress', '2026-07-21 10:00:00'), "
            "(2, 'Write docs', 'AgentB', 'pending', '2026-07-21 09:00:00'), "
            "(3, 'Deploy', 'AgentC', 'completed', '2026-07-20 08:00:00'), "
            "(4, 'Broken task', 'AgentD', 'failed', '2026-07-19 08:00:00')"
        )
        conn.execute(
            "CREATE TABLE events (timestamp TIMESTAMP, agent VARCHAR, action VARCHAR)"
        )
        conn.execute(
            "INSERT INTO events VALUES "
            "('2026-07-21 10:05:00', 'AgentA', 'skill:crypto_monitor started'), "
            "('2026-07-21 09:55:00', 'brain', 'startup')"
        )
        conn.execute(
            "CREATE TABLE objectives (id INTEGER, objective VARCHAR, source VARCHAR, "
            "status VARCHAR, created_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO objectives VALUES "
            "(1, 'Ship feature X', 'discord', 'active', '2026-07-01 00:00:00')"
        )
        conn.execute("CREATE TABLE thoughts (timestamp TIMESTAMP, reasoning VARCHAR)")
        conn.execute(
            "INSERT INTO thoughts VALUES ('2026-07-21 10:06:00', 'Thinking about the bug fix')"
        )
        conn.close()
        return path

    def test_active_tasks(self, db_path):
        reader = brain_status.BrainReader(db_path)
        tasks = reader.active_tasks()
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "AgentA"
        reader.close()

    def test_counts(self, db_path):
        reader = brain_status.BrainReader(db_path)
        assert reader.pending_count() == 1
        assert reader.in_progress_count() == 1
        assert reader.completed_count() == 1
        assert reader.failed_count() == 1
        reader.close()

    def test_recent_events(self, db_path):
        reader = brain_status.BrainReader(db_path)
        events = reader.recent_events(limit=6)
        assert len(events) == 2
        assert events[0]["agent"] == "AgentA"
        reader.close()

    def test_objectives(self, db_path):
        reader = brain_status.BrainReader(db_path)
        objs = reader.objectives()
        assert len(objs) == 1
        assert objs[0]["objective"] == "Ship feature X"
        reader.close()

    def test_latest_thought(self, db_path):
        reader = brain_status.BrainReader(db_path)
        assert "bug fix" in reader.latest_thought()
        reader.close()

    def test_latest_thought_empty_table_returns_empty_string(self, tmp_path):
        import duckdb
        path = str(tmp_path / "empty.db")
        conn = duckdb.connect(path)
        conn.execute("CREATE TABLE thoughts (timestamp TIMESTAMP, reasoning VARCHAR)")
        conn.close()
        reader = brain_status.BrainReader(path)
        assert reader.latest_thought() == ""
        reader.close()

    def test_spend_summary_missing_table_returns_zeros(self, db_path):
        # token_usage table doesn't exist in this fixture DB.
        reader = brain_status.BrainReader(db_path)
        spend = reader.spend_summary()
        assert spend == {"today": 0.0, "month": 0.0, "total": 0.0, "top_models": []}
        reader.close()

    def test_skill_last_run_no_match_returns_none(self, db_path):
        reader = brain_status.BrainReader(db_path)
        assert reader.skill_last_run("weather_agent") is None
        reader.close()

    def test_skill_last_run_match_returns_datetime(self, db_path):
        reader = brain_status.BrainReader(db_path)
        result = reader.skill_last_run("crypto_monitor")
        assert result is not None
        assert hasattr(result, "year")
        reader.close()
