"""
Status Tiles Module
====================
A dense grid of small red / yellow / green tiles, one per program, showing at a
glance whether each is running successfully.

Display only: where the statuses come from is deliberately not decided yet.
All data access goes through `get_statuses(config)`, the single seam to replace
when real data is wired up. Until then it reads `status_tiles.items` from
config.yml, falling back to a built-in sample set.

Layout (800 x 480): header bar, then a tile grid whose column count is chosen
so tiles stay as large as possible while all of them fit. A footer shows
green/yellow/red counts.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

import utils

W, H = 800, 480
HEADER_H = 36
FOOTER_H = 30
PAD = 8
GAP = 4

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

# Pure palette colours so nothing is remapped by the 7-colour quantizer.
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
RED = (255, 0, 0)

STATUS_COLORS = {"ok": GREEN, "warn": YELLOW, "fail": RED}
_ALIASES = {
    "green": "ok", "ok": "ok", "up": "ok", "healthy": "ok",
    "yellow": "warn", "warn": "warn", "warning": "warn", "degraded": "warn",
    "red": "fail", "fail": "fail", "error": "fail", "down": "fail",
}

_SAMPLE = [
    ("weather", "ok"), ("flight_radar", "ok"), ("sports", "ok"),
    ("air_quality", "ok"), ("parking", "warn"), ("crypto", "ok"),
    ("franklin_cam", "fail"), ("aurora", "ok"), ("pollen", "ok"),
    ("river_height", "warn"), ("news", "ok"), ("discord_bot", "ok"),
    ("brain", "ok"), ("ollama", "fail"), ("traffic", "ok"),
    ("iss", "ok"), ("stocks", "warn"), ("xkcd", "ok"),
]


def normalize_status(value) -> str:
    """Map any recognised label to 'ok' / 'warn' / 'fail'; unknown counts as fail."""
    return _ALIASES.get(str(value).strip().lower(), "fail")


def get_statuses(config: dict) -> list[tuple[str, str]]:
    """Return [(name, 'ok'|'warn'|'fail')]. Replace the body to wire real data."""
    items = config.get("status_tiles", {}).get("items")
    if not items:
        return list(_SAMPLE)
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append((str(item.get("name", "?")), normalize_status(item.get("status"))))
        else:
            out.append((str(item), "ok"))
    return out


def grid_layout(n: int, area_w: int, area_h: int, gap: int = GAP) -> tuple[int, int, int, int]:
    """Pick (cols, rows, tile_w, tile_h) that fits n tiles with the largest tile area."""
    if n <= 0:
        return 0, 0, 0, 0
    best = None
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        tw = (area_w - gap * (cols - 1)) // cols
        th = (area_h - gap * (rows - 1)) // rows
        if tw < 1 or th < 1:
            continue
        if best is None or tw * th > best[2] * best[3]:
            best = (cols, rows, tw, th)
    return best or (1, n, 1, 1)


def _fit_label(draw, text, font, max_w):
    while text and draw.textlength(text, font=font) > max_w:
        text = text[:-1]
    return text


def generate(config: dict) -> str:
    cfg = config.get("status_tiles", {})
    output_path = cfg.get("output_path", "images/status_tiles.bmp")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    statuses = get_statuses(config)
    show_labels = cfg.get("show_labels", True)

    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    header_font = utils.get_font(18, bold=True, config=config)
    draw.rectangle([(0, 0), (W, HEADER_H)], fill=BLACK)
    draw.text((PAD, 8), "STATUS", font=header_font, fill=WHITE)
    now_str = datetime.now().strftime("%H:%M")
    draw.text((W - PAD - header_font.getlength(now_str), 8), now_str, font=header_font, fill=WHITE)

    area_x, area_y = PAD, HEADER_H + PAD
    area_w, area_h = W - 2 * PAD, H - HEADER_H - FOOTER_H - 2 * PAD
    cols, rows, tw, th = grid_layout(len(statuses), area_w, area_h)

    label_font = utils.get_font(max(8, min(14, th // 4)), config=config)
    for i, (name, status) in enumerate(statuses):
        r, c = divmod(i, cols)
        x = area_x + c * (tw + GAP)
        y = area_y + r * (th + GAP)
        draw.rectangle([(x, y), (x + tw - 1, y + th - 1)], fill=STATUS_COLORS[status], outline=BLACK)
        if show_labels and tw >= 40 and th >= 20:
            label = _fit_label(draw, name, label_font, tw - 6)
            draw.text((x + 3, y + 2), label, font=label_font, fill=BLACK)

    counts = {k: sum(1 for _, s in statuses if s == k) for k in STATUS_COLORS}
    fy = H - FOOTER_H + 6
    foot_font = utils.get_font(14, bold=True, config=config)
    x = PAD
    for key, label in (("ok", "OK"), ("warn", "WARN"), ("fail", "FAIL")):
        draw.rectangle([(x, fy + 2), (x + 12, fy + 14)], fill=STATUS_COLORS[key], outline=BLACK)
        text = f"{counts[key]} {label}"
        draw.text((x + 18, fy), text, font=foot_font, fill=BLACK)
        x += 18 + int(foot_font.getlength(text)) + 22

    img.save(output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        print(generate(yaml.safe_load(f)))
