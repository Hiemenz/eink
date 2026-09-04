"""
System Health Module
=====================
Displays Raspberry Pi vitals (CPU, memory, disk, uptime) and the per-module
watchdog summary from data/health.json on the e-ink display.

Layout (800 x 480, B&W)
-----------------------
┌─ Header (36px): SYSTEM HEALTH ──────────────────────────── HH:MM ─┐
├─ LEFT PANEL (395px) ──────────┬─ RIGHT PANEL (404px) ──────────────┤
│  PI VITALS                    │  MODULE WATCHDOG                   │
│  CPU        12%  48.1C        │  18/20 modules healthy             │
│  Memory     34%  612 MB free  │  weather        3 failures         │
│  Disk       61%  12.4 GB free │    Connection timeout              │
│  Uptime     3d 4h              │  river_height   1 failure          │
│                                │  ...                                │
├───────────────────────────────┴────────────────────────────────────┤
│  Footer (50px): ALL SYSTEMS OK                          HH:MM:SS   │
└────────────────────────────────────────────────────────────────────┘

Reads /sys/class/thermal/thermal_zone0/temp for CPU temperature (Raspberry
Pi convention) and psutil for the rest. Both degrade gracefully — a missing
thermal file or a missing psutil install still renders a usable display.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

import utils

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

W, H = 800, 480

HEADER_H  = 36
FOOTER_H  = 50
DIVIDER_X = 395
PAD       = 9
CONTENT_Y = HEADER_H + 1 + 4
CONTENT_MAX_Y = H - FOOTER_H - 1

ROW_BODY  = 15
ROW_LABEL = 18
ROW_GAP   = 6

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY  = (180, 180, 180)

THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"


# ---------------------------------------------------------------------------
# Stat collection — each degrades independently, never raises
# ---------------------------------------------------------------------------

def _cpu_temp_c() -> float | None:
    try:
        with open(THERMAL_PATH) as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def _collect_vitals() -> dict[str, Any]:
    vitals: dict[str, Any] = {
        "cpu_percent": None,
        "mem_percent": None,
        "mem_available_mb": None,
        "disk_percent": None,
        "disk_free_gb": None,
        "uptime_s": None,
        "cpu_temp_c": _cpu_temp_c(),
    }
    try:
        import psutil
        vitals["cpu_percent"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        vitals["mem_percent"] = vm.percent
        vitals["mem_available_mb"] = round(vm.available / 1_048_576)
        disk = psutil.disk_usage("/")
        vitals["disk_percent"] = disk.percent
        vitals["disk_free_gb"] = round(disk.free / 1_073_741_824, 1)
        vitals["uptime_s"] = time.time() - psutil.boot_time()
    except Exception:
        pass
    return vitals


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _fmt_age(ts: float | None) -> str:
    """Format a unix timestamp as a relative age, e.g. '4m ago'."""
    if not ts:
        return "never"
    delta = time.time() - ts
    if delta < 0:
        return "just now"
    return f"{_fmt_duration(delta)} ago"


# ---------------------------------------------------------------------------
# Layout helpers (Cursor-based, same pattern as modules/brain_status.py)
# ---------------------------------------------------------------------------

class Cursor:
    def __init__(self, x: int, y: int, max_y: int, max_w: int):
        self.x = x
        self.y = y
        self.max_y = max_y
        self.w = max_w

    def advance(self, dy: int) -> bool:
        self.y += dy
        return self.y < self.max_y

    def fits(self, rows: int = 1, row_h: int = ROW_BODY) -> bool:
        return self.y + rows * row_h < self.max_y


def _draw_label(draw: ImageDraw.ImageDraw, cur: Cursor, text: str, fonts: dict) -> None:
    if not cur.fits(2, ROW_LABEL):
        return
    draw.text((cur.x, cur.y), text.upper(), font=fonts["label"], fill=BLACK)
    cur.advance(ROW_LABEL)
    draw.line([(cur.x, cur.y), (cur.x + cur.w, cur.y)], fill=GRAY, width=1)
    cur.advance(3)


def _draw_body(draw: ImageDraw.ImageDraw, cur: Cursor, text: str, fonts: dict,
               bold: bool = False, indent: int = 0) -> None:
    if not cur.fits(1, ROW_BODY):
        return
    max_chars = max(10, (cur.w - indent) // 6)
    text = str(text)
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    draw.text((cur.x + indent, cur.y), text, font=fonts["body_b" if bold else "body"], fill=BLACK)
    cur.advance(ROW_BODY)


def _draw_gap(cur: Cursor, px: int = ROW_GAP) -> None:
    cur.advance(px)


def _load_fonts(config: dict | None) -> dict:
    return {
        "header": utils.get_font(16, bold=True, config=config),
        "label":  utils.get_font(11, bold=True, config=config),
        "body":   utils.get_font(10, bold=False, config=config),
        "body_b": utils.get_font(10, bold=True, config=config),
        "footer": utils.get_font(12, bold=True, config=config),
    }


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def _render_vitals(draw: ImageDraw.ImageDraw, vitals: dict, fonts: dict, x0: int) -> None:
    cur = Cursor(x=x0 + PAD, y=CONTENT_Y, max_y=CONTENT_MAX_Y, max_w=DIVIDER_X - x0 - PAD * 2)
    _draw_label(draw, cur, "Pi Vitals", fonts)

    cpu = vitals["cpu_percent"]
    temp = vitals["cpu_temp_c"]
    cpu_line = f"CPU        {cpu:.0f}%" if cpu is not None else "CPU        N/A"
    cpu_line += f"   {temp:.1f}C" if temp is not None else "   N/A"
    _draw_body(draw, cur, cpu_line, fonts, bold=True)

    mem_pct = vitals["mem_percent"]
    mem_avail = vitals["mem_available_mb"]
    if mem_pct is not None:
        _draw_body(draw, cur, f"Memory     {mem_pct:.0f}%   {mem_avail} MB free", fonts)
    else:
        _draw_body(draw, cur, "Memory     N/A", fonts)

    disk_pct = vitals["disk_percent"]
    disk_free = vitals["disk_free_gb"]
    if disk_pct is not None:
        _draw_body(draw, cur, f"Disk       {disk_pct:.0f}%   {disk_free} GB free", fonts)
    else:
        _draw_body(draw, cur, "Disk       N/A", fonts)

    _draw_body(draw, cur, f"Uptime     {_fmt_duration(vitals['uptime_s'])}", fonts)


def _render_watchdog(draw: ImageDraw.ImageDraw, health: dict, fonts: dict, x0: int) -> None:
    max_w = W - x0 - PAD
    cur = Cursor(x=x0 + PAD, y=CONTENT_Y, max_y=CONTENT_MAX_Y, max_w=max_w)
    _draw_label(draw, cur, "Module Watchdog", fonts)

    if not health:
        _draw_body(draw, cur, "No health data recorded yet.", fonts)
        _draw_body(draw, cur, "Run main.py at least once.", fonts)
        return

    modules = sorted(health.items())
    failing = [(name, entry) for name, entry in modules if entry.get("consecutive_failures", 0) > 0]
    healthy_count = len(modules) - len(failing)
    _draw_body(draw, cur, f"{healthy_count}/{len(modules)} modules healthy", fonts, bold=True)
    _draw_gap(cur, 3)

    for name, entry in failing:
        count = entry.get("consecutive_failures", 0)
        label = "failure" if count == 1 else "failures"
        _draw_body(draw, cur, f"{name}   {count} {label}", fonts, bold=True)
        err = entry.get("last_error")
        if err:
            _draw_body(draw, cur, str(err), fonts, indent=6)

    if failing:
        _draw_gap(cur, ROW_GAP)

    _draw_label(draw, cur, "Last Success", fonts)
    for name, entry in modules:
        age = _fmt_age(entry.get("last_success_ts"))
        _draw_body(draw, cur, f"{name.ljust(20)} {age}", fonts)


def _render_footer(draw: ImageDraw.ImageDraw, health: dict) -> None:
    y_top = H - FOOTER_H
    draw.rectangle([(0, y_top), (W, H)], fill=WHITE)
    draw.line([(0, y_top), (W, y_top)], fill=BLACK, width=1)

    failing = sum(1 for e in health.values() if e.get("consecutive_failures", 0) > 0)
    status = "ALL SYSTEMS OK" if not failing else f"{failing} MODULE{'S' if failing != 1 else ''} FAILING"

    fonts = {"footer": utils.get_font(16, bold=True)}
    draw.text((PAD, y_top + 14), status, font=fonts["footer"], fill=BLACK)

    ts = datetime.now().strftime("%H:%M:%S")
    tw = fonts["footer"].getlength(ts)
    draw.text((W - PAD - tw, y_top + 14), ts, font=fonts["footer"], fill=BLACK)


# ---------------------------------------------------------------------------
# Main generate() — module entry point
# ---------------------------------------------------------------------------

def generate(config: dict) -> str:
    cfg = config.get("system_health", {})
    output_path = cfg.get("output_path", "images/system_health.bmp")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    fonts = _load_fonts(config)
    vitals = _collect_vitals()
    health = utils.load_health()

    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (W, HEADER_H)], fill=BLACK)
    draw.text((PAD, 9), "SYSTEM HEALTH", font=fonts["header"], fill=WHITE)
    now_str = datetime.now().strftime("%H:%M")
    right_w = fonts["header"].getlength(now_str)
    draw.text((W - PAD - right_w, 9), now_str, font=fonts["header"], fill=WHITE)
    draw.line([(0, HEADER_H), (W, HEADER_H)], fill=BLACK, width=1)

    draw.line([(DIVIDER_X, HEADER_H), (DIVIDER_X, H - FOOTER_H)], fill=BLACK, width=1)

    _render_vitals(draw, vitals, fonts, x0=0)
    _render_watchdog(draw, health, fonts, x0=DIVIDER_X + 1)
    _render_footer(draw, health)

    img.save(output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
