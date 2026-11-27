"""
Holiday Balloon Parade module.

Still pictures of the parade in parade/holiday_parade.html for the seven-color panel.

Modes (holiday_parade.mode):
  snapshot        three balloons on a Franklin street; the line moves up one place every refresh,
                  and Santa's finale closes each lap
  balloon_of_day  one large balloon, a different one each day (Santa on December 24 and 25)

Snow or rain is drawn when the weather data reports it. All art is original and exported to
parade/sprites/ by tools/parade_sprites.py.
"""

from __future__ import annotations

import json
import os
import random
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from utils import get_font, get_logger

logger = get_logger("holiday_parade")

SPRITES = Path(__file__).resolve().parent.parent / "parade" / "sprites"
W, H = 800, 480
STREET = 424  # y of the street line

BLACK, WHITE, GREEN, BLUE = (0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255)
RED, YELLOW, ORANGE = (255, 0, 0), (255, 255, 0), (255, 128, 0)
COATS = (RED, BLUE, GREEN, ORANGE)
SCENES = ("main_street", "theatre", "square", "factory")


def load_balloons() -> list[dict]:
    return json.loads((SPRITES / "balloons.json").read_text())


def christmas_countdown(today: date) -> str:
    christmas = date(today.year, 12, 25)
    if today > christmas:
        christmas = date(today.year + 1, 12, 25)
    days = (christmas - today).days
    if days == 0:
        return "MERRY CHRISTMAS!"
    return "1 DAY UNTIL CHRISTMAS" if days == 1 else f"{days} DAYS UNTIL CHRISTMAS"


# ── Weather ──────────────────────────────────────────────────────────────────

def classify_weather(conditions: dict | None) -> str:
    """'snow', 'rain' or 'none' from an Open-Meteo conditions dict (WMO weather codes)."""
    if not conditions:
        return "none"
    code = conditions.get("weather_code", 0)
    if conditions.get("precip_type") in ("Snow", "Wintry Mix") or 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 51 <= code <= 67 or 80 <= code <= 82 or 95 <= code <= 99:
        return "rain"
    return "none"


def _conditions(config: dict) -> dict | None:
    """Current conditions for forecast_location; None if they can't be had."""
    location = config.get("forecast_location") or {}
    if not location.get("latitude") or not location.get("longitude"):
        return None
    try:
        from modules.weather import fetch_current_conditions
        return fetch_current_conditions(location["latitude"], location["longitude"],
                                        {"User-Agent": "eink-display/1.0"})
    except Exception:
        logger.warning("Weather unavailable; drawing the parade without it.", exc_info=True)
        return None


# ── Drawing helpers ──────────────────────────────────────────────────────────

def _canvas_draw(image: Image.Image) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"  # no anti-aliasing: greys would quantize to colored speckles
    return draw


def _text(draw, xy, string, size, fill, config, anchor="mm", max_width=None):
    font = get_font(size, config=config, bold=True)
    while max_width and size > 8 and draw.textlength(string, font=font) > max_width:
        size -= 1
        font = get_font(size, config=config, bold=True)
    draw.text(xy, string, font=font, fill=fill, anchor=anchor)


