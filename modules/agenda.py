"""
Agenda / Calendar Display module.

Renders an 800x480 BMP showing upcoming events pulled from one or more public
or "secret" iCal (.ics) URLs. This is KEYLESS — no OAuth. It works with:
  - Google Calendar  ("Settings > Integrate calendar > Secret address in iCal format")
  - Outlook / Office 365  (published ICS link)
  - Apple iCloud Calendar  (public share link)
  - Any server that serves a standard .ics file

Parsing: if the `icalendar` or `ics` package is importable it is used; otherwise
a self-contained stdlib VEVENT parser handles the common cases (SUMMARY, DTSTART,
DTEND with DATE and DATE-TIME forms, LOCATION, line unfolding). No new
dependencies are added.

Config section (in config.yml):

    agenda:
      output_path: images/agenda_display.bmp
      ics_urls: []          # list of iCal (.ics) URLs; Google Calendar "secret
                            # address in iCal format" works
      days_ahead: 7
      cache_dir: data/

Layout (800x480), white background:
  - Full-width black header bar: "Agenda" (left) + today's date "Monday, July 21"
    (right).
  - Events grouped by day. Each day gets a sub-header ("TODAY", "TOMORROW", then
    "Wed Jul 23"), with its events beneath.
  - Each event row: time column (left ~90px), bold title (truncated), optional
    gray location on a second line, and a small colored bar on the far left that
    rotates through e-ink-friendly colors per calendar source.
  - "+N more" footer note when events overflow. TODAY/TOMORROW are prioritized.
  - No ics_urls configured  -> "Configure a calendar" help screen (not an error).
  - All fetches fail, no cache -> "Calendar unavailable" centered.
  - Reachable but zero upcoming events -> "No upcoming events" (header shown).

Events are cached for 15 minutes in <cache_dir>/agenda_cache.json; on fetch
failure the stale cache is reused.
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests
from PIL import Image, ImageDraw

# Add project root to path so utils is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("agenda")

WIDTH = 800
HEIGHT = 480

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (140, 140, 140)
LIGHT_GRAY = (200, 200, 200)
DARK_GRAY = (80, 80, 80)

# E-ink-friendly accent colors, rotated per calendar source.
SOURCE_COLORS = [
    (0, 90, 200),      # blue
    (200, 60, 40),     # red
    (30, 150, 70),     # green
    (200, 120, 0),     # orange
    (120, 60, 170),    # purple
    (0, 140, 150),     # teal
]

CACHE_TTL_SECONDS = 15 * 60
HTTP_TIMEOUT = 15


# ─────────────────────────────────────────────────────────────────────────────
# iCal parsing
# ─────────────────────────────────────────────────────────────────────────────
def _unfold_lines(text):
    """Unfold RFC 5545 folded lines (continuation lines start with space/tab)."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = []
    for line in raw:
        if line and line[0] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _split_prop(line):
    """Split an unfolded content line into (name, params_dict, value).

    e.g. 'DTSTART;TZID=America/New_York:20260721T093000' ->
         ('DTSTART', {'TZID': 'America/New_York'}, '20260721T093000')
    """
    if ":" not in line:
        return None, {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v
    return name, params, value


def _unescape(value):
    """Unescape iCal TEXT values (\\, / \\n etc.)."""
    return (
        value.replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _parse_dt(value, params):
    """Parse an iCal DTSTART/DTEND value into (datetime_or_date, all_day_bool).

    Returns (None, False) on failure. All-day events (VALUE=DATE or bare
    YYYYMMDD) yield a date object. Timed events yield an aware/naive datetime;
    UTC 'Z' values are converted to naive local wall-clock-agnostic form kept in
    UTC for consistent day-grouping, then normalized by the caller.
    """
    value = value.strip()
    if not value:
        return None, False

    is_date = params.get("VALUE", "").upper() == "DATE"
    # Bare 8-digit date with no time component => all-day
    if is_date or (len(value) == 8 and value.isdigit()):
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date(), True
        except ValueError:
            return None, False

    # Date-time forms: YYYYMMDDTHHMMSS[Z]
    m = re.match(r"^(\d{8})T(\d{6})(Z)?$", value)
    if m:
        ymd, hms, zulu = m.group(1), m.group(2), m.group(3)
        try:
            dt = datetime.strptime(ymd + hms, "%Y%m%d%H%M%S")
        except ValueError:
            return None, False
        if zulu:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt, False

    # Fallback: try a couple of lenient forms
    for fmt in ("%Y%m%dT%H%M", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value, fmt), False
        except ValueError:
            continue
    return None, False


def _to_local_naive(dt):
    """Normalize a datetime to naive local time for consistent day-grouping."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_ics_fallback(text):
    """Minimal stdlib VEVENT parser. Returns a list of event dicts.

    Robust by design: any VEVENT that can't be parsed is skipped, never raised.
    """
    events = []
    lines = _unfold_lines(text)
    in_event = False
    cur = {}
    for line in lines:
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event = True
            cur = {}
            continue
        if stripped == "END:VEVENT":
            if in_event:
                ev = _finalize_event(cur)
                if ev:
                    events.append(ev)
            in_event = False
            cur = {}
            continue
        if not in_event:
            continue

        name, params, value = _split_prop(line)
        if name is None:
            continue
        try:
            if name == "SUMMARY":
                cur["summary"] = _unescape(value)
            elif name == "LOCATION":
                cur["location"] = _unescape(value)
            elif name == "DTSTART":
                cur["start"], cur["all_day"] = _parse_dt(value, params)
            elif name == "DTEND":
                cur["end"], _ = _parse_dt(value, params)
            elif name == "RRULE":
                cur["rrule"] = value
        except Exception:
            # Never let a single malformed property kill the parse.
            continue
    return events


def _finalize_event(cur):
    """Turn a raw property dict into a normalized event dict, or None."""
    start = cur.get("start")
    if start is None:
        return None
    all_day = cur.get("all_day", False)
    if not all_day and isinstance(start, datetime):
        start = _to_local_naive(start)
    ev = {
        "summary": cur.get("summary", "(No title)") or "(No title)",
        "location": cur.get("location", ""),
        "start": start,
        "all_day": all_day,
        "rrule": cur.get("rrule", ""),
    }
    return ev


def _try_library_parse(text):
    """Parse using `icalendar` or `ics` if importable; else return None."""
    try:
        import icalendar  # type: ignore
    except Exception:
        icalendar = None

    if icalendar is not None:
        try:
            cal = icalendar.Calendar.from_ical(text)
            events = []
            for comp in cal.walk("VEVENT"):
                dtstart = comp.get("DTSTART")
                if dtstart is None:
                    continue
                val = dtstart.dt
                all_day = not isinstance(val, datetime)
                start = val
                if isinstance(start, datetime):
                    start = _to_local_naive(start)
                events.append(
                    {
                        "summary": str(comp.get("SUMMARY", "(No title)")) or "(No title)",
                        "location": str(comp.get("LOCATION", "")),
                        "start": start,
                        "all_day": all_day,
                        "rrule": str(comp.get("RRULE", "")),
                    }
                )
            return events
        except Exception as exc:  # pragma: no cover - library present but bad data
            logger.warning("[agenda] icalendar parse failed, using fallback: %s", exc)
            return None
    return None


def parse_ics(text):
    """Parse ICS text via library if available, else the stdlib fallback."""
    events = _try_library_parse(text)
    if events is not None:
        return events
    return _parse_ics_fallback(text)


# ─────────────────────────────────────────────────────────────────────────────
# Recurrence (best-effort, simple DAILY/WEEKLY only)
# ─────────────────────────────────────────────────────────────────────────────
def _next_recurring_occurrence(ev, window_start, window_end):
    """Best-effort next occurrence within the window for simple DAILY/WEEKLY.

    Returns a new event dict shifted to the next occurring date, or None. Only
    handles unqualified DAILY / WEEKLY (optionally with INTERVAL). Anything more
    complex is skipped, per spec.
    """
    rrule = ev.get("rrule", "") or ""
    m = re.search(r"FREQ=([A-Z]+)", rrule)
    if not m:
        return None
    freq = m.group(1)
    if freq not in ("DAILY", "WEEKLY"):
        return None
    im = re.search(r"INTERVAL=(\d+)", rrule)
    interval = int(im.group(1)) if im else 1
    if interval < 1:
        interval = 1
    step = timedelta(days=interval if freq == "DAILY" else 7 * interval)

    start = ev["start"]
    all_day = ev["all_day"]
    base_date = start if all_day else start.date()

    # Advance from the original start until we land in (or past) the window.
    cur = base_date
    guard = 0
    while cur < window_start and guard < 4000:
        cur = cur + step
        guard += 1
    if window_start <= cur <= window_end:
        new = dict(ev)
        new["rrule"] = ""  # treat the projected occurrence as a one-off
        if all_day:
            new["start"] = cur
        else:
            new["start"] = datetime.combine(cur, start.time())
        return new
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + cache
# ─────────────────────────────────────────────────────────────────────────────
def _event_day(ev):
    """Return the date an event falls on for grouping."""
    start = ev["start"]
    return start if isinstance(start, date) and not isinstance(start, datetime) else start.date()


def _collect_events(ics_urls, days_ahead):
    """Fetch and parse all URLs. Returns (events, any_success).

    events: list of dicts each with source_index, start (ISO), etc., filtered to
    the [today, today+days_ahead] window and sorted chronologically.
    """
    today = date.today()
    window_start = today
    window_end = today + timedelta(days=days_ahead)

    all_events = []
    any_success = False

    for idx, url in enumerate(ics_urls):
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            text = resp.text
        except Exception as exc:
            logger.warning("[agenda] Fetch failed for source %d: %s", idx, exc)
            continue

        any_success = True
        try:
            parsed = parse_ics(text)
        except Exception as exc:
            logger.warning("[agenda] Parse failed for source %d: %s", idx, exc)
            continue

        for ev in parsed:
            try:
                if ev["start"] is None:
                    continue
                if ev.get("rrule"):
                    occ = _next_recurring_occurrence(ev, window_start, window_end)
                    if occ is None:
                        continue
                    ev = occ
                day = _event_day(ev)
                if window_start <= day <= window_end:
                    all_events.append(_serialize_event(ev, idx))
            except Exception:
                # Skip any event we can't process; never crash the collection.
                continue

    all_events.sort(key=lambda e: (e["day"], 0 if e["all_day"] else 1, e["sort_key"]))
    return all_events, any_success


def _serialize_event(ev, source_index):
    """Turn a parsed event into a JSON-serializable, sortable dict."""
    start = ev["start"]
    all_day = ev["all_day"]
    day = _event_day(ev)
    if all_day:
        sort_key = "00:00"
        time_label = "All day"
    else:
        sort_key = start.strftime("%H:%M")
        time_label = _format_time(start)
    return {
        "summary": ev["summary"],
        "location": ev.get("location", "") or "",
        "day": day.isoformat(),
        "all_day": all_day,
        "sort_key": sort_key,
        "time_label": time_label,
        "source_index": source_index,
    }


def _format_time(dt):
    """Format a datetime as '9:30 AM' (no leading zero on hour)."""
    try:
        return dt.strftime("%-I:%M %p")
    except ValueError:
        # Windows/strftime portability fallback
        s = dt.strftime("%I:%M %p")
        return s.lstrip("0")


def _cache_path(config):
    cfg = config.get("agenda", {})
    cache_dir = cfg.get("cache_dir", "data/")
    return os.path.join(cache_dir, "agenda_cache.json")


def _read_cache(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(path, events):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "events": events}, f)
    except Exception as exc:
        logger.warning("[agenda] Failed to write cache: %s", exc)


def _get_events(config, ics_urls, days_ahead):
    """Return (events, status) where status is 'ok', 'stale', or 'unavailable'.

    Uses a 15-minute cache; falls back to stale cache on total fetch failure.
    """
    path = _cache_path(config)
    cached = _read_cache(path)

    if cached and (time.time() - cached.get("ts", 0)) < CACHE_TTL_SECONDS:
        logger.info("[agenda] Using fresh cache (%d events)", len(cached.get("events", [])))
        return cached.get("events", []), "ok"

    events, any_success = _collect_events(ics_urls, days_ahead)
    if any_success:
        _write_cache(path, events)
        return events, "ok"

    # All fetches failed — fall back to stale cache if present.
    if cached is not None:
        logger.warning("[agenda] All fetches failed — using stale cache")
        return cached.get("events", []), "stale"

    return [], "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────────────────────
def _text_w(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate(draw, text, font, max_width):
    """Truncate text with an ellipsis so it fits within max_width."""
    if _text_w(draw, text, font) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ell
        if _text_w(draw, candidate, font) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best or ell


def _day_header_label(day, today):
    """Label a date as TODAY / TOMORROW / 'Wed Jul 23'."""
    delta = (day - today).days
    if delta == 0:
        return "TODAY"
    if delta == 1:
        return "TOMORROW"
    try:
        return day.strftime("%a %b %-d")
    except ValueError:
        return day.strftime("%a %b %d")


def _draw_header(draw, config, today):
    """Full-width black header bar: 'Agenda' left + full date right."""
    bar_h = 54
    draw.rectangle([0, 0, WIDTH, bar_h], fill=BLACK)
    title_font = get_font(30, bold=True, config=config)
    draw.text((24, (bar_h - 30) // 2 - 2), "Agenda", fill=WHITE, font=title_font)

    try:
        date_str = today.strftime("%A, %B %-d")
    except ValueError:
        date_str = today.strftime("%A, %B %d")
    date_font = get_font(22, bold=False, config=config)
    dw = _text_w(draw, date_str, date_font)
    draw.text((WIDTH - 24 - dw, (bar_h - 22) // 2 - 1), date_str, fill=WHITE, font=date_font)
    return bar_h


def _group_by_day(events):
    """Group serialized events into an ordered list of (date, [events])."""
    groups = {}
    order = []
    for ev in events:
        try:
            d = date.fromisoformat(ev["day"])
        except Exception:
            continue
        if d not in groups:
            groups[d] = []
            order.append(d)
        groups[d].append(ev)
    order.sort()
    return [(d, groups[d]) for d in order]


def _render_agenda(events, config, output_path, status):
    """Render the main agenda list."""
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    today = date.today()

    bar_h = _draw_header(draw, config, today)

    day_font = get_font(18, bold=True, config=config)
    time_font = get_font(18, bold=False, config=config)
    title_font = get_font(20, bold=True, config=config)
    loc_font = get_font(14, bold=False, config=config)
    footer_font = get_font(15, bold=False, config=config)

    left_margin = 20
    bar_w = 6                      # colored source bar width
    time_col_x = left_margin + bar_w + 12
    time_col_w = 90
    title_x = time_col_x + time_col_w + 12
    title_max_w = WIDTH - title_x - 24

    footer_reserve = 28
    content_bottom = HEIGHT - footer_reserve
    y = bar_h + 14

    groups = _group_by_day(events)

    row_h_single = 34             # event with no location
    row_h_double = 52             # event with location
    day_header_h = 30

    shown = 0
    total = len(events)
    overflow = False

    for day, day_events in groups:
        # Space needed for the day header + at least one row?
        if y + day_header_h + row_h_single > content_bottom:
            overflow = True
            break

        label = _day_header_label(day, today)
        draw.text((left_margin, y), label, fill=BLACK, font=day_font)
        # Thin underline under the day header
        lw = _text_w(draw, label, day_font)
        draw.line([(left_margin, y + 24), (left_margin + max(lw, 60), y + 24)], fill=LIGHT_GRAY, width=1)
        y += day_header_h

        for ev in day_events:
            has_loc = bool(ev.get("location"))
            row_h = row_h_double if has_loc else row_h_single
            if y + row_h > content_bottom:
                overflow = True
                break

            color = SOURCE_COLORS[ev.get("source_index", 0) % len(SOURCE_COLORS)]

            # Colored source bar
            draw.rectangle([left_margin, y + 2, left_margin + bar_w, y + row_h - 8], fill=color)

            # Time column
            draw.text((time_col_x, y + 2), ev["time_label"], fill=DARK_GRAY, font=time_font)

            # Title (bold, truncated)
            title = _truncate(draw, ev["summary"], title_font, title_max_w)
            draw.text((title_x, y), title, fill=BLACK, font=title_font)

            # Location (second line, small gray)
            if has_loc:
                loc = _truncate(draw, ev["location"], loc_font, title_max_w)
                draw.text((title_x, y + 26), loc, fill=GRAY, font=loc_font)

            y += row_h
            shown += 1

        if overflow:
            break

        y += 6  # gap between day groups

    # Footer: "+N more" and/or stale notice
    notes = []
    remaining = total - shown
    if overflow and remaining > 0:
        notes.append(f"+{remaining} more")
    if status == "stale":
        notes.append("cached data")
    if notes:
        note = "   •   ".join(notes)
        nw = _text_w(draw, note, footer_font)
        draw.text((WIDTH - 24 - nw, HEIGHT - 22), note, fill=GRAY, font=footer_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("[agenda] Saved agenda (%d/%d events shown) to %s", shown, total, output_path)
    return output_path


def _render_message(output_path, config, title, lines, with_header=False):
    """Render a centered informational screen (config / unavailable / empty)."""
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    top = 0
    if with_header:
        top = _draw_header(draw, config, date.today())

    title_font = get_font(34, bold=True, config=config)
    body_font = get_font(20, bold=False, config=config)

    # Vertical centering of the whole block within the region below the header.
    region_top = top + 10
    line_h = 30
    block_h = 48 + len(lines) * line_h
    y = region_top + max(10, (HEIGHT - region_top - block_h) // 2)

    tw = _text_w(draw, title, title_font)
    draw.text(((WIDTH - tw) // 2, y), title, fill=BLACK, font=title_font)
    y += 56

    for line in lines:
        lw = _text_w(draw, line, body_font)
        draw.text(((WIDTH - lw) // 2, y), line, fill=GRAY, font=body_font)
        y += line_h

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("[agenda] Saved message screen '%s' to %s", title, output_path)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def generate(config):
    """Generate the agenda image. Returns the output path."""
    cfg = config.get("agenda", {})
    output_path = cfg.get("output_path", "images/agenda_display.bmp")
    ics_urls = cfg.get("ics_urls") or []
    days_ahead = int(cfg.get("days_ahead", 7) or 7)

    # No-config state (not an error): explain how to set it up.
    if not ics_urls:
        logger.info("[agenda] No ics_urls configured — showing setup screen.")
        return _render_message(
            output_path,
            config,
            "Configure a calendar",
            [
                "Set agenda.ics_urls in config.yml to one or more",
                "iCal (.ics) URLs to see your upcoming events here.",
                "",
                "Google Calendar: Settings > your calendar >",
                "\"Secret address in iCal format\" — paste that URL.",
                "Outlook, Apple Calendar and others also work.",
            ],
            with_header=True,
        )

    events, status = _get_events(config, ics_urls, days_ahead)

    if status == "unavailable":
        logger.warning("[agenda] Calendar unavailable — no cache, all fetches failed.")
        return _render_message(
            output_path,
            config,
            "Calendar unavailable",
            ["Could not reach any calendar source.", "Will retry on the next refresh."],
            with_header=True,
        )

    if not events:
        logger.info("[agenda] Reachable but zero upcoming events.")
        return _render_message(
            output_path,
            config,
            "No upcoming events",
            [f"Nothing scheduled in the next {days_ahead} days."],
            with_header=True,
        )

    return _render_agenda(events, config, output_path, status)


if __name__ == "__main__":
    import yaml

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        cfg = {}
    path = generate(cfg)
    print(f"Output: {path}")
