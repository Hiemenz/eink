"""Poll-closing schedule: when each state's polls close (ET and local).

The image only changes when a group of polls closes, so it never forces a
panel refresh between closings (no ticking countdown).
"""

from datetime import datetime, timezone

from modules import election_common as ec


def groups(data):
    """[(utc_datetime, [postal, ...])] sorted by closing time."""
    by = {}
    for t in data.get("pollClosingTimes", []):
        ts = datetime.fromisoformat(t["poll_closing_time"].replace("Z", "+00:00"))
        by.setdefault(ts, []).append(t["reporting_unit"]["state_postal"])
    return sorted((ts, sorted(v)) for ts, v in by.items())


def polls_open(data, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    return any(ts > now for ts, _ in groups(data))


def generate(config):
    out = config.get("election", {}).get("polls_output_path", "images/election_polls.bmp")
    data = ec.fetch_results("senate", config)
    now = datetime.now(timezone.utc)
    gs = groups(data)
    next_i = next((i for i, (ts, _) in enumerate(gs) if ts > now), None)
    img, d, y = ec.canvas(config, "POLL CLOSING TIMES", "", stamp=ec.feed_time(data))
    tf, sf = ec.get_font(20, bold=True, config=config), ec.get_font(16, config=config)
    rh = min((ec.HEIGHT - y - 30) // max(len(gs), 1), 56)
    for i, (ts, states) in enumerate(gs):
        closed = ts <= now
        col = ec.BLACK if closed else (ec.YELLOW if i == next_i else ec.WHITE)
        d.rectangle([14, y, ec.WIDTH - 14, y + rh - 4], fill=col, outline=ec.BLACK, width=2)
        fg = ec.WHITE if closed else ec.BLACK
        local = ts.astimezone().strftime("%-I:%M %p %Z")
        tag = "CLOSED" if closed else ("NEXT" if i == next_i else "")
        d.text((22, y + 4), f"{local}", fill=fg, font=tf)
        tg = ec.get_font(14, bold=True, config=config)
        d.text((ec.WIDTH - 24 - d.textlength(tag, font=tg), y + 6), tag, fill=fg, font=tg)
        d.text((190, y + 6), " ".join(states), fill=fg, font=sf)
        y += rh
    ec.legend(d, config)
    return ec.finish(img, out)
