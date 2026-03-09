"""
Brain Status Module
===================
Displays the AI brain's current activity and forward plan on the e-ink display.

Layout (800 x 480, B&W)
-----------------------
┌─ Header (36px): AI BRAIN STATUS ──────────────── Cycle #N  HH:MM ─┐
├─ LEFT PANEL (395px) ──────────┬─ RIGHT PANEL (404px) ──────────────┤
│  CURRENTLY WORKING ON         │  PLAN & STRATEGY                   │
│  [active tasks + agent name]  │  Objectives: 1. … 2. …             │
│  RECENT EVENTS                │  Queue: N pending · N running      │
│  brain: startup               │  UPCOMING SKILLS                   │
│  ResearchAgent: searching…    │  crypto_monitor  in 45m            │
│  ACTIVE SKILLS                │  weather_agent   in 2h             │
├───────────────────────────────┴────────────────────────────────────┤
│  Footer (50px): THOUGHT: "…latest reasoning…"                      │
└────────────────────────────────────────────────────────────────────┘

The module reads brain.db in read-only mode — safe to run while brain daemon runs.
Falls back gracefully if brain.db doesn't exist yet.
"""

from __future__ import annotations

import os
import platform
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

W, H = 800, 480

HEADER_H   = 36     # top header bar
FOOTER_H   = 50     # bottom thought bar
DIVIDER_X  = 395    # vertical split between left / right panels
PAD        = 9      # inner padding for panels
CONTENT_Y  = HEADER_H + 1 + 4      # y where panel content begins
CONTENT_H  = H - HEADER_H - 1 - FOOTER_H - 1 - 8  # usable height in panels

# Row heights for different text sizes
ROW_BODY   = 15   # body text row height (10pt)
ROW_LABEL  = 18   # section label row height (11pt bold)
ROW_GAP    = 6    # blank spacer

# Colors (B&W)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY  = (180, 180, 180)   # light gray for alternating rows / subtle dividers


# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------

def _load_fonts(base_path: str | None = None) -> dict[str, ImageFont.FreeTypeFont]:
    """Load Liberation fonts, falling back to system defaults."""
    candidates_mono_regular = [
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        "/Library/Fonts/Courier New.ttf",
    ]
    candidates_mono_bold = [
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
        "/Library/Fonts/Courier New Bold.ttf",
    ]
    candidates_sans_bold = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        base_path or "",
        "/Library/Fonts/Arial Unicode.ttf",
    ]

    def _pick(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
        for path in candidates:
            if path and os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    return {
        "header":  _pick(candidates_sans_bold,   16),
        "label":   _pick(candidates_mono_bold,   11),
        "body":    _pick(candidates_mono_regular, 10),
        "body_b":  _pick(candidates_mono_bold,   10),
        "footer":  _pick(candidates_mono_regular, 10),
    }


# ---------------------------------------------------------------------------
# Brain DB reader (read-only, no side effects on the running brain)
# ---------------------------------------------------------------------------

class BrainReader:
    """Thin read-only wrapper around brain.db."""

    def __init__(self, db_path: str):
        import duckdb
        self._conn = duckdb.connect(db_path, read_only=True)

    def active_tasks(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT task_id, description, assigned_to FROM tasks "
            "WHERE status = 'in_progress' ORDER BY updated_at DESC LIMIT 3"
        ).fetchall()
        return [{"id": r[0], "description": r[1], "agent": r[2]} for r in rows]

    def pending_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0]

    def in_progress_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM tasks WHERE status='in_progress'").fetchone()[0]

    def completed_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0]

    def failed_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM tasks WHERE status='failed'").fetchone()[0]

    def recent_events(self, limit: int = 6) -> list[dict]:
        rows = self._conn.execute(
            "SELECT timestamp, agent, action FROM events ORDER BY timestamp DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [{"ts": str(r[0])[:16], "agent": r[1], "action": r[2]} for r in rows]

    def objectives(self, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, objective, source FROM objectives WHERE status='active' ORDER BY created_at ASC LIMIT ?",
            [limit],
        ).fetchall()
        return [{"id": r[0], "objective": r[1], "source": r[2]} for r in rows]

    def latest_thought(self) -> str:
        row = self._conn.execute(
            "SELECT reasoning FROM thoughts ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else ""

    def skill_last_run(self, skill_name: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT timestamp FROM events WHERE action LIKE ? ORDER BY timestamp DESC LIMIT 1",
            [f"skill:{skill_name}%"],
        ).fetchone()
        if row and row[0]:
            ts = row[0]
            if hasattr(ts, "year"):
                return ts
            try:
                return datetime.fromisoformat(str(ts))
            except Exception:
                return None
        return None

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _trunc(text: str, max_chars: int) -> str:
    text = str(text).strip().replace("\n", " ")
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _fmt_delta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds/60)}m"
    h, m = divmod(int(seconds / 60), 60)
    return f"{h}h {m}m" if m else f"{h}h"


