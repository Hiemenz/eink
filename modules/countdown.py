"""
Countdown Timer Display module.

Renders an 800x480 BMP showing the number of days until the next upcoming
event from the configured list. No external API calls — purely date-math.

Config section (in config.yml):

    countdown:
      output_path: images/countdown_display.bmp
      events:                          # list of events; first upcoming one is shown
        - name: "Summer Vacation"
          date: "2026-08-15"           # YYYY-MM-DD target date
          start_date: "2026-07-01"     # optional: enables progress bar
          color: [255, 128, 0]         # optional: accent color (RGB list)
        - name: "Birthday"
          date: "2026-09-20"

Layout (800x480):
  - White background
  - Event name: bold, large (auto-sized, max ~52px), centered, in accent color
  - Giant day count number centered vertically, black (~200–240px tall)
  - "DAYS" label below the number, gray, size 22
  - Optional progress bar (600px wide, 18px) if start_date provided
  - Optional date range label "Jul 1 → Aug 15" below the bar, gray, small
  - Bottom-right: today's date "Mon Jul 21", gray, size 14
  - If 0 days: shows "TODAY!" instead of a number
  - If no future events: shows "No upcoming events" centered
"""

import os
import sys
from datetime import date, datetime
from PIL import Image, ImageDraw

# Add project root to path so utils is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("countdown")

WIDTH = 800
HEIGHT = 480

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (140, 140, 140)
LIGHT_GRAY = (200, 200, 200)
DEFAULT_ACCENT = (0, 0, 0)

# Fallback events shown when no events are configured
_FALLBACK_EVENTS = [
    {
        "name": "New Year's Day",
        "date": f"{date.today().year + 1}-01-01",
    }
]


def _parse_date(date_str):
    """Parse a YYYY-MM-DD string into a date object, or None on failure."""
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _pick_next_event(events, today):
    """Return the event with the smallest positive days-remaining.

    If multiple events share the smallest count (including 0), return the first
    in list order. Returns None if all events are in the past.
    """
    best = None
    best_days = None
    for ev in events:
        target = _parse_date(ev.get("date", ""))
        if target is None:
            continue
        days_left = (target - today).days
        if days_left < 0:
            continue
        if best_days is None or days_left < best_days:
            best = ev
            best_days = days_left
    return best, best_days


def _short_date(d):
    """Format a date as 'Jul 1' (abbreviated month, no leading zero on day)."""
    return d.strftime("%b %-d")