def _sign(draw, cx, y, width, label, color, config, size=13, ink=WHITE):
    draw.rectangle([cx - width // 2, y, cx + width // 2, y + size + 7], fill=color, outline=BLACK)
    _text(draw, (cx, y + (size + 7) // 2), label, size, ink, config, max_width=width - 8)


def _bulbs(draw, x0, x1, y, night):
    draw.line([x0, y, x1, y], fill=WHITE if night else BLACK)
    for i, x in enumerate(range(x0 + 8, x1 - 4, 16)):
        color = (RED, YELLOW, GREEN, ORANGE)[i % 4]
        draw.ellipse([x - 3, y, x + 3, y + 6], fill=color, outline=None if night else BLACK)


def _building(draw, x, width, height, night, trim_color):
    top = STREET - height
    draw.rectangle([x, top, x + width, STREET], fill=WHITE, outline=BLACK, width=2)
    draw.rectangle([x - 3, top - 5, x + width + 3, top + 3], fill=WHITE, outline=BLACK)
    for wy in range(top + 14, STREET - 78, 30):
        for wx in range(x + 12, x + width - 22, 28):
            draw.rectangle([wx, wy, wx + 13, wy + 19], fill=YELLOW if night else WHITE, outline=BLACK, width=2)
    for i, ax in enumerate(range(x + 2, x + width - 2, 14)):
        draw.rectangle([ax, STREET - 48, min(ax + 13, x + width - 2), STREET - 37],
                       fill=trim_color if i % 2 == 0 else WHITE, outline=BLACK)
    draw.rectangle([x + 10, STREET - 33, x + width - 10, STREET - 3], fill=YELLOW if night else WHITE,
                   outline=BLACK, width=2)
    draw.rectangle([x + width // 2 - 9, STREET - 33, x + width // 2 + 9, STREET], fill=trim_color, outline=BLACK)


def _shop(draw, x, width, height, night, trim, name, config):
    _building(draw, x, width, height, night, trim)
    if name:
        _sign(draw, x + width // 2, STREET - 70, width - 8, name, trim, config,
              ink=BLACK if trim in (YELLOW, GREEN) else WHITE)


def _grays(draw, x, night, config):
    _building(draw, x, 150, 150, night, GREEN)
    draw.rectangle([x + 30, STREET - 168, x + 120, STREET - 155], fill=RED, outline=BLACK)
    _sign(draw, x + 75, STREET - 70, 142, "GRAY'S ON MAIN", BLACK, config, size=12)
    draw.rectangle([x + 140, STREET - 146, x + 172, STREET - 74], fill=BLACK, outline=RED, width=2)
    for i, letter in enumerate("GRAY'S"):
        _text(draw, (x + 156, STREET - 137 + i * 11), letter, 11, YELLOW, config)


def _theatre(draw, x, night, greeting, config):
    draw.rectangle([x, STREET - 156, x + 270, STREET], fill=WHITE, outline=BLACK, width=2)
    draw.rectangle([x + 2, STREET - 196, x + 30, STREET - 70], fill=RED, outline=BLACK, width=2)
    for i, letter in enumerate("FRANKLIN"):
        _text(draw, (x + 16, STREET - 186 + i * 15), letter, 14, WHITE, config)
    _text(draw, (x + 150, STREET - 126), "THEATRE", 22, RED, config)
    draw.rectangle([x + 38, STREET - 104, x + 262, STREET - 62], fill=RED, outline=BLACK, width=2)
    draw.rectangle([x + 46, STREET - 96, x + 254, STREET - 70], fill=WHITE, outline=BLACK)
    for bx in range(x + 46, x + 258, 13):
        for by in (STREET - 100, STREET - 66):
            draw.ellipse([bx - 2, by - 2, bx + 2, by + 2], fill=YELLOW)
    _text(draw, (x + 150, STREET - 83), greeting.upper(), 15, BLACK, config, max_width=200)
    for wx in (x + 60, x + 175):
        draw.rectangle([wx, STREET - 52, wx + 56, STREET], fill=YELLOW if night else WHITE, outline=BLACK, width=2)
    draw.rectangle([x + 132, STREET - 52, x + 158, STREET], fill=RED, outline=BLACK)


def _courthouse(draw, x, night, config):
    draw.rectangle([x, STREET - 118, x + 230, STREET], fill=WHITE, outline=BLACK, width=2)
    draw.polygon([(x + 24, STREET - 118), (x + 115, STREET - 166), (x + 206, STREET - 118)], fill=WHITE,
                 outline=BLACK, width=2)
    _text(draw, (x + 115, STREET - 130), "PUBLIC SQUARE", 11, BLACK, config)
    for i in range(5):
        draw.rectangle([x + 38 + i * 36, STREET - 110, x + 49 + i * 36, STREET - 14], fill=RED, outline=BLACK)
    for wx in (x + 8, x + 208):
        for wy in (STREET - 96, STREET - 54):
            draw.rectangle([wx, wy, wx + 14, wy + 24], fill=YELLOW if night else WHITE, outline=BLACK, width=2)
    draw.rectangle([x + 20, STREET - 14, x + 210, STREET - 7], fill=WHITE, outline=BLACK)


def _chip(draw, x, config):
    """The soldier monument on the Public Square, known locally as Chip."""
    for half, low, high in ((26, 0, 10), (19, 10, 22), (14, 22, 52)):
        draw.rectangle([x - half, STREET - high, x + half, STREET - low], fill=WHITE, outline=BLACK, width=2)
    draw.polygon([(x - 9, STREET - 52), (x - 5, STREET - 138), (x + 5, STREET - 138), (x + 9, STREET - 52)],
                 fill=WHITE, outline=BLACK, width=2)
    draw.polygon([(x - 6, STREET - 138), (x - 4, STREET - 163), (x + 4, STREET - 163), (x + 6, STREET - 138)],
                 fill=BLACK)
    draw.ellipse([x - 4, STREET - 173, x + 4, STREET - 164], fill=BLACK)
    draw.line([x + 8, STREET - 138, x + 8, STREET - 170], fill=BLACK, width=2)
    _text(draw, (x, STREET - 36), "CHIP", 10, BLACK, config)


def _tree(draw, x):
    for top, half, bottom in ((-170, 28, -112), (-138, 42, -74), (-104, 58, -36), (-68, 72, -8)):
        draw.polygon([(x, STREET + top), (x + half, STREET + bottom), (x - half, STREET + bottom)],
                     fill=GREEN, outline=BLACK, width=2)
    for i, (dx, dy) in enumerate([(-8, -130), (14, -112), (-22, -92), (6, -84), (30, -62), (-40, -52),
                                  (-6, -44), (46, -24), (-52, -20), (16, -22)]):
        draw.ellipse([x + dx - 5, STREET + dy - 5, x + dx + 5, STREET + dy + 5],
                     fill=(RED, YELLOW, WHITE, BLUE)[i % 4], outline=BLACK)
    draw.polygon([(x, STREET - 186), (x + 5, STREET - 174), (x + 17, STREET - 173), (x + 8, STREET - 165),
                  (x + 11, STREET - 153), (x, STREET - 160), (x - 11, STREET - 153), (x - 8, STREET - 165),
                  (x - 17, STREET - 173), (x - 5, STREET - 174)], fill=YELLOW, outline=BLACK)


def _factory(draw, x, night, config):
    """The Factory at Franklin: the old stove works and its water tower."""
    for a, b in ((286, 270), (312, 308), (352, 356), (378, 394)):
        draw.line([x + a, STREET - 128, x + b, STREET], fill=BLACK, width=4)
    draw.line([x + 280, STREET - 90, x + 386, STREET - 112], fill=BLACK, width=2)
    draw.line([x + 280, STREET - 112, x + 386, STREET - 90], fill=BLACK, width=2)
    draw.rounded_rectangle([x + 280, STREET - 192, x + 384, STREET - 126], radius=10, fill=RED, outline=BLACK, width=2)
    draw.polygon([(x + 278, STREET - 188), (x + 332, STREET - 218), (x + 386, STREET - 188)], fill=RED, outline=BLACK)
    _text(draw, (x + 332, STREET - 175), "THE", 13, WHITE, config)
    _text(draw, (x + 332, STREET - 152), "FACTORY", 18, WHITE, config)
    roof = [(x, STREET), (x, STREET - 92)]
    for i in range(5):
        roof += [(x + i * 52 + 26, STREET - 114), (x + i * 52 + 52, STREET - 92)]
    draw.polygon(roof + [(x + 260, STREET)], fill=RED, outline=BLACK, width=2)
    _text(draw, (x + 130, STREET - 76), "DORTCH STOVE WORKS", 16, WHITE, config, max_width=240)
    for i in range(6):
        wx = x + 12 + i * 41
        draw.rectangle([wx, STREET - 60, wx + 28, STREET - 18], fill=YELLOW if night else WHITE, outline=BLACK, width=2)
        draw.line([wx + 14, STREET - 60, wx + 14, STREET - 18], fill=BLACK, width=2)


def draw_street(image: Image.Image, scene: str, night: bool, greeting: str, config: dict) -> None:
    """Paint the sky and one stretch of downtown Franklin."""
    draw = _canvas_draw(image)
    draw.rectangle([0, 0, W, H], fill=BLUE if night else WHITE)
    if night:
        rng = random.Random(4)
        for _ in range(90):
            x, y = rng.randrange(W), rng.randrange(48, 260)
            draw.rectangle([x, y, x + 1, y + 1], fill=WHITE)
        draw.ellipse([702, 58, 756, 112], fill=YELLOW)
        draw.ellipse([718, 50, 772, 104], fill=BLUE)

    if scene == "main_street":
        _shop(draw, 6, 150, 112, night, RED, "", config)
        _grays(draw, 164, night, config)
        _shop(draw, 344, 170, 126, night, BLACK, "WHITE'S MERCANTILE", config)
        _shop(draw, 522, 132, 106, night, GREEN, "THE PAINT STORE", config)
        _shop(draw, 662, 134, 138, night, BLUE, "", config)
    elif scene == "theatre":
        _shop(draw, 6, 220, 132, night, BLUE, "LANDMARK BOOKSELLERS", config)
        _theatre(draw, 240, night, greeting, config)
        _shop(draw, 526, 180, 118, night, RED, "SOUTHERN COTTAGE", config)
        _shop(draw, 716, 80, 146, night, GREEN, "", config)
    elif scene == "square":
        _shop(draw, 6, 128, 118, night, GREEN, "", config)
        _courthouse(draw, 160, night, config)
        _chip(draw, 452, config)
        _tree(draw, 566)
        _shop(draw, 664, 132, 132, night, RED, "", config)
    else:
        _shop(draw, 6, 140, 124, night, BLUE, "", config)
        _factory(draw, 180, night, config)
        _shop(draw, 614, 182, 112, night, GREEN, "", config)
    for left, right, y in ((6, 150, 300), (650, 794, 290)):
        _bulbs(draw, left, right, y, night)

    draw.rectangle([0, STREET + 1, W, H], fill=WHITE)
    draw.line([0, STREET, W, STREET], fill=BLACK, width=3)


def paste_sprite(image: Image.Image, name: str, center_x: int, center_y: int, halo: bool) -> None:
    """Paste a sprite centred on a point; at night a white rim separates it from the blue sky."""
    sprite = Image.open(SPRITES / f"{name}.png").convert("RGBA")
    corner = (center_x - sprite.width // 2, center_y - sprite.height // 2)
    alpha = sprite.getchannel("A")
    if halo:
        image.paste(WHITE, corner, alpha.filter(ImageFilter.MaxFilter(5)))
    image.paste(sprite.convert("RGB"), corner, alpha)


def _handler(draw, x, coat):
    draw.line([x - 4, STREET - 14, x - 6, STREET - 1], fill=BLACK, width=3)
    draw.line([x + 4, STREET - 14, x + 6, STREET - 1], fill=BLACK, width=3)
    draw.rounded_rectangle([x - 7, STREET - 34, x + 7, STREET - 12], radius=5, fill=coat, outline=BLACK)
    draw.ellipse([x - 6, STREET - 47, x + 6, STREET - 35], fill=WHITE, outline=BLACK, width=2)
    draw.pieslice([x - 7, STREET - 50, x + 7, STREET - 36], 180, 360, fill=coat, outline=BLACK)


def draw_weather(image: Image.Image, weather: str, night: bool, seed: int) -> None:
    if weather not in ("snow", "rain"):
        return
    draw = _canvas_draw(image)
    rng = random.Random(seed)
    ink = WHITE if night else BLUE
    for _ in range(130 if weather == "snow" else 170):
        x, y = rng.randrange(W), rng.randrange(44, STREET - 8)
        if weather == "rain":
            draw.line([x, y, x - 4, y + 11], fill=ink)
        elif rng.random() < 0.3:
            draw.line([x - 3, y, x + 3, y], fill=ink)
            draw.line([x, y - 3, x, y + 3], fill=ink)
            draw.line([x - 2, y - 2, x + 2, y + 2], fill=ink)
            draw.line([x - 2, y + 2, x + 2, y - 2], fill=ink)
        else:
            draw.rectangle([x, y, x + 2, y + 2], fill=ink)
    if weather == "snow":  # drifts along the curb
        points = [(0, STREET + 4)] + [(x, STREET - rng.randrange(3, 10)) for x in range(0, W + 25, 24)]
        draw.polygon(points + [(W, STREET + 4)], fill=WHITE, outline=BLACK)


def _header(image, countdown, config):
    draw = _canvas_draw(image)
    draw.rectangle([0, 0, W, 38], fill=RED)
    _text(draw, (14, 20), "FRANKLIN HOLIDAY PARADE", 19, WHITE, config, anchor="lm", max_width=420)
    _text(draw, (W - 14, 20), countdown, 17, WHITE, config, anchor="rm", max_width=330)


def _footer(draw, x, label, width, config, size=19):
    _text(draw, (x, 452), label, size, BLACK, config, max_width=width)


# ── Pictures ─────────────────────────────────────────────────────────────────

def render_snapshot(config, balloons, step, weather, night, today, greeting) -> Image.Image:
    """One moment of the march. Steps 0..N-1 show three balloons; step N is Santa's finale."""
    count = len(balloons)
    step %= count + 1
    image = Image.new("RGB", (W, H), WHITE)
    draw_street(image, SCENES[step % len(SCENES)], night, greeting, config)
    draw = _canvas_draw(image)
    if step == count:
        paste_sprite(image, "finale", 400, 232, night)
        rng = random.Random(step)
        for _ in range(110):
            x, y = rng.randrange(W), rng.randrange(46, 230)
            if x < 150 or x > 640:
                draw.rectangle([x, y, x + 4, y + 8], fill=rng.choice((RED, YELLOW, GREEN, ORANGE)))
        _footer(draw, 400, "Santa, his reindeer and elves", 760, config, size=22)
    else:
        for slot, x in enumerate((133, 400, 667)):  # newest on the left; the line marches right
            index = (step - slot) % count
            y = (186, 164, 202)[index % 3]
            for k, offset in enumerate((-78, -30, 30, 78)):
                draw.line([x + offset // 3, y + 40, x + offset, STREET - 28],
                          fill=WHITE if night else BLACK, width=1)
            for k, offset in enumerate((-78, -30, 30, 78)):
                _handler(draw, x + offset, COATS[(index + k) % 4])
            paste_sprite(image, f"{balloons[index]['file']}-small", x, y, night)
            _footer(draw, x, balloons[index]["name"], 250, config)
    draw_weather(image, weather, night, seed=step)
    _header(image, christmas_countdown(today), config)
    return image


def render_balloon_of_day(config, balloons, weather, night, today, greeting) -> Image.Image:
    santa = today.month == 12 and today.day in (24, 25)
    pick = {"name": "Santa", "file": "santa"} if santa else balloons[today.toordinal() % len(balloons)]
    image = Image.new("RGB", (W, H), WHITE)
    draw_street(image, SCENES[today.toordinal() % len(SCENES)], night, greeting, config)
    draw = _canvas_draw(image)
    for offset in (-150, -60, 60, 150):
        draw.line([400 + offset // 3, 300, 400 + offset, STREET - 28], fill=WHITE if night else BLACK, width=2)
    for k, offset in enumerate((-150, -60, 60, 150)):
        _handler(draw, 400 + offset, COATS[k])
    paste_sprite(image, f"{pick['file']}-large", 400, 224, night)
    _footer(draw, 400, f"Balloon of the day: {pick['name']}", 760, config, size=24)
    draw_weather(image, weather, night, seed=today.toordinal())
    _header(image, christmas_countdown(today), config)
    return image


def _next_step(state_file: str, count: int) -> int:
    try:
        with open(state_file) as f:
            step = (int(json.load(f)["step"]) + 1) % (count + 1)
    except (OSError, ValueError, KeyError, TypeError):
        step = 0
    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump({"step": step}, f)
    return step


def generate(config: dict) -> str:
    """Generate the parade image. Return output path."""
    cfg = config.get("holiday_parade", {})
    output_path = cfg.get("output_path", "images/holiday_parade.bmp")
    greeting = str(cfg.get("greeting", "Happy Holidays Franklin"))
    weather = str(cfg.get("weather", "auto"))
    night_setting = str(cfg.get("night", "auto"))
    now = datetime.now()

    conditions = _conditions(config) if "auto" in (weather, night_setting) else None
    if weather == "auto":
        weather = classify_weather(conditions)
    if night_setting == "auto":
        night = not conditions["is_day"] if conditions and "is_day" in conditions else not 7 <= now.hour < 17
    else:
        night = night_setting.lower() in ("on", "true", "yes", "night")

    balloons = load_balloons()
    if cfg.get("mode", "snapshot") == "balloon_of_day":
        image = render_balloon_of_day(config, balloons, weather, night, now.date(), greeting)
    else:
        step = _next_step(cfg.get("state_file", "data/holiday_parade_state.json"), len(balloons))
        image = render_snapshot(config, balloons, step, weather, night, now.date(), greeting)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    image.save(output_path)
    logger.info("Saved to %s (weather: %s, night: %s)", output_path, weather, night)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        generate(yaml.safe_load(f))