class Cursor:
    """Tracks the current y-position within a panel."""
    def __init__(self, x: int, y: int, max_y: int, max_w: int):
        self.x = x
        self.y = y
        self.max_y = max_y
        self.w = max_w     # usable width

    def advance(self, dy: int) -> bool:
        self.y += dy
        return self.y < self.max_y

    def fits(self, rows: int = 1, row_h: int = ROW_BODY) -> bool:
        return self.y + rows * row_h < self.max_y


def _draw_label(draw: ImageDraw.ImageDraw, cur: Cursor,
                text: str, fonts: dict, divider: bool = True) -> None:
    """Draw a bold section label with optional underline."""
    if not cur.fits(2, ROW_LABEL):
        return
    draw.text((cur.x, cur.y), text.upper(), font=fonts["label"], fill=BLACK)
    cur.advance(ROW_LABEL)
    if divider:
        draw.line([(cur.x, cur.y), (cur.x + cur.w, cur.y)], fill=GRAY, width=1)
        cur.advance(3)


def _draw_body(draw: ImageDraw.ImageDraw, cur: Cursor,
               text: str, fonts: dict, bold: bool = False, indent: int = 0) -> None:
    """Draw a single body-text row, truncated to fit."""
    if not cur.fits(1, ROW_BODY):
        return
    max_chars = max(10, (cur.w - indent) // 6)
    draw.text((cur.x + indent, cur.y), _trunc(text, max_chars),
              font=fonts["body_b" if bold else "body"], fill=BLACK)
    cur.advance(ROW_BODY)


def _draw_gap(cur: Cursor, px: int = ROW_GAP) -> None:
    cur.advance(px)


# ---------------------------------------------------------------------------
# Render helpers — left and right panels
# ---------------------------------------------------------------------------

SKILL_INTERVALS = {
    "crypto_monitor": 3600,
    "weather_agent":  21600,
    "system_health":  1800,
}


def _render_left(draw: ImageDraw.ImageDraw, reader: BrainReader | None,
                 fonts: dict, x0: int) -> None:
    cur = Cursor(x=x0 + PAD, y=CONTENT_Y, max_y=H - FOOTER_H - 1, max_w=DIVIDER_X - x0 - PAD * 2)

    # ── Currently Working On ──────────────────────────────────────────
    _draw_label(draw, cur, "Currently Working On", fonts)

    tasks = reader.active_tasks() if reader else []
    if tasks:
        for task in tasks:
            agent = _trunc(task.get("agent", ""), 18)
            desc  = _trunc(task.get("description", ""), 38)
            _draw_body(draw, cur, agent, fonts, bold=True)
            _draw_body(draw, cur, desc, fonts, indent=6)
            _draw_gap(cur, 3)
    else:
        _draw_body(draw, cur, "Idle — no active tasks", fonts)

    _draw_gap(cur, ROW_GAP)

    # ── Recent Events ────────────────────────────────────────────────
    _draw_label(draw, cur, "Recent Events", fonts)

    events = reader.recent_events(6) if reader else []
    if events:
        for ev in events:
            label = f"{_trunc(ev['agent'], 14)}: {_trunc(ev['action'], 24)}"
            _draw_body(draw, cur, label, fonts)
    else:
        _draw_body(draw, cur, "No events recorded yet", fonts)

    _draw_gap(cur, ROW_GAP)

    # ── Active Skills ─────────────────────────────────────────────────
    _draw_label(draw, cur, "Active Skills", fonts)
    skill_names = list(SKILL_INTERVALS.keys())
    if skill_names:
        for s in skill_names:
            _draw_body(draw, cur, f"• {s}", fonts)
    else:
        _draw_body(draw, cur, "No skills loaded", fonts)


def _render_right(draw: ImageDraw.ImageDraw, reader: BrainReader | None,
                  fonts: dict, x0: int) -> None:
    max_w = W - x0 - PAD
    cur = Cursor(x=x0 + PAD, y=CONTENT_Y, max_y=H - FOOTER_H - 1, max_w=max_w)

    # ── Objectives ───────────────────────────────────────────────────
    _draw_label(draw, cur, "Plan & Strategy", fonts)

    objectives = reader.objectives(5) if reader else []
    if objectives:
        for obj in objectives:
            src_tag = f"[{obj['source'][:4]}]" if obj.get("source") else ""
            line = f"{obj['id']}. {_trunc(obj['objective'], 36)} {src_tag}"
            _draw_body(draw, cur, line, fonts)
    else:
        _draw_body(draw, cur, "No objectives set", fonts)
        _draw_body(draw, cur, "Add via Discord: !add <goal>", fonts)

    _draw_gap(cur, ROW_GAP)

    # ── Task Queue ────────────────────────────────────────────────────
    _draw_label(draw, cur, "Task Queue", fonts)

    if reader:
        pending   = reader.pending_count()
        running   = reader.in_progress_count()
        completed = reader.completed_count()
        failed    = reader.failed_count()
        _draw_body(draw, cur, f"{pending} pending  {running} running", fonts)
        _draw_body(draw, cur, f"{completed} done    {failed} failed", fonts)
    else:
        _draw_body(draw, cur, "N/A", fonts)

    _draw_gap(cur, ROW_GAP)

    # ── Upcoming Skills ───────────────────────────────────────────────
    _draw_label(draw, cur, "Upcoming Skills", fonts)

    now = datetime.utcnow()
    rows = []
    for skill_name, interval in SKILL_INTERVALS.items():
        last = reader.skill_last_run(skill_name) if reader else None
        if last:
            next_run = last + timedelta(seconds=interval)
            delta = (next_run - now).total_seconds()
            eta = f"in {_fmt_delta(delta)}" if delta > 0 else "due now"
        else:
            eta = f"every {_fmt_delta(interval)}"
        rows.append((skill_name, eta))

    if rows:
        # Align columns
        max_name = max(len(r[0]) for r in rows)
        for name, eta in rows:
            line = f"{name.ljust(max_name)}  {eta}"
            _draw_body(draw, cur, line, fonts)
    else:
        _draw_body(draw, cur, "No skills scheduled", fonts)


def _render_footer(draw: ImageDraw.ImageDraw, reader: BrainReader | None,
                   fonts: dict) -> None:
    thought = reader.latest_thought() if reader else ""
    y_top = H - FOOTER_H
    draw.rectangle([(0, y_top), (W, H)], fill=WHITE)
    draw.line([(0, y_top), (W, y_top)], fill=BLACK, width=1)

    prefix = "THOUGHT: "
    full_text = prefix + (thought.replace("\n", " ").strip() if thought else "No thoughts recorded yet.")

    # Wrap to two lines
    max_chars = (W - PAD * 2) // 6
    lines = textwrap.wrap(full_text, width=max_chars)[:3]

    y = y_top + 6
    for line in lines:
        if y + ROW_BODY > H - 2:
            break
        draw.text((PAD, y), line, font=fonts["footer"], fill=BLACK)
        y += ROW_BODY


# ---------------------------------------------------------------------------
# Main generate() — module entry point
# ---------------------------------------------------------------------------

def generate(config: dict) -> str:
    cfg = config.get("brain_status", {})
    output_path = cfg.get("output_path", "images/brain_status.bmp")
    db_path = cfg.get("db_path", "brain.db")
    font_path = config.get("font_path")

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    fonts = _load_fonts(font_path)

    # Try to open brain DB (read-only)
    reader: BrainReader | None = None
    db_missing = False
    if Path(db_path).exists():
        try:
            reader = BrainReader(db_path)
        except Exception as e:
            print(f"[brain_status] Could not open {db_path}: {e}")
    else:
        db_missing = True

    # ── Canvas ────────────────────────────────────────────────────────
    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # ── Header bar ───────────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, HEADER_H)], fill=BLACK)
    now_str = datetime.now().strftime("%H:%M")
    draw.text((PAD, 9), "AI BRAIN STATUS", font=fonts["header"], fill=WHITE)
    right_text = now_str if reader else "OFFLINE"
    right_w = fonts["header"].getlength(right_text)
    draw.text((W - PAD - right_w, 9), right_text, font=fonts["header"], fill=WHITE)

    # ── Header bottom border ──────────────────────────────────────────
    draw.line([(0, HEADER_H), (W, HEADER_H)], fill=BLACK, width=1)

    # ── Vertical divider ─────────────────────────────────────────────
    draw.line([(DIVIDER_X, HEADER_H), (DIVIDER_X, H - FOOTER_H)], fill=BLACK, width=1)

    # ── No-database placeholder ───────────────────────────────────────
    if db_missing:
        msg = "brain.db not found — start the Brain daemon first."
        cx = W // 2
        cy = (HEADER_H + H - FOOTER_H) // 2
        draw.text((cx, cy - 10), msg, font=fonts["body"], fill=GRAY, anchor="mm")
        _render_footer(draw, None, fonts)
    else:
        # ── Left panel ────────────────────────────────────────────────
        _render_left(draw, reader, fonts, x0=0)

        # ── Right panel ───────────────────────────────────────────────
        _render_right(draw, reader, fonts, x0=DIVIDER_X + 1)

        # ── Footer ────────────────────────────────────────────────────
        _render_footer(draw, reader, fonts)

    if reader:
        reader.close()

    img.save(output_path)
    return output_path
