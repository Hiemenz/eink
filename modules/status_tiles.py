"""
Status Tiles Module
====================
A dense grid of small red / yellow / green tiles, one per program, showing at a
glance whether each is running successfully.

Display only: where the statuses come from is deliberately not decided yet.
All data access goes through `get_statuses(config)`, the single seam to replace
when real data is wired up. Until then it reads `status_tiles.items` from
config.yml, falling back to a built-in sample set.

Layout (800 x 480): a headline (worst status wins), then a grid of rounded\ncards — white with a green dot when OK, solid yellow/red when not — capped in\nsize so a handful of programs stay small. A footer shows green/yellow/red counts.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

import utils

W, H = 800, 480
HEADER_H = 52
FOOTER_H = 36
PAD = 8
GAP = 8
CARD_RADIUS = 10
MAX_TILE_W = 170
MAX_TILE_H = 72

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
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _summary(counts: dict) -> tuple[str, tuple[int, int, int]]:
    """Headline text and its colour: worst status wins."""
    if counts["fail"]:
        n = counts["fail"]
        return f"{n} {'SERVICE' if n == 1 else 'SERVICES'} DOWN", RED
    if counts["warn"]:
        n = counts["warn"]
        return f"{n} DEGRADED", YELLOW
    return "ALL SYSTEMS OPERATIONAL", GREEN


def _draw_card(draw, box, name, status, font, show_label):
    """Rounded card: white for ok (with a green dot), solid fill for warn/fail."""
    x0, y0, x1, y1 = box
    color = STATUS_COLORS[status]
    solid = status != "ok"
    text_color = WHITE if status == "fail" else BLACK
    radius = max(3, min(CARD_RADIUS, (y1 - y0) // 3))
    draw.rounded_rectangle(box, radius=radius, fill=color if solid else WHITE,
                           outline=BLACK, width=1)
    d = max(6, min(14, (y1 - y0) // 3))
    cy = (y0 + y1) // 2
    dot = (x0 + 8, cy - d // 2, x0 + 8 + d, cy - d // 2 + d)
    draw.ellipse(dot, fill=WHITE if status == "fail" else color, outline=BLACK)
    if show_label and (x1 - x0) >= 48:
        tx = dot[2] + 7
        label = _fit_label(draw, name, font, x1 - tx - 6)
        draw.text((tx, cy), label, font=font, fill=text_color, anchor="lm")


def generate(config: dict) -> str:
    cfg = config.get("status_tiles", {})
    output_path = cfg.get("output_path", "images/status_tiles.bmp")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Problems first, so they sit top-left where the eye lands.
    order = {"fail": 0, "warn": 1, "ok": 2}
    statuses = sorted(get_statuses(config), key=lambda t: order[t[1]])
    show_labels = cfg.get("show_labels", True)
    counts = {k: sum(1 for _, s in statuses if s == k) for k in STATUS_COLORS}

    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # Header: worst-status headline on the left, clock on the right.
    title_font = utils.get_font(26, bold=True, config=config)
    small_font = utils.get_font(16, config=config)
    headline, hcolor = _summary(counts)
    draw.ellipse((PAD + 4, 22, PAD + 24, 42), fill=hcolor, outline=BLACK, width=2)
    draw.text((PAD + 36, 32), headline, font=title_font, fill=BLACK, anchor="lm")
    now_str = datetime.now().strftime("%H:%M")
    draw.text((W - PAD - 4, 32), now_str, font=small_font, fill=BLACK, anchor="rm")
    draw.line([(PAD, HEADER_H), (W - PAD, HEADER_H)], fill=BLACK, width=2)

    # Grid of compact cards, capped in size so few programs stay small and tidy.
    area_x, area_y = PAD, HEADER_H + PAD + 4
    area_w, area_h = W - 2 * PAD, H - HEADER_H - FOOTER_H - 2 * PAD - 4
    cols, rows, tw, th = grid_layout(len(statuses), area_w, area_h)
    tw, th = min(tw, MAX_TILE_W), min(th, MAX_TILE_H)
    if cols:
        grid_w = cols * tw + (cols - 1) * GAP
        area_x += (area_w - grid_w) // 2   # centre when capped
        area_y += (area_h - (rows * th + (rows - 1) * GAP)) // 2

    label_font = utils.get_font(max(9, min(13, th // 3)), bold=True, config=config)
    for i, (name, status) in enumerate(statuses):
        r, c = divmod(i, cols)
        x = area_x + c * (tw + GAP)
        y = area_y + r * (th + GAP)
        _draw_card(draw, (x, y, x + tw - 1, y + th - 1), name, status, label_font, show_labels)

    # Footer legend with counts.
    fy = H - FOOTER_H + 8
    x = PAD + 4
    for key, label in (("ok", "OK"), ("warn", "WARN"), ("fail", "FAIL")):
        draw.ellipse((x, fy + 2, x + 14, fy + 16), fill=STATUS_COLORS[key], outline=BLACK)
        text = f"{counts[key]} {label}"
        draw.text((x + 22, fy + 9), text, font=small_font, fill=BLACK, anchor="lm")
        x += 22 + int(small_font.getlength(text)) + 28

    img.save(output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        print(generate(yaml.safe_load(f)))