def _fit_name_font(draw, name, config, max_width, max_size=52, min_size=18):
    """Find the largest bold font size at which name fits within max_width."""
    for size in range(max_size, min_size - 1, -2):
        font = get_font(size, bold=True, config=config)
        bbox = draw.textbbox((0, 0), name, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            return font, size
    return get_font(min_size, bold=True, config=config), min_size


def _draw_centered_text(draw, text, font, y, color, width=WIDTH):
    """Draw text centered horizontally at vertical position y."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = (width - w) // 2
    draw.text((x, y), text, fill=color, font=font)
    return bbox[3] - bbox[1]  # return text height


def _render(event, days_left, today, output_path, config):
    """Render the countdown image and save it to output_path."""
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    name = event.get("name", "Upcoming Event")
    raw_color = event.get("color")
    if isinstance(raw_color, (list, tuple)) and len(raw_color) == 3:
        accent = tuple(int(c) for c in raw_color)
    else:
        accent = DEFAULT_ACCENT

    margin_x = 40
    usable_width = WIDTH - 2 * margin_x

    # ── Event name ────────────────────────────────────────────────────────────
    name_font, name_size = _fit_name_font(draw, name, config, usable_width)
    name_bbox = draw.textbbox((0, 0), name, font=name_font)
    name_h = name_bbox[3] - name_bbox[1]
    name_y = 24
    _draw_centered_text(draw, name, name_font, name_y, accent)
    content_top = name_y + name_h + 10

    # ── Bottom-right: today's date ─────────────────────────────────────────
    date_font = get_font(14, bold=False, config=config)
    date_str = today.strftime("%a %b %-d")
    date_bbox = draw.textbbox((0, 0), date_str, font=date_font)
    date_w = date_bbox[2] - date_bbox[0]
    date_h = date_bbox[3] - date_bbox[1]
    date_x = WIDTH - margin_x - date_w
    date_y = HEIGHT - 18 - date_h
    draw.text((date_x, date_y), date_str, fill=GRAY, font=date_font)

    # Reserve space at the bottom for the date label
    content_bottom = date_y - 10

    # ── Progress bar area (reserve space even before drawing) ──────────────
    start_date = _parse_date(event.get("start_date", ""))
    target_date = _parse_date(event.get("date", ""))
    has_progress = (start_date is not None and target_date is not None
                    and start_date < target_date)

    bar_h = 18
    range_font = get_font(14, bold=False, config=config)
    range_bbox = draw.textbbox((0, 0), "Ay", font=range_font)
    range_h = range_bbox[3] - range_bbox[1]

    progress_block_h = 0
    if has_progress:
        progress_block_h = 10 + bar_h + 6 + range_h + 4  # gap + bar + gap + label + pad

    # ── "DAYS" label ──────────────────────────────────────────────────────
    days_label_font = get_font(22, bold=False, config=config)
    days_label_bbox = draw.textbbox((0, 0), "DAYS", font=days_label_font)
    days_label_h = days_label_bbox[3] - days_label_bbox[1]

    # ── Giant number / TODAY! ─────────────────────────────────────────────
    # Available vertical space for the number + "DAYS" + progress block
    available_h = content_bottom - content_top - days_label_h - 6 - progress_block_h

    if days_left == 0:
        number_str = "TODAY!"
    else:
        number_str = str(days_left)

    # Auto-size the big number to fill available height (target 200–240px)
    max_num_h = max(80, available_h - 20)
    num_font = None
    for size in range(260, 40, -4):
        test_font = get_font(size, bold=False, config=config)
        bbox = draw.textbbox((0, 0), number_str, font=test_font)
        h = bbox[3] - bbox[1]
        w = bbox[2] - bbox[0]
        if h <= max_num_h and w <= usable_width:
            num_font = test_font
            num_h = h
            num_w = w
            break
    if num_font is None:
        num_font = get_font(80, bold=False, config=config)
        bbox = draw.textbbox((0, 0), number_str, font=num_font)
        num_h = bbox[3] - bbox[1]
        num_w = bbox[2] - bbox[0]

    # Center the number+DAYS+progress block vertically in the available space
    total_block_h = num_h + 6 + days_label_h + progress_block_h
    block_top = content_top + (content_bottom - content_top - total_block_h) // 2

    num_x = (WIDTH - num_w) // 2
    num_y = block_top
    draw.text((num_x, num_y), number_str, fill=BLACK, font=num_font)

    # ── "DAYS" label ─────────────────────────────────────────────────────
    days_y = num_y + num_h + 6
    _draw_centered_text(draw, "DAYS", days_label_font, days_y, GRAY)

    # ── Progress bar ─────────────────────────────────────────────────────
    if has_progress:
        bar_width = 600
        bar_x = (WIDTH - bar_width) // 2
        bar_y = days_y + days_label_h + 10

        total_days = max(1, (target_date - start_date).days)
        elapsed_days = max(0, min((today - start_date).days, total_days))
        fraction = elapsed_days / total_days

        # Outline
        draw.rectangle(
            [bar_x, bar_y, bar_x + bar_width, bar_y + bar_h],
            outline=LIGHT_GRAY,
            width=1,
        )
        # Fill
        fill_w = int(bar_width * fraction)
        if fill_w > 0:
            draw.rectangle(
                [bar_x, bar_y, bar_x + fill_w, bar_y + bar_h],
                fill=BLACK,
            )

        # Date range label
        range_str = f"{_short_date(start_date)} → {_short_date(target_date)}"
        range_y = bar_y + bar_h + 6
        _draw_centered_text(draw, range_str, range_font, range_y, GRAY)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("Saved countdown image to %s", output_path)
    return output_path


def _render_no_events(output_path, config):
    """Render a simple 'No upcoming events' placeholder image."""
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    font = get_font(32, bold=False, config=config)
    _draw_centered_text(draw, "No upcoming events", font, HEIGHT // 2 - 20, GRAY)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("No upcoming events — saved placeholder to %s", output_path)
    return output_path


def generate(config):
    """Generate countdown timer image. Returns output path."""
    cfg = config.get("countdown", {})
    output_path = cfg.get("output_path", "images/countdown_display.bmp")
    events = cfg.get("events")

    if not events:
        logger.warning("[countdown] No events configured — using fallback sample event.")
        events = _FALLBACK_EVENTS

    today = date.today()
    event, days_left = _pick_next_event(events, today)

    if event is None:
        return _render_no_events(output_path, config)

    logger.info(
        "[countdown] Next event: %s on %s (%d days away)",
        event.get("name"),
        event.get("date"),
        days_left,
    )
    return _render(event, days_left, today, output_path, config)


if __name__ == "__main__":
    import yaml

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
