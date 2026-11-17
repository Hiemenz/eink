"""Trusted Train: a playful, CSS-only animated header scene for a Streamlit dashboard.

Usage::

    from dashboard.trusted_train import train_html
    st.markdown(train_html("Global Home", mode="dark"), unsafe_allow_html=True)

``train_html`` returns one ``<style>`` block followed by markup. There is no JavaScript: every
moving thing is a CSS animation of ``transform``/``translate``/``scale``/``rotate``/``opacity``,
and all artwork is inline SVG carried as ``data:`` URIs. The stage is a size container, so every
length is in ``cqw`` and the scene scales with its width.

Coordinates in this file are stage units: 1 unit == 1cqw, x grows right, y grows *up* from the
bottom edge of the scene.
"""

from __future__ import annotations

import datetime as dt
import html
import math
import re
import time
from urllib.parse import quote

W = 100.0  # stage width in cqw
H = 25.0  # stage height in cqw
RAIL = 1.5  # top of the rail: train wheels sit here
STREET = 1.8  # sidewalk level in front of the shops
TALL = 1.1  # buildings are drawn 10% taller than their artwork
BASE = 1.3  # street buildings start here, i.e. below the tracks

# Terrace ledges as polylines (x, y). Left ends are ramps; the upper envelope of the three is the
# visible hillside the Monday/Wednesday crowd climbs.
MID = [(56.5, STREET), (59.5, 7.0), (W, 7.0)]
BACK = [(58.5, STREET), (63.0, 11.0), (W, 11.0)]
CREST = [(61.0, STREET), (66.5, 15.0), (87.0, 15.0), (W, 12.4)]
PLATEAU = 15.0
MEADOW = [(0, 7.7), (12, 8.3), (24, 7.9), (38, 7.4), (50, 6.9), (64, 6.4)]  # the lakeside meadow's crest

# Day cycle. (hour, level) stops, linear in between; the same table drives the CSS keyframes and
# the still-mode snapshot so the two can never disagree.
NIGHT_STOPS = ((0, 1), (5, 1), (6.5, 0), (18, 0), (19.5, 1), (24, 1))
WARM_STOPS = ((0, 0), (5, 0), (6, 0.5), (7.2, 0), (17.2, 0), (18.5, 0.5), (19.8, 0), (24, 0))
SUN_RISE, SUN_SET = 6.0, 19.0
MOON_RISE = 18.0
MODES = ("clock", "light", "dark", "auto")

SEASONS = ("spring", "summer", "fall", "winter")
THEMES = ("auto",) + SEASONS + ("christmas",)

DEFAULT_SLIDES = (
    ("Overview", "The numbers that matter, at a glance"),
    ("Trends", "See how each metric moves over time"),
    ("Regions", "Compare markets side by side"),
    ("Teams", "Who is working on what this week"),
    ("Reports", "Download and share the details"),
)

# Probabilities and odds, in one place so the tests and the scene agree.
P_STREET = 0.55
P_HILL = 0.45
P_DICKENS = 0.28
P_KINGS = 0.65
P_GAZEBO_CROWD = 0.75
P_SCARECROW = 0.80
P_SKATERS = 0.70
P_SLEDDERS = 0.70
GRINCH_ODDS = 5  # one page load in five
GRINCH_SECONDS = 350
STAFF_MIN, STAFF_MAX = 40, 100
STAFF_DAYS = (0, 2)  # Monday, Wednesday
CAROL_HOURS = ((12, 13), (15, 16))

# The village's events, in priority order. Each runs for the week leading up to its weekend.
EVENTS = ("auto", "none", "carol_weekend", "lantern_night", "harvest_fair", "founders_day", "spring_fair", "village_market")
EVENT_SEASONS = {
    "carol_weekend": ("winter",),
    "lantern_night": ("fall",),
    "harvest_fair": ("fall",),
    "founders_day": ("summer",),
    "spring_fair": ("spring",),
    "village_market": ("spring", "summer"),
}
EVENT_NAMES = {
    "carol_weekend": "Carol Weekend",
    "lantern_night": "Lantern Night",
    "harvest_fair": "Harvest Music Fair",
    "founders_day": "Founders' Day",
    "spring_fair": "Spring Fair",
    "village_market": "Village Market",
}


# --------------------------------------------------------------------------------------------
# Chance
# --------------------------------------------------------------------------------------------
class LCG:
    """Small seeded linear congruential generator (Numerical Recipes constants)."""

    def __init__(self, seed: int):
        s = (int(seed) * 2654435761 + 0x9E3779B9) & 0xFFFFFFFF
        s ^= s >> 15
        s = (s * 2246822519) & 0xFFFFFFFF
        s ^= s >> 13
        self.state = s or 1
        for _ in range(3):
            self._next()

    def _next(self) -> int:
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        return self.state

    def random(self) -> float:
        return (self._next() >> 8) / float(1 << 24)

    def randint(self, a: int, b: int) -> int:
        return a + int(self.random() * (b - a + 1))

    def choice(self, seq):
        return seq[int(self.random() * len(seq))]

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.random()

    def chance(self, p: float) -> bool:
        return self.random() < p

    def sample(self, seq, k: int) -> list:
        pool = list(seq)
        out = []
        for _ in range(min(k, len(pool))):
            out.append(pool.pop(int(self.random() * len(pool))))
        return out


# --------------------------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------------------------
def season_for(day: dt.date) -> str:
    m = day.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


def is_christmastide(day: dt.date) -> bool:
    """Dec 1 through Jan 6 (Epiphany)."""
    return day.month == 12 or (day.month == 1 and day.day <= 6)


def days_to_christmas(day: dt.date) -> int:
    """Days until Dec 25; 0 on the day, negative from Dec 26 to Jan 6."""
    year = day.year - 1 if day.month == 1 else day.year
    return (dt.date(year, 12, 25) - day).days


def kings_progress(day: dt.date) -> float:
    """0.0 while the kings wait (before Dec 25), rising to 1.0 on Jan 6."""
    if not is_christmastide(day):
        return 0.0
    return min(1.0, max(0.0, -days_to_christmas(day) / 12.0))


def present_count(day: dt.date) -> int:
    """Presents on the post office pile: more as Christmas approaches, none after."""
    left = days_to_christmas(day)
    if left < 0 or left > 24:
        return 3 if left > 24 else 0
    return 4 + (24 - left)


def is_carol_time(now: dt.datetime) -> bool:
    return any(a <= now.hour < b for a, b in CAROL_HOURS)


def day_level(stops, hour: float) -> float:
    """Piecewise-linear level (0..1) at ``hour`` (0..24) from (hour, level) stops."""
    for (h0, v0), (h1, v1) in zip(stops, stops[1:]):
        if hour <= h1:
            return v0 + (v1 - v0) * (hour - h0) / (h1 - h0)
    return stops[-1][1]


def night_level(hour: float) -> float:
    return day_level(NIGHT_STOPS, hour)


def orbit(hour: float, rise: float) -> tuple:
    """(x in cqw, height in cqw, opacity) of a sun/moon that rises at ``rise`` and travels 13 hours."""
    phase = ((hour - rise) % 24) / 13.0
    if phase > 1:
        return 6.0, 0.0, 0.0
    return 6.0 + 82.0 * phase, 10.0 * math.sin(math.pi * phase), min(1.0, 6 * min(phase, 1 - phase))


def nth_saturday(year: int, month: int, n: int) -> dt.date:
    """The n-th Saturday of a month; n = -1 for the last one."""
    if n > 0:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(5 - first.weekday()) % 7 + 7 * (n - 1))
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - 5) % 7)


def _lead_up(day: dt.date, saturday: dt.date) -> bool:
    """The Monday before a festival Saturday through its Sunday."""
    return -5 <= (day - saturday).days <= 1


def village_event(day: dt.date) -> str | None:
    """The village event the town is preparing for or enjoying on ``day`` (None if quiet)."""
    y = day.year
    if _lead_up(day, nth_saturday(y, 12, 2)):  # Carol Weekend: second weekend of December
        return "carol_weekend"
    if dt.date(y, 11, 26) <= day <= dt.date(y, 11, 30):  # Lantern Night, end of November
        return "lantern_night"
    if _lead_up(day, nth_saturday(y, 9, -1)):  # Harvest Music Fair: last weekend of September
        return "harvest_fair"
    if dt.date(y, 7, 1) <= day <= dt.date(y, 7, 5):
        return "founders_day"
    if _lead_up(day, nth_saturday(y, 4, -1)):  # Spring Fair: last weekend of April
        return "spring_fair"
    if day.weekday() == 5 and season_for(day) in EVENT_SEASONS["village_market"]:
        return "village_market"
    return None


def resolve_event(event: str, theme: str, day: dt.date):
    """(season, christmas?, event) for the ``event`` and ``theme`` arguments.

    A named event picks its own season when the theme is "auto" and is an error against a theme
    from another season. "auto" follows the calendar and is dropped if the theme disagrees.
    """
    if event not in EVENTS:
        raise ValueError("event must be one of %s" % ", ".join(EVENTS))
    ev = None
    if event == "auto":
        ev = village_event(day)
    elif event != "none":
        ev = event
        if theme == "auto":
            theme = "christmas" if ev == "carol_weekend" else EVENT_SEASONS[ev][0]
    season, xmas = _resolve(theme, day)
    if ev and (season not in EVENT_SEASONS[ev] or (ev == "carol_weekend" and not xmas)):
        if event != "auto":
            raise ValueError("event %r does not happen in %s" % (ev, season))
        ev = None
    return season, xmas, ev


# --------------------------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------------------------
def _interp(profile, x: float) -> float:
    if x < profile[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(profile, profile[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return profile[-1][1]


def ground(x: float) -> float:
    """Height of the visible hillside at x: the upper envelope of street and terrace ledges."""
    return max(STREET, _interp(MID, x), _interp(BACK, x), _interp(CREST, x))


def climb_route(x_from: float, x_to: float, step: float = 0.125) -> list:
    """Polyline along the hillside between two x positions, corners kept, straights merged."""
    n = max(1, int(round(abs(x_to - x_from) / step)))
    pts = [(x_from + (x_to - x_from) * i / n,) for i in range(n + 1)]
    pts = [(p[0], ground(p[0])) for p in pts]
    out = [pts[0]]
    for prev, cur, nxt in zip(pts, pts[1:], pts[2:]):
        s0 = (cur[1] - prev[1]) / (cur[0] - prev[0])
        s1 = (nxt[1] - cur[1]) / (nxt[0] - cur[0])
        if abs(s0 - s1) > 1e-6:
            out.append(cur)
    out.append(pts[-1])
    return out


# --------------------------------------------------------------------------------------------
# SVG helpers. Art is drawn in "design units" inside its own viewBox, y down.
# --------------------------------------------------------------------------------------------
def _uri(vw: float, vh: float, body: str) -> str:
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {vw:g} {vh:g}' "
        f"preserveAspectRatio='none'>{body}</svg>"
    )
    return 'url("data:image/svg+xml,' + quote(svg, safe=" ='/:,.-;()") + '")'


def _r(x, y, w, h, f, o=""):
    return f"<rect x='{x:g}' y='{y:g}' width='{w:g}' height='{h:g}' fill='{f}'{o}/>"


def _p(pts, f, o=""):
    d = " ".join(f"{x:g},{y:g}" for x, y in pts)
    return f"<polygon points='{d}' fill='{f}'{o}/>"


def _c(x, y, r, f, o=""):
    return f"<circle cx='{x:g}' cy='{y:g}' r='{r:g}' fill='{f}'{o}/>"


def _e(x, y, rx, ry, f, o=""):
    return f"<ellipse cx='{x:g}' cy='{y:g}' rx='{rx:g}' ry='{ry:g}' fill='{f}'{o}/>"


def _ln(pts, stroke, sw, o=""):
    d = " ".join(f"{x:g},{y:g}" for x, y in pts)
    return (
        f"<polyline points='{d}' fill='none' stroke='{stroke}' stroke-width='{sw:g}' "
        f"stroke-linecap='round' stroke-linejoin='round'{o}/>"
    )


def _t(x, y, size, text, f="#fff", fit=None, o=""):
    fit_attr = f" textLength='{fit:g}' lengthAdjust='spacingAndGlyphs'" if fit else ""
    return (
        f"<text x='{x:g}' y='{y:g}' font-size='{size:g}' fill='{f}' text-anchor='middle' "
        f"font-family='Verdana,Arial,sans-serif' font-weight='700'{fit_attr}{o}>"
        f"{html.escape(text)}</text>"
    )


def _mix(a: str, b: str, t: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    ch = [
        round(int(a[i : i + 2], 16) * (1 - t) + int(b[i : i + 2], 16) * t)
        for i in (0, 2, 4)
    ]
    return "#%02x%02x%02x" % tuple(ch)


class _K:
    """Drawing context handed to every art function."""

    def __init__(self, night: bool, season: str, xmas: bool):
        self.night = night
        self.season = season
        self.xmas = xmas
        self.snow = season == "winter"
        self.fall = season == "fall"
        self.win = "#ffd66b" if night else "#cfe8f6"
        self.glow = "#fff3b0" if night else "#eef7fb"

    def w(self, c: str) -> str:
        """A surface colour, dimmed toward navy at night."""
        return _mix(c, "#0b1230", 0.58) if self.night else c

    def lit(self, day: str, night: str) -> str:
        return night if self.night else day


# --------------------------------------------------------------------------------------------
# Scene builder
# --------------------------------------------------------------------------------------------
class _Scene:
    def __init__(
        self, mode: str, still: bool, season: str, xmas: bool, event: str | None = None, secs: float = 0
    ):
        self.event = event
        self.clock = mode == "clock"
        self.secs = secs
        self.mode = mode
        self.still = still
        self.season = season
        self.xmas = xmas
        self.winter = season == "winter"
        self.fall = season == "fall"
        self.css: dict = {}
        self.dark: dict = {}
        self.out: list = []

    # -- css ---------------------------------------------------------------------------------
    def rule(self, key: str, css: str) -> None:
        self.css.setdefault(key, css)

    def themed(self, key: str, day: str, night: str) -> None:
        """A rule with a day and a night body, resolved for the current mode."""
        if self.mode == "dark":
            self.css.setdefault(key, night)
        else:
            self.css.setdefault(key, day)
            if self.mode == "auto":
                self.dark.setdefault(key, night)

    def art(self, name: str, vb, fn) -> str:
        cls = "tt-a-" + name
        if cls not in self.css:

            def img(night):
                k = _K(night, self.season, self.xmas)
                return ".%s{background-image:%s}" % (cls, _uri(vb[0], vb[1], fn(k)))

            self.themed(cls, img(False), img(True))
            if self.clock:  # the night picture, faded in over the day one by .tt-ov
                self.css[cls + "-n"] = img(True).replace("." + cls + "{", "." + cls + "-n{", 1)
        return cls

    def ov(self, cls: str) -> str:
        """In clock mode, the night-art overlay that goes inside a sprite carrying ``cls``."""
        m = re.search(r"tt-a-\w+", cls)
        if not self.clock or not m:
            return ""
        return '<i class="tt-ov %s-n" style="opacity:%s"></i>' % (m.group(0), _f(self.dark_now))

    @property
    def dark_now(self) -> float:
        return night_level(self.secs / 3600.0)

    def shows(self, when) -> bool:
        """False when an element tagged day/night can never be visible in this mode."""
        if when is None or self.mode in ("auto", "clock"):
            return True
        return when == ("night" if self.mode == "dark" else "day")

    # -- markup ------------------------------------------------------------------------------
    def put(self, cls, x, y, w, h, *, tip=None, style="", inner="", when=None, tag="div"):
        if not self.shows(when):
            return
        classes = "tt-s " + cls
        inner = self.ov(cls) + inner
        if when and self.mode == "auto":
            classes += " tt-" + when
        if tip:
            classes += " tt-h"
            side = " tt-tr" if x + w / 2 > 82 else (" tt-tl" if x + w / 2 < 12 else "")
            inner += '<span class="tt-tip%s">%s</span>' % (side, html.escape(tip))
        pos = "left:%scqw;bottom:%scqw;width:%scqw;height:%scqw;" % (
            _f(x),
            _f(y),
            _f(w),
            _f(h),
        )
        el = '<%s class="%s" style="%s%s">%s</%s>' % (tag, classes, pos, style, inner, tag)
        if when and self.clock:  # a full-stage layer that fades the sprite in (night) or out (day)
            lvl = self.dark_now if when == "night" else 1 - self.dark_now
            el = '<div class="tt-fx tt-%sf" style="opacity:%s">%s</div>' % (when[0], _f(lvl), el)
        self.out.append(el)

    def sprite(self, name, x, y, w, h, vb, fn, **kw):
        if not self.shows(kw.get("when")):
            return
        self.put(self.art(name, vb, fn), x, y, w, h, **kw)

    def anim(self, spec: str) -> str:
        """Inline animation declaration, dropped entirely in still mode."""
        return "" if self.still else "animation:%s;" % spec

    def raw(self, markup: str) -> None:
        self.out.append(markup)


def _f(v: float) -> str:
    return ("%.2f" % v).rstrip("0").rstrip(".")


# --------------------------------------------------------------------------------------------
# Terrain art (full-stage layers; design units are tenths of a cqw)
# --------------------------------------------------------------------------------------------
_PAL = {
    "spring": dict(
        far="#b9d6bd", meadow="#a5d188", crest="#6aa95c", back="#79b566", mid="#89c172"
    ),
    "summer": dict(
        far="#b3d1b0", meadow="#9ccb7f", crest="#5f9f55", back="#70ae5f", mid="#82bc6c"
    ),
    "fall": dict(
        far="#cdb890", meadow="#cdb463", crest="#a9773a", back="#b98842", mid="#c79a4e"
    ),
    "winter": dict(
        far="#d5e0ec", meadow="#eef3f8", crest="#d3dee9", back="#e0e8f0", mid="#ecf1f6"
    ),
}
_GROUND = {
    "spring": "#7fb866",
    "summer": "#77b05f",
    "fall": "#a39a4a",
    "winter": "#f4f7fa",
}
_SKY = {
    "spring": "linear-gradient(#8ecbf0,#e3f4fb 75%)",
    "summer": "linear-gradient(#7fc2ee,#dff2fb 75%)",
    "fall": "linear-gradient(#9cc7e8,#fde9cc 78%)",
    "winter": "linear-gradient(#b4cfe4,#f0f6fa 75%)",
}
_SKY_NIGHT = "linear-gradient(#070c26,#1b2752 70%,#2c3a6b)"


def _terr(profile, fill, edge):
    pts = [(x * 10, (H - y) * 10) for x, y in profile]
    poly = pts + [(W * 10, H * 10), (pts[0][0], H * 10)]
    return _p(poly, fill) + _ln(pts, edge, 1.6)


def _art_far(k: _K) -> str:
    pal = _PAL[k.season]
    hills = [
        (0, 12.2),
        (9, 13.6),
        (17, 11.6),
        (27, 14.2),
        (38, 11.8),
        (47, 13.4),
        (58, 12.0),
        (W, 12.0),
    ]
    far = k.w(pal["far"])
    out = _terr(hills, far, _mix(far, "#ffffff", 0.25))
    # viaduct for the far-away train
    y = (H - 9.25) * 10
    out += _r(0, y, 700, 1.6, k.w("#8a8f99"))
    for x in range(10, 700, 26):
        out += _r(x, y + 1.6, 2.2, 12, k.w("#9aa0aa"))
    return out


def _art_meadow(k: _K) -> str:
    pal = _PAL[k.season]
    m = k.w(pal["meadow"])
    return _terr(MEADOW, m, _mix(m, "#ffffff", 0.3))


def _art_terrace(which: str, profile):
    def fn(k: _K) -> str:
        c = k.w(_PAL[k.season][which])
        out = _terr(profile, c, _mix(c, "#ffffff", 0.35))
        if which == "mid":
            # stair treads on the three ramps the staff crowd climbs
            tread = _mix(c, "#000000", 0.18)
            for prof in (MID, BACK, CREST):
                (x0, y0), (x1, y1) = prof[0], prof[1]
                steps = 9
                for i in range(steps):
                    xa = x0 + (x1 - x0) * i / steps
                    if abs(ground(xa + 0.05) - _interp(prof, xa + 0.05)) > 1e-6:
                        continue
                    ya = y0 + (y1 - y0) * i / steps
                    if prof is MID:
                        out += _r(xa * 10, (H - ya) * 10 - 1, 4, 1.2, tread)
        return out

    return fn


def _art_ground(k: _K) -> str:
    g = k.w(_GROUND[k.season])
    out = _r(0, (H - STREET) * 10, 1000, STREET * 10, g)
    # sidewalk in front of the village
    out += _r(465, (H - STREET) * 10, 535, 3.4, k.w("#c9c3b6"))
    # ballast, sleepers, rail
    out += _r(0, (H - RAIL) * 10, 1000, 6.5, k.w("#8c8377"))
    for x in range(2, 1000, 9):
        out += _r(x, (H - RAIL) * 10 + 1.2, 4, 2.6, k.w("#5c4a3a"))
    out += _r(0, (H - RAIL) * 10, 1000, 1.2, k.w("#3d4148"))
    # foreground strip
    fg = _mix(g, "#ffffff", 0.12) if k.snow else _mix(g, "#000000", 0.1)
    out += _r(0, (H - 0.85) * 10, 1000, 8.5, fg)
    return out


def _art_lake(k: _K) -> str:
    if k.snow:
        water, shine = k.w("#c3deee"), k.w("#ffffff")
    else:
        water, shine = k.lit("#5fb0d8", "#1c3c6e"), k.lit("#a8dcf0", "#35589a")
    out = _e(70, 14, 70, 13.5, k.w("#b7a98a") if not k.snow else k.w("#a9c4d6"))
    out += _e(70, 14, 67.5, 12, water)
    for x, y, w in ((30, 9, 22), (72, 18, 30), (95, 10, 16), (48, 21, 14)):
        out += _r(x, y, w, 0.9, shine, " opacity='.7'")
    if k.night and not k.snow:
        out += _e(88, 8, 5, 1.2, "#f6f1c7", " opacity='.6'")
    return out


# --------------------------------------------------------------------------------------------
# Building art
# --------------------------------------------------------------------------------------------
def _decor(k: _K, w: float, top: float = 0) -> str:
    """Snow cap and Christmas lights along a flat roofline."""
    out = ""
    if k.snow:
        out += _r(0, top, w, 2.2, k.lit("#ffffff", "#c9d6ee"))
    if k.xmas:
        cols = ("#e63946", "#2a9d8f", "#f4c430", "#3a86ff")
        for i, x in enumerate(range(2, int(w) - 1, 4)):
            out += _c(x, top + 3.4, 0.8, cols[i % 4])
    return out


def _wreath(k: _K, x: float, y: float, r: float = 2.2) -> str:
    if not k.xmas:
        return ""
    return _c(x, y, r, "none", " stroke='#2f7d32' stroke-width='1.3'") + _c(
        x, y + r, 0.8, "#d62828"
    )


def _shop(
    sign,
    wall,
    trim,
    sign_bg,
    *,
    vb=(38, 44),
    awning=None,
    sign_fg="#ffffff",
    sub=None,
    door="#5b3a29",
    columns=False,
):
    """Generic storefront. The lowest 5 units are a plinth that sits below the tracks."""

    def fn(k: _K) -> str:
        w, h = vb
        out = _r(0, 4, w, h - 4, k.w(wall)) + _r(0, 0, w, 5, k.w(trim))
        y = 7
        if h >= 46:  # room for an upper floor
            n = max(2, int(w // 11))
            for i in range(n):
                cx = w * (i + 0.5) / n
                out += _r(cx - 3, y, 6, 7.5, k.win) + _r(
                    cx - 3.6, y + 7.5, 7.2, 1, k.w(trim)
                )
            y += 11
        out += _r(2, y, w - 4, 8, sign_bg if k.night else sign_bg)
        fit = w - 8 if len(sign) > 6 else None
        out += _t(w / 2, y + 6.3, 5.4, sign, sign_fg, fit)
        y += 9
        if awning:
            out += _p([(1.5, y), (w - 1.5, y), (w, y + 5), (0, y + 5)], k.w(awning))
            for x in range(5, int(w) - 3, 8):
                out += _p(
                    [(x, y), (x + 3.5, y), (x + 3.7, y + 5), (x - 0.2, y + 5)],
                    k.w("#ffffff"),
                    " opacity='.85'",
                )
            y += 6
        else:
            y += 2
        fh = h - 5 - y
        if columns:
            out += _r(3, y, w - 6, fh, k.w(_mix(wall, "#000000", 0.12)))
            for x in (5, w / 2 - 8, w / 2 + 5, w - 8):
                out += _r(x, y, 3, fh, k.w("#f1ece0"))
            out += _r(w / 2 - 3.5, y + 2, 7, fh - 2, k.w(door))
            out += _wreath(k, w / 2, y + 6)
        else:
            out += _r(3, y, w - 16, fh - 3, k.win)
            out += _r(3, y + (fh - 3) / 2, w - 16, 0.6, k.w(trim))
            out += _r(w - 11, y, 7.5, fh, k.w(door))
            out += _r(w - 9.6, y + 1.5, 4.7, fh * 0.42, k.win)
            out += _wreath(k, w - 7.2, y + 4.2)
            if sub:
                out += _r(3, y + fh - 8.6, w - 16, 4.2, "#fff8e7")
                out += _t(3 + (w - 16) / 2, y + fh - 5.5, 2.5, sub, "#5a2d0c", w - 18)
        out += _r(0, h - 5, w, 5, k.w("#8d8478"))
        return out + _decor(k, w)

    return fn


def _house(wall, roof, *, vb=(32, 30), chimney=True, lights=False, door="#6b3f2a"):
    def fn(k: _K) -> str:
        w, h = vb
        out = ""
        if chimney:
            out += _r(w * 0.68, 2, 3.6, 9, k.w("#8a5a44"))
        out += _p([(-1, 12), (w / 2, 0), (w + 1, 12)], k.w(roof))
        out += _r(1.5, 12, w - 3, h - 12, k.w(wall))
        if k.snow:
            out += _ln([(0.5, 11), (w / 2, 0.8), (w - 0.5, 11)], k.lit("#ffffff", "#c9d6ee"), 2)
        out += _r(4, 16, 6.5, 6.5, k.win) + _r(w - 10.5, 16, 6.5, 6.5, k.win)
        out += _r(w / 2 - 2.6, h - 10.5, 5.2, 10.5, k.w(door))
        out += _c(w / 2, 8, 1.7, k.win)
        out += _wreath(k, w / 2, h - 7.5, 1.6)
        if lights and k.xmas:
            cols = ("#ff3b3b", "#35d07f", "#ffd23f", "#4aa3ff", "#ffffff")
            n = 14
            for i in range(n + 1):
                t = i / n
                x = t * w
                y = 12 - 12 * (1 - abs(2 * t - 1))
                out += _c(x, y + 1.2, 1.0, cols[i % 5])
            for i, x in enumerate(range(2, int(w) - 1, 3)):
                out += _c(x, 13.2, 0.8, cols[i % 5])
                out += _c(x, h - 0.9, 0.8, cols[(i + 2) % 5])
            for y in range(14, int(h), 3):
                out += _c(1.6, y, 0.8, cols[y % 5]) + _c(w - 1.6, y, 0.8, cols[(y + 1) % 5])
        return out

    return fn


def _art_station(k: _K) -> str:
    w, h = 54, 40
    out = _r(4, 14, w - 8, h - 19, k.w("#c96f4a"))
    out += _p([(0, 15), (8, 4), (w - 8, 4), (w, 15)], k.w("#4d5562"))
    out += _p([(w / 2 - 9, 6), (w / 2, -1), (w / 2 + 9, 6)], k.w("#4d5562"))
    out += _c(w / 2, 5.2, 2.6, "#fffdf2") + _ln([(w / 2, 5.2), (w / 2, 3.6)], "#222", 0.5)
    out += _ln([(w / 2, 5.2), (w / 2 + 1.2, 5.6)], "#222", 0.5)
    out += _r(w / 2 - 11, 16.5, 22, 5.5, "#1f3a5f") + _t(w / 2, 20.8, 4.2, "STATION")
    for x in (8, 18, 30, 40):
        out += _r(x, 24.5, 6, 7, k.win)
    out += _r(24.3, 24.5, 5.4, 10.5, k.w("#5b3a29"))
    out += _wreath(k, 27, 27.5, 1.6)
    out += _r(0, h - 5, w, 5, k.w("#9b958a"))
    if k.snow:
        out += _ln([(1, 14), (8.5, 4.6), (w - 8.5, 4.6), (w - 1, 14)], k.lit("#fff", "#c9d6ee"), 1.8)
    return out


def _art_gazebo(k: _K) -> str:
    w, h = 34, 34
    roof = "#b5533c" if not k.fall else "#a8481f"
    out = _r(w / 2 - 0.6, 0, 1.2, 4, k.w("#6a4a2f"))
    out += _p([(1, 13), (w / 2, 2.5), (w - 1, 13)], k.w(roof))
    out += _r(0, 12.5, w, 1.8, k.w("#f5f1e6"))
    for x in (2.5, 11, 21.5, 30):
        out += _r(x, 14, 1.6, 15, k.w("#f5f1e6"))
    out += _r(1.5, 23, w - 3, 1, k.w("#f5f1e6"))
    for x in range(3, w - 2, 2):
        out += _r(x, 23, 0.5, 6, k.w("#f5f1e6"))
    out += _r(0, 29, w, 5, k.w("#b9ad98"))
    if k.snow:
        out += _ln([(1.5, 12.4), (w / 2, 3), (w - 1.5, 12.4)], k.lit("#fff", "#c9d6ee"), 1.8)
    if k.xmas:
        for i, x in enumerate(range(2, w - 1, 3)):
            out += _c(x, 15.2, 0.8, ("#e63946", "#f4c430", "#2a9d8f")[i % 3])
        out += _wreath(k, w / 2, 9, 2)
    if k.fall:
        for i, x in enumerate(range(2, w - 1, 3)):
            out += _c(x, 14.8 + (i % 2) * 0.8, 1.1, ("#e07a1f", "#c8451b", "#e3b23c")[i % 3])
        for x in (1.5, 27.5):
            out += _c(x + 2.4, 27.5, 2.3, "#e8761c") + _r(x + 2, 24.6, 0.8, 1.2, "#4f6b2a")
        out += _r(9, 25.5, 6, 3.5, "#d9b44a") + _r(19, 25.5, 6, 3.5, "#d9b44a")
    return out


def _art_theatre(k: _K) -> str:
    """Art-deco village playhouse."""
    w, h = 58, 52
    brick = k.w("#c7a36d")
    out = _r(0, 10, w, h - 10, brick) + _r(0, 8, w, 3, k.w("#8a6a3c"))
    out += _p([(18, 10), (29, 4), (40, 10)], k.w("#8a6a3c"))
    for x in (5, 46):
        out += _r(x, 14, 7, 8, k.win)
    neon = k.lit("#c9302c", "#ff4d4d")
    # vertical blade sign
    out += _r(24.5, 0, 9, 25, k.lit("#7a1512", "#3a0606"))
    out += _r(24.5, 0, 9, 25, "none", f" stroke='{k.lit('#f1c84b', '#ffe066')}' stroke-width='.9'")
    for i, ch in enumerate("THEATRE"):
        out += _t(29, 4 + i * 3.25, 3.3, ch, k.lit("#ffe9a8", "#fffbe0"))
    # marquee
    out += _p([(3, 25), (55, 25), (58, 34), (0, 34)], k.lit("#f6f0df", "#fff7d1"))
    out += _r(0, 33, w, 2, neon) + _r(3, 24, 52, 1.6, neon)
    line = "IT'S A WONDERFUL LIFE" if k.xmas else "NOW SHOWING  TONIGHT"
    out += _t(29, 31.2, 3.6, line, "#2b2b2b", 46)
    bulb = k.lit("#e8c46a", "#fff27a")
    for x in range(2, 58, 4):
        out += _c(x, 35.6, 0.9, bulb)
    # doors and poster cases
    out += _r(5, 38, 9, 9, k.lit("#33291f", "#2a2118")) + _r(6, 39, 7, 7, k.win)
    out += _r(44, 38, 9, 9, k.lit("#33291f", "#2a2118")) + _r(45, 39, 7, 7, k.win)
    for x in (19, 30):
        out += _r(x, 37.5, 9, 9.5, k.w("#6b2d1a")) + _r(x + 1.4, 38.8, 6.2, 5, k.win)
    out += _r(25.8, 35.5, 6.4, 5, k.w("#8a6a3c")) + _r(27, 36.4, 4, 3.2, k.win)
    out += _r(0, h - 5, w, 5, k.w("#8d8478"))
    if k.xmas:
        for i, x in enumerate(range(1, 58, 3)):
            out += _c(x, 12.5 + (i % 2), 1.0, ("#2f7d32", "#d62828")[i % 2])
        out += _wreath(k, 8.5, 17.5, 2.4) + _wreath(k, 49.5, 17.5, 2.4)
        out += _wreath(k, 9.5, 41.5, 1.5) + _wreath(k, 48.5, 41.5, 1.5)
    if k.snow:
        out += _r(0, 8, 24.5, 1.8, k.lit("#fff", "#c9d6ee")) + _r(33.5, 8, 24.5, 1.8, k.lit("#fff", "#c9d6ee"))
    return out


def _art_office(k: _K) -> str:
    """The market hall: brick and glass."""
    w, h = 78, 48
    out = _r(0, 6, w, h - 6, k.w("#9b4a35")) + _r(0, 4, w, 3, k.w("#3b3e44"))
    out += _r(22, 0, 34, 8, k.w("#3b3e44"))
    out += _t(39, 6.2, 4.4, "MARKET HALL", k.lit("#ffffff", "#ffffff"), 30)
    for row in (11, 22):
        for x in range(4, 74, 9):
            out += _r(x, row, 6.5, 8, k.win)
    out += _r(28, 31, 22, 12, k.win)
    for x in (28, 35.3, 42.6):
        out += _r(x, 31, 0.7, 12, k.w("#3b3e44"))
    out += _r(35.6, 34, 6.8, 9, k.lit("#27323d", "#5a4a1a"))
    out += _r(26, 29.5, 26, 1.8, k.w("#3b3e44"))
    out += _r(0, h - 5, w, 5, k.w("#8d8478"))
    out += _wreath(k, 39, 32.5, 1.8)
    return out + _decor(k, w, 4)


def _art_event_center(k: _K) -> str:
    """Village Hall: glass lobby with stone piers, canopy and a sign band."""
    w, h = 165, 52
    char, dark, stone = k.w("#3f4248"), k.w("#2f3237"), k.w("#b9ad98")
    out = _r(0, 14, w, h - 14, char) + _r(22, 3, 121, 13, dark)
    for x in range(11, w, 11):
        out += _r(x, 16, 0.5, h - 20, dark)
    for x in list(range(5, 48, 9)) + list(range(119, 160, 9)):
        out += _r(x, 22, 4.6, 9, k.win) + _r(x, 36, 4.6, 7, k.win)
    glass = k.lit("#a9d4ea", "#ffe08a")
    out += _r(55, 21, 55, 27, glass)
    for x in range(55, 111, 5):
        out += _r(x, 21, 0.5, 27, dark)
    out += _r(55, 31, 55, 0.6, dark)
    for x in (53.5, 66.5, 79.5, 92.5, 105.5, 108.5)[:5] + (107.5,):
        out += _r(x, 18, 4, 30, stone)
    out += _r(74, 36, 17, 12, k.lit("#22262b", "#4a3c12")) + _r(75.2, 37, 6.6, 11, glass)
    out += _r(83.2, 37, 6.6, 11, glass)
    out += _r(47, 16.5, 71, 3.6, k.w("#1d1f22"))
    # sign band
    out += _r(40, 5, 85, 9.5, k.lit("#1c1d20", "#0d0e10"))
    out += _t(87, 11.7, 5, "VILLAGE HALL", k.lit("#d9dce0", "#f4f4f4"), 68)
    out += _r(0, h - 4, w, 4, k.w("#5a5d63"))
    if k.snow:
        out += _r(0, 14, 22, 1.8, k.lit("#fff", "#c9d6ee")) + _r(143, 14, 22, 1.8, k.lit("#fff", "#c9d6ee"))
        out += _r(22, 3, 121, 1.8, k.lit("#fff", "#c9d6ee"))
    return out


def _art_library(k: _K) -> str:
    w, h = 50, 36
    out = _p([(0, 11), (w / 2, 1), (w, 11)], k.w("#d9d2c0"))
    out += _r(0, 10.5, w, 2.5, k.w("#bfb7a2")) + _r(2, 13, w - 4, h - 13, k.w("#e7e0cf"))
    out += _t(w / 2, 9.3, 4, "LIBRARY", k.lit("#5c5444", "#f4efe0"))
    for x in (5, 13, 34, 42):
        out += _r(x, 13, 3, h - 16, k.w("#f6f2e6"))
    for x in (8.6, 37.6):
        out += _r(x, 17, 3.8, 9, k.win)
    out += _r(20, 17, 10, h - 20, k.w("#6b4a2f")) + _r(21.2, 18.2, 7.6, 5, k.win)
    out += _r(0, h - 3, w, 3, k.w("#bfb7a2"))
    out += _wreath(k, 25, 25, 1.8)
    if k.snow:
        out += _ln([(1, 10.4), (w / 2, 1.4), (w - 1, 10.4)], k.lit("#fff", "#c9d6ee"), 1.6)
    return out


def _art_inn(k: _K) -> str:
    """Lakeside inn: blue clapboard, long porch, dormers."""
    w, h = 60, 36
    out = _p([(0, 12), (5, 3), (w - 5, 3), (w, 12)], k.w("#4d5562"))
    for x in (12, 27, 42):
        out += _p([(x, 7), (x + 3, 3.4), (x + 6, 7)], k.w("#3f4651")) + _r(x + 1.3, 6, 3.4, 4, k.win)
    out += _r(1.5, 12, w - 3, h - 12, k.w("#5d7fa3"))
    for x in range(5, w - 6, 8):
        out += _r(x, 14.5, 4.4, 5.6, k.win)
    out += _r(0, 21.5, w, 1.8, k.w("#f5f1e6"))
    for x in range(1, w, 8):
        out += _r(x, 23, 1.2, h - 26, k.w("#f5f1e6"))
    for x in range(5, w - 6, 8):
        if 24 < x < 32:
            continue
        out += _r(x, 24.5, 4.4, 5.5, k.win)
    out += _r(27, 24, 6, h - 27, k.w("#7a2f24"))
    out += _r(21, 12.3, 18, 4.6, k.w("#2c3e57")) + _t(30, 15.9, 3.4, "THE INN")
    out += _r(0, h - 3, w, 3, k.w("#b9ad98"))
    out += _wreath(k, 30, 27, 1.6)
    if k.snow:
        out += _r(5.5, 2.6, w - 11, 1.8, k.lit("#fff", "#c9d6ee"))
    if k.xmas:
        for i, x in enumerate(range(1, w, 3)):
            out += _c(x, 22.4, 0.8, ("#e63946", "#f4c430", "#2a9d8f")[i % 3])
    return out


def _art_church(k: _K) -> str:
    w, h = 40, 56
    out = _p([(4, 20), (10, 0), (16, 20)], k.w("#4d5562")) + _r(5.5, 18, 9, 16, k.w("#f1ede2"))
    out += _r(8.2, 22, 3.6, 5, k.win)
    out += _p([(0, 36), (20, 24), (40, 36)], k.w("#4d5562")) + _r(2, 35, 36, h - 35, k.w("#f1ede2"))
    for x in (6, 15, 29):
        out += _r(x, 40, 3.6, 8, k.win)
    out += _r(21, 44, 5.6, h - 44, k.w("#7a2f24"))
    out += _wreath(k, 23.8, 47, 1.6)
    if k.snow:
        out += _ln([(1, 35.4), (20, 24.6), (39, 35.4)], k.lit("#fff", "#c9d6ee"), 1.8)
    return out


def _art_post_office(k: _K) -> str:
    w, h = 38, 33
    out = _r(0, 5, w, h - 5, k.w("#b5533c")) + _r(0, 3, w, 3, k.w("#f1ede2"))
    out += _r(3, 8, w - 6, 6.5, "#1f3a5f") + _t(w / 2, 13.2, 4.2, "POST OFFICE", "#fff", w - 10)
    out += _r(4, 17.5, 8, 8, k.win) + _r(26, 17.5, 8, 8, k.win)
    out += _r(15.5, 17, 7, h - 20, k.w("#27405f"))
    out += _r(0, h - 3, w, 3, k.w("#8d8478"))
    out += _r(w - 3.5, 0, 0.6, 5, "#777") + _p([(w - 3, 0), (w + 0.8, 1), (w - 3, 2.2)], "#d62828")
    out += _wreath(k, 19, 20.5, 1.7)
    return out + _decor(k, w, 3)


def _tree(kind: str, variant: int = 0):
    def fn(k: _K) -> str:
        if kind == "pine":
            g = k.w("#2f6b3c")
            out = _r(5, 15, 2, 5, k.w("#5b3a29"))
            for i, (y, half) in enumerate(((0, 3.6), (4.5, 5), (9, 6))):
                out += _p([(6 - half, y + 7), (6, y), (6 + half, y + 7)], g)
                if k.snow:
                    out += _ln([(6 - half * 0.5, y + 3.4), (6, y + 0.6), (6 + half * 0.5, y + 3.4)], k.lit("#fff", "#c9d6ee"), 1.1)
            return out
        out = _r(6, 10, 2, 8, k.w("#5b3a29"))
        if k.snow:
            for a, b in (((7, 11), (3, 5)), ((7, 10), (11, 4)), ((7, 12), (7, 2)), ((5, 8), (2, 9)), ((9, 7.5), (12.5, 8.5))):
                out += _ln([a, b], k.w("#5b3a29"), 0.9)
            return out + _ln([(3.2, 4.6), (5, 6.6)], k.lit("#fff", "#c9d6ee"), 0.8)
        cols = {
            "fall": ("#d9622b", "#e3a52b", "#b8371f"),
            "spring": ("#8fce7a", "#f2b8cf", "#9ad08a"),
            "summer": ("#4f9a4a", "#5aa650", "#468f47"),
        }[k.season]
        c = k.w(cols[variant % 3])
        return out + _c(7, 7, 6, c) + _c(4, 9, 3.6, c) + _c(10.4, 9.4, 3.4, c)

    return fn


def _art_loco(k: _K) -> str:
    """Steam locomotive facing right. 85 x 50 design units, wheels on the bottom edge."""
    body, red, gold = "#2c333d", "#b3262d", "#f1c84b"
    out = _r(4, 8, 26, 30, red) + _r(2, 5, 30, 4, "#5a1216")
    out += _r(9, 13, 8, 9, k.win) + _r(20, 13, 6, 9, k.win)
    out += _r(30, 18, 42, 20, body) + _e(72, 28, 5, 10, body)
    out += _r(30, 24, 43, 1.6, gold) + _r(4, 30, 26, 1.6, gold)
    out += _r(60, 6, 6, 13, body) + _p([(57.5, 6), (68.5, 6), (66.5, 1), (59.5, 1)], "#1b2027")
    out += _e(46, 18, 4.5, 4.2, gold) + _r(38, 13, 3, 6, body)
    out += _p([(73, 38), (85, 45), (73, 45)], "#5a1216") + _r(0, 37, 76, 4, "#1b2027")
    for x in (12, 26):
        out += _c(x, 44, 6, "#1b2027") + _c(x, 44, 3.6, red) + _c(x, 44, 1.2, gold)
    for x in (44, 56, 67):
        out += _c(x, 45.5, 4.5, "#1b2027") + _c(x, 45.5, 2.5, red)
    out += _r(12, 43.3, 55, 1.4, "#9aa0aa")
    if k.night:
        out += _p([(76, 24), (85, 19), (85, 33), (76, 28)], "#fff3b0", " opacity='.55'")
    out += _c(75.5, 26, 2.2, k.lit("#ffe9a8", "#fffbe0"))
    if k.xmas:
        out += _c(72, 20, 3.4, "none", " stroke='#2f7d32' stroke-width='1.5'") + _c(72, 23.2, 1, "#d62828")
    return out


def _art_far_train(k: _K) -> str:
    c = k.w("#6f7785")
    out = ""
    for x in (0, 13, 26, 39):
        out += _r(x, 2, 11.5, 5, c)
        for wx in (1.5, 4.5, 7.5):
            out += _r(x + wx, 3, 2, 1.8, k.win)
    out += _p([(51.5, 2), (58, 2), (61, 7), (51.5, 7)], k.w("#8f3b34"))
    return out + _r(0, 7, 61, 1, k.w("#3d4148"))


def _art_pier(k: _K) -> str:
    """Plank pier, seen side on: deck, piles, three lamp posts."""
    plank, pile = k.w("#b98b5a"), k.w("#6b4a2f")
    out = _r(0, 13, 62, 2.2, plank)
    for x in range(0, 62, 4):
        out += _r(x, 13, 0.4, 2.2, pile)
    for x in (3, 20, 38, 56):
        out += _r(x, 15, 1.4, 5, pile)
    for x in (12, 30, 48):
        out += _r(x, 3, 0.8, 10, k.w("#2b2f36"))
        if k.night:
            out += _c(x + 0.4, 2.4, 4.2, "#ffe9a8", " opacity='.35'")
        out += _c(x + 0.4, 2.4, 1.6, k.lit("#f6f1c7", "#fff6b8"))
        if k.xmas:
            out += _r(x - 0.7, 6, 2.2, 1.4, "#d62828")
    if k.snow:
        out += _r(0, 12.4, 62, 0.9, k.lit("#fff", "#c9d6ee"))
    return out


def _art_pier_tree(k: _K) -> str:
    """The Christmas tree at the end of the pier (a plain fir out of season)."""
    g = k.w("#256b3a") if not k.xmas else "#1f7a3a"
    out = _r(6, 20, 2, 4, k.w("#5b3a29"))
    for y, half in ((1, 3), (6, 4.6), (11, 6.2)):
        out += _p([(7 - half, y + 9), (7, y), (7 + half, y + 9)], g)
    if k.xmas:
        cols = ("#ffd23f", "#ff4d4d", "#4aa3ff", "#ffffff")
        for i, (x, y) in enumerate(((7, 6), (5, 10), (9, 11), (4, 15), (7.5, 15.5), (10.5, 16), (3, 19), (7, 19.5), (11, 19))):
            out += _c(x, y, 0.9, cols[i % 4])
        out += _p([(7, -1), (7.9, 1.2), (10, 1.3), (8.3, 2.6), (9, 4.8), (7, 3.5), (5, 4.8), (5.7, 2.6), (4, 1.3), (6.1, 1.2)], "#ffd23f")
        if k.night:
            out += _c(7, 12, 10, "#ffe9a8", " opacity='.16'")
    elif k.snow:
        out += _ln([(5, 6), (7, 1.6), (9, 6)], k.lit("#fff", "#c9d6ee"), 1)
    return out


def _art_boat(k: _K) -> str:
    out = _p([(1, 9), (21, 9), (18, 13), (4, 13)], k.w("#8a4b2a"))
    out += _r(10.4, 0, 0.7, 9, k.w("#5b3a29"))
    out += _p([(11.3, 0.5), (18.5, 8), (11.3, 8)], k.w("#fdfaf0"))
    return out + _p([(10.2, 2), (4.5, 8), (10.2, 8)], k.w("#e9e2cf"))


def _art_billboard(k: _K) -> str:
    out = _r(14, 44, 2.4, 14, k.w("#5b3a29")) + _r(84, 44, 2.4, 14, k.w("#5b3a29"))
    out += _r(0, 0, 100, 46, k.w("#5b3a29"), " rx='2'")
    out += _r(2, 2, 96, 42, k.lit("#fff8e7", "#2a2f45"), " rx='1'")
    if k.night:
        for x in (20, 50, 80):
            out += _p([(x - 2, 0), (x + 2, 0), (x + 9, 12), (x - 9, 12)], "#fff3b0", " opacity='.18'")
    return out


def _art_stars(k: _K) -> str:
    rng = LCG(7)
    out = ""
    for _ in range(70):
        x, y = rng.uniform(0, 1000), rng.uniform(0, 120)
        out += _c(x, y, rng.choice((0.6, 0.8, 1.1)), "#ffffff", " opacity='%.1f'" % rng.uniform(0.4, 1))
    return out


# --------------------------------------------------------------------------------------------
# Static scene
# --------------------------------------------------------------------------------------------
def _full(sc: _Scene, name: str, fn) -> None:
    cls = sc.art(name, (W * 10, H * 10), fn)
    sc.out.append('<div class="tt-s tt-full %s">%s</div>' % (cls, sc.ov(cls)))


def _bld(sc, name, x, y, w, h, vb, fn, tip=None):
    """Place a building. ``h`` is the artwork height; it is drawn TALL times taller."""
    sc.sprite(name, x, y, w, h * TALL, vb, fn, tip=tip)


def _base_css(sc: _Scene) -> None:
    sc.rule(
        "base",
        ".tt-stage{container-type:inline-size;width:100%;position:relative;margin:0 0 .5rem}"
        ".tt-scene{position:relative;height:25cqw;overflow:hidden;overflow:clip;contain:layout paint;"
        "border-radius:.8cqw;"
        "font-family:Verdana,Arial,sans-serif;line-height:1.15;user-select:none}"
        ".tt-scene *{box-sizing:border-box}"
        ".tt-s{position:absolute;background-size:100% 100%;background-repeat:no-repeat}"
        ".tt-full{left:0;bottom:0;width:100cqw;height:25cqw;pointer-events:none}"
        ".tt-tip{position:absolute;left:50%;bottom:calc(100% + .3cqw);translate:-50% .4cqw;"
        "opacity:0;pointer-events:none;transition:opacity .18s,translate .18s;"
        "background:#1f2937;color:#fff;font-size:max(.78cqw,10px);font-weight:400;"
        "padding:.35cqw .65cqw;border-radius:.4cqw;white-space:nowrap;z-index:99;"
        "box-shadow:0 .15cqw .5cqw rgba(0,0,0,.3)}"
        ".tt-tip.tt-tr{left:auto;right:0;translate:0 .4cqw}"
        ".tt-tip.tt-tl{left:0;translate:0 .4cqw}"
        ".tt-h{cursor:help}"
        ".tt-h:hover>.tt-tip,.tt-h:focus>.tt-tip{opacity:1;translate:-50% 0}"
        ".tt-h:hover>.tt-tip.tt-tr,.tt-h:hover>.tt-tip.tt-tl{translate:0 0}",
    )
    sc.themed(
        "sky",
        ".tt-scene{background:%s}" % _SKY[sc.season],
        ".tt-scene{background:%s}" % _SKY_NIGHT,
    )
    if sc.mode == "auto":
        sc.rule("when", ".tt-night{display:none}")
        sc.dark["when"] = ".tt-night{display:block}.tt-day{display:none}"


def _clock_css(sc: _Scene) -> None:
    """Day-cycle layers: night art fading over day art, night/day sprite layers, golden-hour tint."""
    anim = lambda name: "" if sc.still else "animation:%s 86400s linear -%ds infinite;" % (name, sc.secs)
    sc.rule(
        "clock",
        ".tt-ov{position:absolute;left:0;top:0;width:100%%;height:100%%;background-size:100%% 100%%;"
        "background-repeat:no-repeat;pointer-events:none;%s}"
        ".tt-fx{position:absolute;left:0;bottom:0;width:100cqw;height:25cqw;pointer-events:none}"
        ".tt-nf{%s}.tt-df{%s}"
        ".tt-warm{background:#ffb070;mix-blend-mode:multiply;pointer-events:none;%s}"
        % (anim("tt-nite"), anim("tt-nite"), anim("tt-dayf"), anim("tt-warmk")),
    )
    if not sc.still:
        stops = lambda st, f: "".join("%s%%{opacity:%s}" % (_f(h / 24 * 100), _f(f(v))) for h, v in st)
        sc.rule(
            "clockk",
            "@keyframes tt-nite{%s}@keyframes tt-dayf{%s}@keyframes tt-warmk{%s}"
            % (stops(NIGHT_STOPS, lambda v: v), stops(NIGHT_STOPS, lambda v: 1 - v), stops(WARM_STOPS, lambda v: v)),
        )


def _orbs(sc: _Scene) -> None:
    """Sun and moon travel their arcs over 24 hours, starting wherever ``now`` puts them."""
    hour = sc.secs / 3600.0
    for name, rise in (("sun", SUN_RISE), ("moon", MOON_RISE)):
        size = 3.6 if name == "sun" else 3.2
        x, y, o = orbit(hour, rise)
        style = "left:0;bottom:10cqw;pointer-events:none;transform:translate(%scqw,-%scqw);opacity:%s;" % (_f(x), _f(y), _f(o))
        if not sc.still:
            steps = "".join(
                "%s%%{transform:translate(%scqw,-%scqw);opacity:%s}"
                % (_f(h / 24 * 100), _f(orbit(h, rise)[0]), _f(orbit(h, rise)[1]), _f(orbit(h, rise)[2]))
                for h in [i / 2 for i in range(49)]
            )
            sc.rule("orb" + name, "@keyframes tt-%s{%s}" % (name, steps))
            style += "animation:tt-%s 86400s linear -%ds infinite;" % (name, sc.secs)
        sc.put("tt-" + name, 0, 10, size, size, style=style)


def _sky(sc: _Scene) -> None:
    sc.rule(
        "orb",
        ".tt-sun{border-radius:50%;background:radial-gradient(#fff6c2,#ffd95a 60%,rgba(255,217,90,0) 72%)}"
        ".tt-moon{border-radius:50%;background:radial-gradient(circle at 38% 38%,#fffdf0,#e6e2c8 62%,"
        "rgba(230,226,200,0) 70%)}"
        ".tt-cloud{border-radius:1cqw;background:#fff;opacity:.85;box-shadow:1.6cqw .5cqw 0 -.2cqw #fff,"
        "-1.4cqw .45cqw 0 -.25cqw #fff}",
    )
    if sc.clock:
        _clock_css(sc)
        sc.raw('<div class="tt-s tt-full tt-ov" style="background:%s;opacity:%s"></div>' % (_SKY_NIGHT, _f(sc.dark_now)))
        _orbs(sc)
    else:
        sc.put("tt-sun", 44, 19.5, 3.6, 3.6, when="day")
        sc.put("tt-moon", 44, 19.5, 3.2, 3.2, when="night")
    if sc.shows("night"):
        cls = sc.art("stars", (1000, 250), _art_stars)
        sc.put(cls + " tt-full", 0, 0, 100, 25, when="night")
    if sc.shows("day"):
        sc.rule("cloudk", "@keyframes tt-cloud{to{transform:translateX(112cqw)}}")
        for x, y, w, dur, delay in ((-8, 21.6, 4.2, 170, -60), (-8, 18.4, 3.2, 230, -150)):
            sc.put(
                "tt-cloud",
                x,
                y,
                w,
                1.0,
                when="day",
                style=sc.anim("tt-cloud %ds linear %ds infinite" % (dur, delay))
                or "left:%scqw;" % _f(24 + w * 9),
            )


def _far(sc: _Scene) -> None:
    _full(sc, "far", _art_far)
    sc.rule(
        "fark",
        "@keyframes tt-far{0%{transform:translateX(-8cqw)}55%,100%{transform:translateX(74cqw)}}",
    )
    sc.sprite(
        "fartrain",
        0,
        9.4,
        6.1,
        0.8,
        (61, 8),
        _art_far_train,
        style=sc.anim("tt-far 160s linear -20s infinite") or "left:14cqw;",
    )
    _full(sc, "meadow", _art_meadow)


def _hill(sc: _Scene) -> None:
    _full(sc, "crest", _art_terrace("crest", CREST))
    for i, x in enumerate((88.5, 91, 94.2, 97)):
        sc.sprite("pine", x, _interp(CREST, x + 0.6) - 0.1, 1.3, 2.2, (12, 20), _tree("pine"))
    _bld(
        sc,
        "eventcenter",
        68.5,
        PLATEAU - 0.1,
        16.5,
        5.2,
        (165, 52),
        _art_event_center,
        tip="Village Hall: fairs and dances up on the green",
    )
    _full(sc, "back", _art_terrace("back", BACK))
    y = 10.85
    houses = (
        (67.9, 3.1, 3.0, "#e8d9b5", "#7a4b3a", "A quiet street. Wave at a neighbor."),
        (71.5, 3.3, 3.1, "#c9d7e3", "#4d5562", None),
        (86.2, 3.2, 3.0, "#e5c9b3", "#5d4037", None),
        (89.9, 3.3, 3.1, "#d8e2c4", "#6b4f3a", None),
        (96.6, 3.2, 2.9, "#e3d2c0", "#4d5562", None),
    )
    for i, (x, w, h, wall, roof, tip) in enumerate(houses):
        _bld(sc, "hb%d" % i, x, y, w, h, (32, 30), _house(wall, roof), tip)
    tip = "The Griswold house: 25,000 twinkle lights" if sc.xmas else "The house with the big yard"
    gris = _house("#f4f1ea", "#5d4037", vb=(46, 32), lights=True)
    _bld(sc, "griswold", 75.6, y, 4.6, 3.3, (46, 32), gris, tip)
    if sc.xmas:
        sc.sprite("treelot", 80.7, y, 4.9, 2.3, (48, 22), _art_tree_lot, tip="Tree lot: pick the biggest one")
    lot = () if sc.xmas else (81, 82.6, 84.3)
    for i, x in enumerate(lot + (93.6, 95)):
        sc.sprite("tree%d" % (i % 3), x, y, 1.4, 1.9, (14, 18), _tree("round", i))
    _full(sc, "mid", _art_terrace("mid", MID))
    y = 6.85
    _bld(sc, "postoffice", 63.8, y, 3.8, 3.3, (38, 33), _art_post_office, "Post Office")
    _bld(sc, "library", 68.0, y, 5.0, 3.6, (50, 36), _art_library, "Library: quiet, please")
    _bld(sc, "inn", 73.4, y, 6.0, 3.6, (60, 36), _art_inn, "The lakeside inn")
    homes = (
        (84.6, 3.3, 3.0, "#f0e2c2", "#6b4f3a"),
        (88.3, 3.4, 3.1, "#cfd9e6", "#4d5562"),
        (97.0, 3.0, 2.9, "#e9d3c0", "#7a4b3a"),
    )
    for i, (x, w, h, wall, roof) in enumerate(homes):
        _bld(sc, "hm%d" % i, x, y, w, h, (32, 30), _house(wall, roof))
    _bld(sc, "church", 92.3, y, 4.0, 5.2, (40, 56), _art_church, "Chapel on the hill")
    tip = "Keep the change, ya filthy animal" if sc.xmas else "The big brick house"
    _bld(sc, "brickhouse", 80.0, y, 4.2, 3.4, (42, 35), _art_brick_house, tip)


def _lakefront(sc: _Scene) -> None:
    _full(sc, "ground", _art_ground)
    lake_when = "day" if sc.winter else None  # winter nights swap in Lights on the Lake
    sc.sprite("lake", 32.5, 1.85, 14, 2.8, (140, 28), _art_lake, when=lake_when)
    sc.sprite("pier", 39.4, 2.55, 5.2, 2.0, (62, 20), _art_pier, when=lake_when)
    sc.sprite(
        "piertree",
        38.4,
        3.15,
        1.5,
        2.5,
        (14, 24),
        _art_pier_tree,
        when=lake_when,
        tip="The pier tree" if sc.xmas else "End of the pier",
    )
    if not sc.winter:
        sc.rule(
            "bobk",
            "@keyframes tt-bob{0%,100%{transform:rotate(-3deg)}50%{transform:rotate(3deg) translateY(-.08cqw)}}",
        )
        sc.sprite(
            "boat",
            34.6,
            3.3,
            2.0,
            1.25,
            (22, 13),
            _art_boat,
            style="transform-origin:50% 90%;" + sc.anim("tt-bob 6s ease-in-out infinite"),
        )
    for i, x in enumerate((30.6, 47.2)):
        sc.sprite("tree%d" % i, x, 4.0 + i * 0.2, 1.5, 2.0, (14, 18), _tree("round", i + 1))


def _street(sc: _Scene) -> None:
    _bld(sc, "station", 50.8, BASE, 5.4, 3.7, (54, 40), _art_station, "The station. All aboard!")
    gz = "The lakefront gazebo"
    if sc.fall:
        gz = "The gazebo, dressed for the harvest festival"
    elif sc.xmas:
        gz = "The gazebo: carols at 12 and 3"
    sc.sprite("gazebo", 46.9, STREET - 0.45, 3.4, 3.6, (34, 34), _art_gazebo, tip=gz)
    shops = (
        ("coffee", 61.6, 3.8, 4.2, _shop("COFFEE", "#d8c3a5", "#6f4e37", "#4b2e1e", awning="#2f6b4f"), "Coffee: say hi to a neighbor"),
        ("bank", 65.6, 4.2, 4.7, _shop("BAKERY", "#f2d9b0", "#c98a4b", "#6b3f2a", vb=(42, 47), awning="#c0392b"), "The bakery: warm bread from six"),
        ("books", 70.0, 3.6, 4.2, _shop("BOOKS", "#7a8f6a", "#3f5136", "#2f3d28", awning="#b3262d"), "The bookshop"),
        ("pets", 73.8, 3.4, 3.9, _shop("PETS", "#f0d58c", "#b8860b", "#1f6f8b", vb=(34, 40), awning="#1f6f8b"), "Pet shop: treats for good dogs"),
        ("choc", 77.4, 3.8, 4.4, _shop("CHOCOLATE", "#8b5a3c", "#4a2c1a", "#3a1f10", vb=(38, 46), awning="#d98fa5", sub="Looking for expert tasters"), "Chocolate shop: Looking for expert tasters"),
        ("skan", 87.5, 4.0, 4.3, _shop("GROCER", "#f3efe4", "#27405f", "#27405f", vb=(40, 46), awning="#2f6b4f"), "The grocer: fresh from the farms"),
    )
    for name, x, w, h, fn, tip in shops:
        vb = (w * 10, round(h * 10 + (4 if h >= 4.4 else 2)))
        _bld(sc, name, x, BASE, w, h, _shop_vb(fn, vb), fn, tip)
    theatre_tip = "The theatre: lights up after dark"
    _bld(sc, "theatre", 81.5, BASE, 5.8, 5.0, (58, 52), _art_theatre, theatre_tip)
    _bld(sc, "office", 92.2, BASE, 6.8, 4.8, (78, 48), _art_office, "The market hall")


def _shop_vb(fn, default):
    """The viewBox a _shop() art function was built with."""
    for cell in fn.__closure__ or ():
        v = cell.cell_contents
        if isinstance(v, tuple) and len(v) == 2 and all(isinstance(n, (int, float)) for n in v):
            return v
    return default


def _train(sc: _Scene, title: str) -> None:
    sc.rule(
        "train",
        ".tt-train{position:absolute;left:1.5cqw;bottom:1.5cqw;display:flex;align-items:flex-end;"
        "gap:.25cqw}"
        ".tt-car{min-width:12cqw;max-width:21cqw}"
        ".tt-car-b{height:3.3cqw;border-radius:.5cqw .5cqw .15cqw .15cqw;border-top:.32cqw solid #5a1216;"
        "background:linear-gradient(#c8323a 0 16%,#f1c84b 16% 21%,#b3262d 21% 80%,#f1c84b 80% 85%,"
        "#8f1d23 85%);display:flex;align-items:center;justify-content:center;padding:0 1.2cqw;"
        "color:#fff;font-weight:800;font-size:1.45cqw;white-space:nowrap;"
        "text-shadow:0 .08cqw 0 #5a1216}"
        ".tt-car-b span{overflow:hidden;text-overflow:ellipsis}"
        ".tt-car-w{height:.75cqw;background:"
        + ",".join(
            "radial-gradient(circle,#c9a227 0 18%,#1b2027 20% 62%,transparent 66%) "
            + pos
            + "/.75cqw .75cqw no-repeat"
            for pos in ("left 1cqw top", "left 2cqw top", "right 1cqw top", "right 2cqw top")
        )
        + "}"
        ".tt-loco{position:relative;flex:none;width:8.5cqw;height:5cqw;background-size:100% 100%}"
        ".tt-puff{position:absolute;left:5.9cqw;top:-.7cqw;width:1.1cqw;height:1.1cqw;border-radius:50%;"
        "background:#fff;opacity:0}"
        "@keyframes tt-puff{0%{opacity:0;transform:translate(0,0) scale(.4)}15%{opacity:.75}"
        "100%{opacity:0;transform:translate(-1.6cqw,-3cqw) scale(1.7)}}",
    )
    puffs = ""
    if not sc.still:
        puffs = "".join(
            '<i class="tt-puff" style="animation:tt-puff 4.2s ease-out %ss infinite"></i>' % d
            for d in ("0", "-2.1")
        )
    sc.raw(
        '<div class="tt-train"><div class="tt-car"><div class="tt-car-b"><span>%s</span></div>'
        '<div class="tt-car-w"></div></div><div class="tt-loco %s">%s</div></div>'
        % (html.escape(title), sc.art("loco", (85, 50), _art_loco), puffs)
    )


# --------------------------------------------------------------------------------------------
# Small figures and props
# --------------------------------------------------------------------------------------------
def _g(x, y, body, s=1.0):
    return "<g transform='translate(%g,%g) scale(%g)'>%s</g>" % (x, y, s, body)


def _fig(coat, hat=None, hatc="#222222", skin="#f0c8a0", legs="#2b2f36", extra=""):
    """A little person in a 10 x 16 box."""
    out = _r(3, 13, 1.6, 3, legs) + _r(5.4, 13, 1.6, 3, legs)
    out += _p([(2.4, 6), (7.6, 6), (8.6, 13.6), (1.4, 13.6)], coat)
    out += _c(5, 3.8, 2.1, skin)
    if hat == "top":
        out += _r(3.2, -0.4, 3.6, 2.6, hatc) + _r(2.2, 2, 5.6, 0.8, hatc)
    elif hat == "crown":
        out += _p([(3, 2.4), (3, 0), (4, 1.2), (5, -0.4), (6, 1.2), (7, 0), (7, 2.4)], hatc)
    elif hat == "cap":
        out += _p([(2.8, 2.6), (5, 0.2), (7.2, 2.6)], hatc) + _c(5, 0.3, 0.7, "#ffffff")
    elif hat == "bonnet":
        out += _e(5, 2.3, 2.7, 1.5, hatc)
    elif hat == "shako":
        out += _r(3.4, -1.4, 3.2, 3.6, hatc) + _r(4.6, -2.6, 0.8, 1.4, "#f1c84b")
    return out + extra


def _figs(k: _K, *specs) -> str:
    """Several figures side by side; each spec is (x, scale, fig-kwargs)."""
    out = ""
    for x, s, kw in specs:
        kw = dict(kw)
        kw["coat"] = k.w(kw["coat"]) if kw.pop("dim", True) else kw["coat"]
        out += _g(x, 16 - 16 * s, _fig(**kw), s)
    return out


_DICKENS = (
    ("scrooge", "Ebenezer Scrooge. Bah, humbug!", dict(coat="#2b2b33", hat="top", extra=_r(8.6, 7, 0.6, 9, "#4a2c1a"))),
    ("dickens", "Mr. Dickens, taking notes", dict(coat="#5a3d2b", hat="top", hatc="#3a2a20", extra=_p([(3.4, 5.4), (6.6, 5.4), (5, 8.2)], "#6b5a4a") + _r(0.6, 8.6, 2.6, 2, "#b3262d"))),
    ("cratchit", "Bob Cratchit and Tiny Tim", dict(coat="#6b7a4a", hat="cap", hatc="#4a4a4a", extra=_g(7.4, 8.4, _fig("#8a5a44", "cap", "#7a2f24"), 0.48))),
    ("marley", "Jacob Marley's ghost", dict(coat="#cfe0ea", skin="#e8f1f5", legs="#cfe0ea", dim=False, extra=_ln([(1.6, 10), (0.2, 12), (1.4, 14), (0, 16)], "#9aa0aa", 0.7))),
    ("fezziwig", "Old Fezziwig, ready to dance", dict(coat="#2f7d32", hat="top", hatc="#5a3d2b", extra=_r(2.8, 8.4, 4.4, 1, "#f1c84b"))),
    ("lamplighter", "The lamplighter", dict(coat="#3f4a5c", hat="cap", hatc="#2b2f36", extra=_r(8.8, 0.5, 0.5, 15.5, "#4a2c1a") + _c(9, 0.6, 1, "#ffd66b"))),
)


def _art_dickens(spec):
    def fn(k: _K) -> str:
        return _figs(k, (0, 1.0, spec))

    return fn


def _art_kings(k: _K) -> str:
    gift = "#f1c84b"
    return _figs(
        k,
        (0, 0.94, dict(coat="#6a2c91", hat="crown", hatc=gift, skin="#c98f62", extra=_r(7, 8, 2.2, 2.2, gift))),
        (11, 1.0, dict(coat="#b3262d", hat="crown", hatc=gift, skin="#8a5a3c", extra=_r(7, 8, 2.2, 2.2, "#7fd1e8"))),
        (22, 0.94, dict(coat="#1f6f8b", hat="crown", hatc=gift, extra=_r(7, 8, 2.2, 2.2, "#e9e2cf"))),
    )


def _art_stable(k: _K) -> str:
    out = _p([(0, 12), (15, 2), (30, 12)], k.w("#6b4a2f")) + _r(2.5, 12, 25, 10, k.w("#8a6a44"))
    out += _r(5, 13.5, 20, 8.5, k.lit("#3a2a1c", "#ffd98a"))
    out += _r(12, 18.2, 6, 2.2, "#d9b44a") + _c(15, 17.6, 1.2, "#fbe3c8")
    out += _g(6, 13.6, _fig("#5d7fa3", "bonnet", "#dfe9f2"), 0.5) + _g(19.4, 13.6, _fig("#7a5a3c"), 0.5)
    star = [(15, -3), (16.2, 0.4), (19.6, 0.4), (16.9, 2.4), (17.9, 5.8), (15, 3.8), (12.1, 5.8), (13.1, 2.4), (10.4, 0.4), (13.8, 0.4)]
    return out + _g(0, 2.5, _p(star, "#ffd23f"), 0.7) + _g(4.5, 0, "", 1)


def _art_snowman(k: _K) -> str:
    sn = k.lit("#ffffff", "#dfe7f5")
    out = _c(8, 16, 5.6, sn) + _c(8, 8.6, 4, sn) + _c(8, 3.4, 2.8, sn)
    out += _r(5.8, -1.6, 4.4, 2.8, "#222") + _r(4.6, 1, 6.8, 0.8, "#222")
    out += _c(7, 3, 0.4, "#222") + _c(9, 3, 0.4, "#222") + _p([(8, 3.6), (10.6, 4.2), (8, 4.6)], "#f08a24")
    out += _r(5.4, 5.6, 5.4, 1.2, "#d62828") + _r(9.4, 6.4, 1.2, 3, "#d62828")
    out += _ln([(4.2, 8.4), (0.6, 5.6)], "#5b3a29", 0.6) + _ln([(11.8, 8.4), (15.4, 5.6)], "#5b3a29", 0.6)
    for y in (8, 10.2, 14):
        out += _c(8, y, 0.5, "#222")
    return out


def _art_mailbox(k: _K) -> str:
    out = _r(4.6, 9, 1.8, 9, k.w("#5b3a29"))
    out += _p([(0.5, 9.5), (0.5, 4.5), (3, 1.5), (8, 1.5), (10.5, 4.5), (10.5, 9.5)], k.w("#c8323a"))
    out += _r(2, 5, 7, 1.2, "#fff8e7") + _r(10.5, 2.2, 0.7, 4.4, "#f1c84b") + _r(10.5, 2.2, 2.4, 1.6, "#f1c84b")
    return out + (_r(0.5, 1.2, 10, 1.3, k.lit("#fff", "#c9d6ee")) if k.snow else "")


def _art_presents(n: int):
    def fn(k: _K) -> str:
        cols = ("#d62828", "#2a9d8f", "#3a86ff", "#f4a261", "#8e44ad", "#2f7d32")
        out, i, row, per_row = "", 0, 0, 8
        while i < n:
            count = min(per_row - row, n - i)
            x0 = (24 - count * 3) / 2
            for j in range(count):
                x, y = x0 + j * 3, 15 - (row + 1) * 2.8
                out += _r(x, y, 2.7, 2.6, cols[(i + row) % 6]) + _r(x + 1.1, y, 0.5, 2.6, "#fff8e7")
                i += 1
            row += 1
        return out

    return fn


def _stand(sign, roof, stripe, goods):
    """A market stand: striped canopy, counter, a sign."""

    def fn(k: _K) -> str:
        out = _r(1, 5, 0.8, 13, k.w("#5b3a29")) + _r(18.2, 5, 0.8, 13, k.w("#5b3a29"))
        out += _p([(0, 6), (2, 1), (18, 1), (20, 6)], k.w(roof))
        for x in (4, 10, 16):
            out += _p([(x - 1.2, 1), (x + 1.2, 1), (x + 1.6, 6), (x - 1.6, 6)], k.w(stripe))
        out += _r(1, 11.5, 18, 6.5, k.w("#9b6b43")) + _r(3, 12.6, 14, 3, "#fff8e7")
        out += _t(10, 15, 2.5, sign, "#5a2d0c", 12)
        for i, x in enumerate(range(3, 18, 3)):
            out += _c(x, 10.6, 1.1, goods[i % len(goods)])
        if k.snow:
            out += _r(1.6, 0.4, 16.8, 1.2, k.lit("#fff", "#c9d6ee"))
        return out

    return fn


def _art_corn_maze(k: _K) -> str:
    out = _r(0, 4, 100, 18, k.w("#c9a437")) + _r(0, 4, 100, 2, k.w("#8aa03a"))
    path = k.w("#8a6a3c")
    for pts in (
        [(4, 22), (4, 12), (22, 12), (22, 17), (40, 17), (40, 9), (58, 9)],
        [(58, 9), (58, 18), (76, 18), (76, 11), (92, 11), (92, 22)],
        [(30, 12), (30, 8)],
        [(66, 18), (66, 13)],
        [(12, 12), (12, 17)],
    ):
        out += _ln(pts, path, 1.8)
    for x in range(1, 100, 3):
        out += _ln([(x, 4.6), (x, 1.2 + (x % 2))], k.w("#7f9a2e"), 0.9) + _c(x, 2.6 + (x % 2), 0.7, k.w("#e3b23c"))
    out += _r(38, 13.5, 24, 5, "#fff8e7") + _t(50, 17.3, 3.6, "CORN MAZE", "#5a2d0c", 20)
    return out


def _art_hay_maze(k: _K) -> str:
    hay, edge = k.w("#e3c462"), k.w("#b8963a")
    out = ""
    for x, y in ((0, 10), (9, 10), (18, 10), (36, 10), (45, 10), (54, 10), (4.5, 5), (13.5, 5), (40.5, 5), (49.5, 5), (27, 10), (9, 0), (45, 0)):
        out += _r(x, y, 8.4, 4.6, hay, f" stroke='{edge}' stroke-width='.6'")
        out += _ln([(x + 2.8, y), (x + 2.8, y + 4.6)], edge, 0.4) + _ln([(x + 5.6, y), (x + 5.6, y + 4.6)], edge, 0.4)
    out += _g(26.5, 0.6, _fig(k.w("#b3262d"), "cap", "#27405f"), 0.56)
    return out


def _art_market(k: _K) -> str:
    out = ""
    for i, (x, roof) in enumerate(((0, "#b3262d"), (26, "#e07a1f"), (52, "#2f6b4f"))):
        out += _g(x, 0, _stand(("PIES", "MARKET", "HONEY")[i], roof, "#fff8e7", ("#e8761c", "#d9b44a", "#b3262d"))(k), 1.15)
    for x in (23.5, 49.5, 76):
        out += _c(x, 19.6, 1.6, "#e8761c") + _r(x - 0.3, 17.4, 0.6, 1, "#4f6b2a")
    return out


def _art_scarecrow(k: _K) -> str:
    out = _r(4.6, 6, 0.8, 12, k.w("#5b3a29")) + _r(0, 7.2, 10, 0.8, k.w("#5b3a29"))
    out += _p([(2.4, 7), (7.6, 7), (8.2, 13), (1.8, 13)], k.w("#3a6ea5"))
    out += _c(5, 4.4, 2.2, k.w("#e8c98a")) + _p([(2, 3), (5, -0.6), (8, 3)], k.w("#8a5a2b"))
    return out + _ln([(0, 7.6), (-0.6, 9)], "#d9b44a", 0.6) + _ln([(10, 7.6), (10.6, 9)], "#d9b44a", 0.6)


def _art_chestnut(k: _K) -> str:
    out = _stand("CHESTNUTS", "#6b3f2a", "#e3b23c", ("#5a2d0c", "#7a4b2a"))(k)
    out += _r(7.5, 7.8, 5, 2.8, "#33363b") + _r(8.2, 7, 3.6, 1, k.lit("#e0661f", "#ff8a3c"))
    return out


def _art_peanuts(k: _K) -> str:
    """Charlie Brown's little tree, Snoopy on his decorated doghouse, Linus and his blanket."""
    out = _ln([(8, 21), (8.6, 11), (10.5, 6)], k.w("#5b3a29"), 0.8)
    for a, b in (((8.4, 15), (4.8, 13.4)), ((8.5, 12.6), (12, 11)), ((8.9, 9.6), (6.2, 8))):
        out += _ln([a, b], k.w("#2f7d32"), 1)
    out += _c(10.8, 8.2, 1.3, "#d62828") + _r(5.6, 20, 5, 1.2, k.w("#8a5a44"))
    out += _g(15, 6.6, _fig(k.w("#f1c84b"), skin="#f0c8a0", extra=_ln([(2.6, 9.2), (4, 10.6), (6, 9.2), (7.4, 10.6)], "#222", 0.7)), 0.9)
    out += _g(26, 7.4, _fig(k.w("#c8323a"), extra=_p([(8, 8), (11.6, 9), (12.6, 16), (8.6, 14)], "#7fb7e6")), 0.85)
    out += _p([(38, 14), (45.5, 6.5), (53, 14)], k.w("#c8323a")) + _r(39.5, 14, 12, 8, k.w("#d9433f"))
    out += _p([(43.5, 22), (43.5, 17.5), (45.5, 15.8), (47.5, 17.5), (47.5, 22)], "#2b2b2b")
    out += _e(45, 5, 4.6, 1.6, "#ffffff") + _c(49.6, 4.2, 1.7, "#ffffff") + _e(50.6, 5.2, 0.6, 1.3, "#222")
    if k.xmas:
        cols = ("#ffd23f", "#35d07f", "#4aa3ff", "#ff7bd0")
        for i in range(7):
            out += _c(39 + i * 2.2, 13.6 - (3.3 - abs(i - 3)) * 2.2 + 1.2, 0.8, cols[i % 4])
    return out


def _art_tree_lot(k: _K) -> str:
    out = _r(1, 2, 0.7, 20, k.w("#5b3a29")) + _r(46.3, 2, 0.7, 20, k.w("#5b3a29"))
    out += _ln([(1.3, 3), (24, 6.5), (46.6, 3)], "#3d4148", 0.4)
    cols = ("#ffd23f", "#ff4d4d", "#4aa3ff", "#35d07f")
    for i in range(11):
        t = (i + 0.5) / 11
        out += _c(1.3 + 45.3 * t, 3 + 3.5 * (1 - abs(2 * t - 1) ** 2) + 0.8, 0.9, cols[i % 4])
    for i, x in enumerate((4, 11.5, 19, 27, 34.5, 41)):
        top = 9 + (i % 2) * 2
        out += _p([(x - 3, 22), (x, top), (x + 3, 22)], k.w("#256b3a")) + _p([(x - 2, top + 6.5), (x, top - 2), (x + 2, top + 6.5)], k.w("#2f7d3f"))
    out += _r(16, 0, 16, 4.6, "#fff8e7") + _t(24, 3.6, 3.2, "TREES $25", "#b3262d", 13)
    return out


def _art_brick_house(k: _K) -> str:
    """The big brick Georgian from the Home Alone street: a wreath in every window."""
    w, h = 42, 35
    out = _r(7, 1, 3.4, 8, k.w("#7a3b2a")) + _r(31.6, 1, 3.4, 8, k.w("#7a3b2a"))
    out += _p([(-1, 11), (4, 3), (38, 3), (43, 11)], k.w("#3f4651")) + _r(1, 11, 40, h - 11, k.w("#a04a35"))
    out += _p([(15, 11), (21, 5.5), (27, 11)], k.w("#f1ede2"))
    for y in (14, 24):
        for x in (4, 12, 26, 34):
            out += _r(x, y, 4.4, 6, k.win)
            out += _wreath(k, x + 2.2, y + 2.6, 1.3)
    out += _r(18.4, 14, 5.2, 6, k.win) + _r(18.2, 24, 5.6, h - 24, k.w("#f1ede2")) + _r(19, 25, 4, h - 25, k.w("#2f4a36"))
    out += _wreath(k, 21, 28, 1.5)
    if k.snow:
        out += _r(4.5, 2.6, 33, 1.6, k.lit("#fff", "#c9d6ee"))
    if k.xmas:
        for i, x in enumerate(range(1, 42, 3)):
            out += _c(x, 12, 0.8, ("#ffffff", "#ffd23f")[i % 2])
    return out


def _art_grinch(k: _K) -> str:
    """Left to right: the Grinch, his sleigh under a big bag of presents, and Max out front."""
    green = "#7ab317"
    out = _g(0, 6.2, _fig("#c8323a", "cap", "#c8323a", skin=green, legs=green), 0.62)
    out += _ln([(9, 15.4), (30, 15.4), (32.5, 13.4)], "#8a4b2a", 0.9) + _r(11, 12.6, 16, 2.2, "#b3262d")
    out += _e(19, 7.6, 8.6, 6.4, "#d9c9a3") + _p([(16, 2.4), (19, 0), (22, 2.4)], "#d9c9a3")
    for x, y, c in ((14, 9, "#d62828"), (19.5, 5.4, "#2a9d8f"), (23, 9.6, "#3a86ff")):
        out += _r(x, y, 2.6, 2.4, c) + _r(x + 1, y, 0.5, 2.4, "#fff8e7")
    out += _ln([(32.5, 13.4), (37, 13)], "#5b3a29", 0.4)
    out += _e(39.4, 13, 2.6, 1.4, "#a9794a") + _c(42.2, 11.6, 1.3, "#a9794a") + _e(41.6, 11.4, 0.5, 1.2, "#6b4a2f")
    out += _r(37.6, 14, 0.6, 2, "#a9794a") + _r(40.8, 14, 0.6, 2, "#a9794a")
    out += _ln([(42.4, 10.4), (42, 8), (43.4, 8.6)], "#8a5a2b", 0.4) + _c(43.5, 11.9, 0.35, "#d62828")
    return out


def _art_heart(k: _K) -> str:
    return "<path d='M5 9 C-3 3 1.5 -1.5 5 2.6 C8.5 -1.5 13 3 5 9Z' fill='#e63946'/>"


def _art_sleigh(k: _K) -> str:
    """Santa's sleigh and two reindeer, flying right."""
    out = _p([(0, 6), (3, 13), (15, 13), (16.5, 8), (12, 8), (11, 4), (3, 4)], "#b3262d")
    out += _ln([(1, 15.4), (16, 15.4), (18, 13.6)], "#f1c84b", 0.7)
    out += _g(4, 0.4, _fig("#c8323a", "cap", "#c8323a", extra=_e(5, 6.2, 2, 1.6, "#ffffff")), 0.6)
    out += _e(2.6, 4.6, 2.6, 2.8, "#3a6b35")
    for x in (22, 32):
        out += _e(x + 3, 9.4, 3.4, 1.7, "#8a5a2b") + _c(x + 7, 7.2, 1.4, "#8a5a2b")
        out += _ln([(x + 7, 6), (x + 6.4, 3.6)], "#5b3a29", 0.4) + _ln([(x + 7.4, 6), (x + 8.4, 3.8)], "#5b3a29", 0.4)
        for lx in (0.6, 2, 4, 5.4):
            out += _r(x + lx, 10.4, 0.5, 3, "#8a5a2b")
    out += _c(40.6, 7.4, 0.6, "#ff3b3b") + _ln([(16.5, 9), (33, 9)], "#5b3a29", 0.3)
    return out


def _art_ufo(k: _K) -> str:
    out = _p([(8, 9), (22, 9), (30, 30), (0, 30)], "#b8ffb0", " opacity='.22'")
    out += _e(15, 4.4, 5.4, 4, "#bfe9ff") + _e(15, 7, 13, 3, "#8f98a8") + _e(15, 6.4, 13, 2.2, "#c3cad6")
    for i, x in enumerate((5, 10, 15, 20, 25)):
        out += _c(x, 7.4, 0.9, ("#ffd23f", "#ff4d4d", "#35d07f")[i % 3])
    return out


def _art_balloon(k: _K) -> str:
    out = ""
    for i, c in enumerate(("#e63946", "#f4c430", "#3a86ff", "#f4c430", "#e63946")):
        out += _e(10, 9, 9 - i * 2.1, 9, c)
    out += _ln([(4, 15.6), (8, 22)], "#5b3a29", 0.3) + _ln([(16, 15.6), (12, 22)], "#5b3a29", 0.3)
    return out + _r(8, 22, 4, 3, "#8a5a2b")


def _art_birds(k: _K) -> str:
    out = ""
    for x, y in ((2, 6), (9, 3), (16, 7), (23, 2), (29, 6)):
        out += _ln([(x, y), (x + 1.6, y - 1.4), (x + 3.2, y), (x + 4.8, y - 1.4), (x + 6.4, y)], k.lit("#333b47", "#aab4c6"), 0.6)
    return out


def _art_kite(k: _K) -> str:
    out = _p([(6, 0), (11, 6), (6, 14), (1, 6)], "#e63946") + _p([(6, 0), (11, 6), (6, 6)], "#f4c430")
    return out + _ln([(6, 14), (4.6, 17), (7, 19.6), (5, 22), (6.6, 25)], "#555", 0.35)


def _art_wagon(k: _K) -> str:
    out = _r(0, 6, 20, 5, "#8a4b2a") + _r(1, 2.4, 18, 4, "#e3c462") + _c(4, 12, 2.4, "#3d2a1c") + _c(16, 12, 2.4, "#3d2a1c")
    for x in (3, 8, 13):
        out += _g(x, -1.6, _fig(("#b3262d", "#27405f", "#2f7d32")[x % 3]), 0.46)
    out += _ln([(20, 9), (24, 9)], "#5b3a29", 0.5)
    out += _e(29, 7.6, 4.4, 2.6, "#7a5a3c") + _p([(32, 6.6), (35.4, 3), (36.6, 5.6), (33.4, 8.4)], "#7a5a3c")
    for x in (25.6, 27.4, 30.4, 32.2):
        out += _r(x, 9, 0.7, 5, "#7a5a3c")
    return out


def _art_crows(k: _K) -> str:
    out = ""
    for x, y in ((2, 4), (10, 1), (17, 5)):
        out += _e(x + 2, y + 1.6, 2, 1.1, "#1d1f22") + _p([(x + 3.6, y + 1.2), (x + 5.2, y + 1.6), (x + 3.6, y + 2)], "#e0a020")
        out += _p([(x + 0.6, y + 1.2), (x + 2, y - 1), (x + 3, y + 1.2)], "#1d1f22")
    return out


def _art_soldiers(k: _K) -> str:
    specs = []
    for i in range(5):
        specs.append((i * 9, 1.0, dict(coat="#c8323a", hat="shako", hatc="#1d1f22", legs="#27405f", dim=False, extra=_ln([(2.4, 6.4), (7.6, 13)], "#ffffff", 0.7) + _r(8.4, 2, 0.5, 9, "#5b3a29"))))
    return _g(0, 2.6, _figs(k, *specs), 1)


def _art_bandits(k: _K) -> str:
    return _figs(
        k,
        (0, 1.0, dict(coat="#5a4a3a", hat="cap", hatc="#2b2b2b", dim=False, extra=_r(-1.6, 9, 3.6, 3.6, "#d9c9a3"))),
        (10, 0.74, dict(coat="#3a3a44", hat="cap", hatc="#1d1f22", dim=False, extra=_r(8, 8, 2, 4, "#9aa0aa"))),
    )


def _art_snowkids(k: _K) -> str:
    out = _figs(
        k,
        (0, 0.8, dict(coat="#3a86ff", hat="cap", hatc="#d62828", dim=False)),
        (8, 0.7, dict(coat="#8e44ad", hat="cap", hatc="#f4c430", dim=False)),
        (50, 0.8, dict(coat="#2a9d8f", hat="cap", hatc="#e63946", dim=False)),
        (59, 0.72, dict(coat="#f4a261", hat="cap", hatc="#27405f", dim=False)),
    )
    snow = k.lit("#ffffff", "#dfe7f5")
    return out + _p([(14, 16), (15, 10), (22, 10), (23, 16)], snow) + _p([(40, 16), (41, 10.6), (48, 10.6), (49, 16)], snow)


def _art_rink(k: _K) -> str:
    """Hockey goals and a centre line for the frozen lake."""
    out = _ln([(70, 4), (70, 24)], "#d62828", 0.5) + _c(70, 14, 3, "none", " stroke='#3a86ff' stroke-width='.4'")
    for x, s in ((14, 1), (126, -1)):
        out += _ln([(x, 10), (x - 3 * s, 10), (x - 3 * s, 16), (x, 16)], "#d62828", 0.7)
    return out


def _art_car(color):
    def fn(k: _K) -> str:
        out = _p([(0, 8), (0, 5), (3, 4.6), (5, 1.6), (11, 1.6), (13.4, 4.6), (17, 5.2), (17, 8)], color)
        out += _p([(5.6, 2.4), (10.6, 2.4), (12.2, 4.6), (4.4, 4.6)], "#ffe9a8") + _c(4, 8.4, 1.7, "#111") + _c(13, 8.4, 1.7, "#111")
        return out + _p([(17, 5.6), (24, 3.6), (24, 9.4), (17, 7)], "#fff3b0", " opacity='.5'") + _c(16.8, 6.2, 0.7, "#ffffff")

    return fn


def _art_lights_lake(k: _K) -> str:
    """Lights on the Lake: a drive-through show laid out where the lake sits."""
    cols = ("#ff4d4d", "#ffd23f", "#35d07f", "#4aa3ff", "#ff7bd0", "#ffffff")
    out = _e(72, 30, 72, 16, "#0c1430") + _r(0, 33, 144, 5.5, "#1a1d24")
    for x in range(2, 144, 7):
        out += _r(x, 35.4, 3.4, 0.5, "#e9e2cf")
    # entrance arch with the name
    out += _ln([(3, 33), (3, 12), (40, 12), (40, 33)], "#ffd23f", 0.8)
    out += _t(21.5, 9.6, 4.2, "LIGHTS ON THE LAKE", "#fff6b8", 40)
    for i in range(13):
        out += _c(4.5 + i * 2.9, 13.6, 0.8, cols[i % 6])
    # tunnel of arches
    for i, x in enumerate(range(46, 100, 9)):
        c = cols[i % 5]
        out += f"<path d='M{x} 33 V22 a6 6 0 0 1 12 0 V33' fill='none' stroke='{c}' stroke-width='.9' stroke-dasharray='1.1 1'/>"
    # light trees
    for i, (x, hgt) in enumerate(((104, 16), (113, 21), (122, 15), (131, 19), (139, 13))):
        for j in range(6):
            t = j / 5
            out += _ln([(x, 32 - hgt), (x - 4 + 8 * t, 32)], cols[(i + 2) % 5], 0.35)
        out += _g(x - 2.4, 28.4 - hgt, _p([(2.4, 0), (3, 1.8), (4.8, 1.8), (3.4, 2.9), (3.9, 4.6), (2.4, 3.6), (0.9, 4.6), (1.4, 2.9), (0, 1.8), (1.8, 1.8)], "#ffd23f"), 1)
    # snowflakes, a reindeer, candy canes behind the road
    for x, y, r in ((50, 12, 3), (66, 7, 3.6), (84, 11, 3), (97, 6, 2.6), (30, 4, 2.2)):
        for a in range(0, 180, 45):
            out += f"<line x1='{x - r}' y1='{y}' x2='{x + r}' y2='{y}' stroke='#bfe9ff' stroke-width='.5' transform='rotate({a} {x} {y})'/>"
    out += _ln([(8, 31), (8, 25), (15, 25), (15, 31)], "#ffffff", 0.6) + _ln([(15, 25), (17.6, 21), (20, 21.6)], "#ffffff", 0.6)
    out += _ln([(17.6, 21), (16.6, 17.6), (18.6, 18.6)], "#ffd23f", 0.5) + _c(20.2, 21.8, 0.7, "#ff3b3b")
    for x in (25, 30, 35):
        out += f"<path d='M{x} 32 V23 a2 2 0 0 1 4 0' fill='none' stroke='#ff4d4d' stroke-width='.9' stroke-dasharray='1.4 1.2'/>"
        out += f"<path d='M{x} 32 V23 a2 2 0 0 1 4 0' fill='none' stroke='#ffffff' stroke-width='.9' stroke-dasharray='1.2 1.4' stroke-dashoffset='1.3'/>"
    for i in range(24):
        out += _c(3 + i * 6, 40.6 + (i % 2) * 1.6, 0.8, cols[i % 6])
    return out


def _art_twinkle(k: _K) -> str:
    rng = LCG(11)
    out = ""
    for _ in range(46):
        out += _c(rng.uniform(4, 140), rng.uniform(3, 32), rng.choice((0.5, 0.7, 0.9)), rng.choice(("#ffffff", "#fff6b8", "#bfe9ff")))
    return out


def _art_marquee(k: _K) -> str:
    out = ""
    for x in range(4, 58, 8):
        out += _c(x, 35.6, 1.5, "#ffffff") + _c(x, 35.6, 3, "#fff27a", " opacity='.35'")
    return out + _r(24.5, 0, 9, 25, "none", " stroke='#ff7b7b' stroke-width='1.2' opacity='.8'")


def _art_snow(seed: int, n: int):
    def fn(k: _K) -> str:
        rng = LCG(seed)
        return "".join(_c(rng.uniform(0, 250), rng.uniform(0, 250), rng.choice((0.9, 1.2, 1.6)), "#ffffff", " opacity='.9'") for _ in range(n))

    return fn


def _art_leaves(k: _K) -> str:
    rng = LCG(5)
    out = ""
    for _ in range(16):
        x, y = rng.uniform(4, 246), rng.uniform(4, 246)
        out += "<ellipse cx='%g' cy='%g' rx='2.6' ry='1.3' fill='%s' transform='rotate(%d %g %g)'/>" % (x, y, rng.choice(("#d9622b", "#e3a52b", "#b8371f")), rng.randint(0, 180), x, y)
    return out


def _art_pumpkins(k: _K) -> str:
    out = ""
    for x, r in ((4, 3.2), (11, 2.4), (17, 3.6)):
        out += _e(x, 8 - r * 0.2, r, r * 0.8, k.w("#e8761c")) + _e(x, 8 - r * 0.2, r * 0.45, r * 0.8, k.w("#f08a24")) + _r(x - 0.4, 8 - r - 0.8, 0.8, 1.4, "#4f6b2a")
    return out


# --------------------------------------------------------------------------------------------
# Feature groups
# --------------------------------------------------------------------------------------------
_COATS = ("#c8323a", "#27405f", "#2f7d32", "#8e44ad", "#e07a1f", "#1f6f8b", "#5a4a3a", "#d98fa5", "#3b3e44")
MAX_WALKERS = 9  # street + hillside + Dickens roamers on screen in one load


def _person_css(sc: _Scene) -> None:
    sc.rule(
        "person",
        ".tt-p{position:absolute;width:.32cqw;height:.82cqw;border-radius:.16cqw .16cqw .04cqw .04cqw;"
        "background:linear-gradient(#f0c8a0 0 27%,var(--c,#27405f) 27% 72%,#2b2f36 72%)}"
        ".tt-w{animation:var(--k) var(--p) linear var(--d) infinite both}"
        "@keyframes tt-wA{0%{opacity:0;transform:translateX(0)}6%,54%{opacity:1}"
        "60%,100%{opacity:0;transform:translateX(var(--dx))}}"
        "@keyframes tt-wB{0%{opacity:0;transform:translateX(0)}5%{opacity:1}"
        "40%,48%{transform:translateX(var(--dx))}85%{opacity:1}90%,100%{opacity:0;transform:translateX(0)}}"
        "@keyframes tt-wC{0%,30%{opacity:0;transform:translateX(0)}36%{opacity:1}"
        "55%,65%{transform:translateX(calc(var(--dx)*.5))}90%{opacity:1}"
        "96%,100%{opacity:0;transform:translateX(var(--dx))}}",
    )


def _walk_style(rng: LCG, dx: float) -> str:
    period = rng.randint(46, 110)
    return "--k:tt-w%s;--p:%ds;--d:-%ds;--dx:%scqw;" % (rng.choice("ABC"), period, rng.randint(0, period), _f(dx))


def cast_walkers(rng: LCG) -> list:
    """Chance street and hillside walkers for this page load: (x, y, dx) triples."""
    out = []
    for _ in range(8):
        x, dx = rng.uniform(48, 97), rng.uniform(6, 22) * rng.choice((-1, 1))
        if rng.chance(P_STREET):
            out.append((x, STREET, max(47 - x, min(99 - x, dx))))
    for i in range(6):
        ledge, lo = ((7.0, 64.5), (11.0, 68.0))[i % 2]
        x, dx = rng.uniform(lo, 97), rng.uniform(5, 16) * rng.choice((-1, 1))
        if rng.chance(P_HILL):
            out.append((x, ledge, max(lo - x, min(99 - x, dx))))
    return out


def _walkers(sc: _Scene, rng: LCG, budget: int) -> int:
    cast = cast_walkers(rng)[: max(0, budget)]
    if sc.still:
        return 0
    _person_css(sc)
    for x, y, dx in cast:
        style = "--c:%s;%s" % (rng.choice(_COATS), _walk_style(rng, dx))
        sc.put("tt-p tt-w tt-x", x, y, 0.32, 0.82, style=style)
    return len(cast)


def staff_route(rng: LCG) -> list:
    """One staff member's way from the office door to the Event Center door, on the ground."""
    door = 95.7 + rng.uniform(-0.5, 0.5)
    turn = rng.uniform(55.6, 56.4)  # the cleared gap beside the station
    hall = 76.75 + rng.uniform(-0.9, 0.9)
    return [(door, STREET), (turn, STREET)] + climb_route(turn, hall)[1:]


def staff_keyframes(name: str, route: list, start: float, arrive: float) -> str:
    """@keyframes that walk ``route`` between ``start``% and ``arrive``%, hidden at both doors."""
    seg = [((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 for (x0, y0), (x1, y1) in zip(route, route[1:])]
    total = sum(seg)

    def tf(p):
        return "transform:translate(%scqw,-%scqw)" % (_f(p[0]), _f(p[1]))

    out = "0%%,%s%%{opacity:0;%s}%s%%{opacity:1}" % (_f(start), tf(route[0]), _f(start + 1.5))
    done = 0.0
    for p, d in zip(route[1:-1], seg):
        done += d
        out += "%s%%{%s}" % (_f(start + (arrive - start) * done / total), tf(p))
    out += "%s%%{opacity:1}%s%%,100%%{opacity:0;%s}" % (_f(arrive - 1.5), _f(arrive), tf(route[-1]))
    return "@keyframes %s{%s}" % (name, out)


def is_staff_day(day: dt.date) -> bool:
    return day.weekday() in STAFF_DAYS


def _staff(sc: _Scene, rng: LCG, day: dt.date) -> int:
    """Monday and Wednesday: the team walks to the Event Center and, after a while, back."""
    if not is_staff_day(day):
        return 0
    n = rng.randint(STAFF_MIN, STAFF_MAX)
    _person_css(sc)
    sc.rule("staff", ".tt-st{left:0;bottom:0;opacity:0}")
    for i in range(n):
        route = staff_route(rng)
        coat = rng.choice(_COATS)
        start = rng.uniform(2, 30)
        arrive = start + rng.uniform(42, 55)
        if sc.still:
            x, y = rng.choice(route[1:])
            style = "--c:%s;opacity:1;transform:translate(%scqw,-%scqw);" % (coat, _f(x), _f(y))
        else:
            sc.rule("st%d" % i, staff_keyframes("tt-st%d" % i, route, start, arrive))
            style = "--c:%s;animation:tt-st%d 260s linear infinite alternate both;" % (coat, i)
        sc.raw('<i class="tt-p tt-st" style="%s"></i>' % style)
    return n


def _billboard(sc: _Scene, slides) -> None:
    slides = list(slides or DEFAULT_SLIDES)
    n = len(slides)
    share = 100.0 / n
    sc.rule(
        "bb",
        ".tt-bb-s{position:absolute;left:5%;right:5%;top:5%;height:70%;display:flex;flex-direction:column;"
        "justify-content:center;align-items:center;text-align:center;gap:.25cqw;opacity:0}"
        ".tt-bb-s b{font-size:1cqw;font-weight:800}.tt-bb-s span{font-size:.66cqw}",
    )
    sc.themed("bbc", ".tt-bb-s{color:#3b2a1a}", ".tt-bb-s{color:#f4efe0}")
    sc.rule(
        "bbk%d" % n,
        "@keyframes tt-bb%d{0%%{opacity:0}%s%%,%s%%{opacity:1}%s%%,100%%{opacity:0}}"
        % (n, _f(share * 0.1), _f(share * 0.9), _f(share)),
    )
    inner = ""
    for i, (head, text) in enumerate(slides):
        if sc.still:
            style = "opacity:1;" if i == 0 else ""
        else:
            style = "animation:tt-bb%d %ds linear %ds infinite both;" % (n, 6 * n, 6 * i)
        inner += '<div class="tt-bb-s" style="%s"><b>%s</b><span>%s</span></div>' % (style, html.escape(head), html.escape(text))
    sc.sprite("billboard", 33.6, 5.7, 11.4, 5.6, (100, 58), _art_billboard, inner=inner, tip="What's on the dashboard: one slide per tab")


def _fall_festival(sc: _Scene, rng: LCG) -> None:
    if sc.event not in ("harvest_fair", "lantern_night"):  # those take over the west end of the meadow
        sc.sprite("cornmaze", 2.6, 6.3, 10.5, 2.3, (100, 22), _art_corn_maze, tip="Harvest festival corn maze")
        if rng.chance(P_SCARECROW):
            sc.sprite("scarecrow", 10.6, 8.1, 1.1, 1.9, (10, 18), _art_scarecrow, tip="The scarecrow keeps watch")
        sc.sprite("haymaze", 14.4, 6.5, 6.6, 1.55, (63, 15), _art_hay_maze, tip="Hay bale maze for the little ones")
    sc.sprite("market", 22.2, 6.4, 8.4, 2.3, (84, 23), _art_market, tip="Harvest market")
    apples = _stand("APPLES", "#b3262d", "#fff8e7", ("#d62828", "#7fb23a"))
    sc.sprite("apples", 31.2, 6.3, 2.3, 2.1, (20, 18), apples, tip="Apple stand: cider donuts while they last")


def _winter_meadow(sc: _Scene) -> None:
    if not sc.xmas:
        return
    sc.sprite("peanuts", 4.6, 6.6, 6.6, 2.5, (60, 23), _art_peanuts, tip="It's not such a bad little tree")
    cider = _stand("HOT CIDER", "#8a4b2a", "#f4c430", ("#e07a1f", "#b3262d"))
    sc.sprite("cider", 14.2, 6.6, 2.4, 2.2, (20, 18), cider, tip="Hot cider stand")


def _chestnuts(sc: _Scene) -> None:
    if not (sc.fall or sc.winter):
        return
    puff = ""
    if not sc.still:
        puff = '<i class="tt-puff" style="left:.9cqw;top:.2cqw;width:.5cqw;height:.5cqw;animation:tt-puff 5s ease-out infinite"></i>'
    sc.sprite("chestnut", 44.7, STREET - 0.2, 2.0, 1.9, (20, 18), _art_chestnut, inner=puff, tip="Chestnuts roasting")


def _post_office_pile(sc: _Scene, rng: LCG, day: dt.date):
    """The pile of presents, and (one load in five) the Grinch who takes it."""
    if not sc.xmas:
        return False
    n = present_count(day)
    grinch = rng.randint(1, GRINCH_ODDS) == 1 and not sc.still and n > 0
    if n:
        style = ""
        if grinch:
            sc.rule("pilek", "@keyframes tt-pile{0%,6%{opacity:1}9%,93%{opacity:0}100%{opacity:1}}")
            style = "animation:tt-pile %ds linear 12s 1 both;" % GRINCH_SECONDS
        sc.sprite("pile%d" % n, 63.6, 6.9, 2.4, 1.5, (24, 15), _art_presents(n), style=style, tip="%d presents waiting for the 25th" % n)
    return grinch


def _grinch(sc: _Scene) -> None:
    """A 350-second raid along the middle terrace, far from the train, left to right."""
    sc.rule(
        "grinchk",
        "@keyframes tt-grinch{0%{opacity:0;transform:translateX(0)}2%,97%{opacity:1}"
        "100%{opacity:0;transform:translateX(37cqw)}}"
        "@keyframes tt-heart{0%,68%{opacity:0;transform:scale(.3)}72%{opacity:1;transform:scale(1.5)}"
        "74%,82%{opacity:1;transform:scale(1)}86%,100%{opacity:0;transform:scale(1)}}",
    )
    anim = "%ds linear 12s 1 both" % GRINCH_SECONDS
    heart = '<i class="tt-s %s" style="left:.2cqw;bottom:1.5cqw;width:.8cqw;height:.75cqw;animation:tt-heart %s"></i>' % (sc.art("heart", (10, 9.4), _art_heart), anim)
    sc.sprite("grinch", 64.4, 7.0, 3.6, 1.35, (44, 16.4), _art_grinch, inner=heart, style="opacity:0;animation:tt-grinch %s;" % anim)


def carol_delay(now: dt.datetime) -> int:
    """Seconds since midnight: the negative delay that lines a 24-hour animation up with the clock."""
    return now.hour * 3600 + now.minute * 60 + now.second


def carol_keyframes() -> str:
    """Opacity on for the carol hours (12 to 1 and 3 to 4) of a 24-hour cycle."""
    out, last = "", 0.0
    for a, b in CAROL_HOURS:
        s, e = a / 24 * 100, b / 24 * 100
        out += "%s%%,%s%%{opacity:0}%s%%,%s%%{opacity:1}" % (_f4(last), _f4(s - 0.001), _f4(s), _f4(e - 0.001))
        last = e
    return "@keyframes tt-carol{%s%s%%,100%%{opacity:0}}" % (out, _f4(last))


def _f4(v: float) -> str:
    return ("%.4f" % v).rstrip("0").rstrip(".")


def _dickens(sc: _Scene, rng: LCG, now: dt.datetime, budget: int) -> int:
    """Dickens characters roam by chance; every one of them turns out to sing at carol time."""
    if not sc.xmas:
        return 0
    _person_css(sc)
    sc.rule("carolk", carol_keyframes())
    sc.rule("note", ".tt-note{position:absolute;left:55%;bottom:100%;font-size:.8cqw;font-weight:700;opacity:0;font-style:normal}")
    sc.themed("notec", ".tt-note{color:#27405f}", ".tt-note{color:#ffe9a8}")
    carol = "animation:tt-carol 86400s step-end -%ds infinite;" % carol_delay(now)
    singing = is_carol_time(now)
    roaming = 0
    for i, (name, tip, spec) in enumerate(_DICKENS):
        picked = (sc.event == "carol_weekend" or rng.chance(P_DICKENS)) and roaming < budget
        x = rng.uniform(58, 92) if picked else 49.4 + i * 1.25
        dx = rng.uniform(5, 14) * rng.choice((-1, 1))
        cls = sc.art("dk" + name, (10, 16), _art_dickens(spec))
        if sc.still:
            if not (picked or singing):
                continue
            note = '<i class="tt-note" style="opacity:1">&#9834;</i>' if singing else ""
            sc.put(cls, x, STREET, 0.95, 1.5, inner=note, tip=tip)
            continue
        note = '<i class="tt-note" style="%s">&#9834;</i>' % carol
        if picked:
            roaming += 1
            sc.put(cls + " tt-w tt-x", x, STREET, 0.95, 1.5, inner=note, tip=tip, style=_walk_style(rng, max(57 - x, min(98 - x, dx))))
        else:  # waits out of sight, steps up beside the gazebo for the carol hours
            sc.put(cls + " tt-x", x, STREET, 0.95, 1.5, inner=note, tip=tip, style="opacity:0;" + carol)
    return roaming


def _gazebo_crowd(sc: _Scene, rng: LCG) -> None:
    if not (sc.xmas and rng.chance(P_GAZEBO_CROWD)):
        return
    _person_css(sc)
    n = rng.randint(8, 12)
    for i in range(n):
        x = 46.4 + 4.6 * (i + rng.uniform(0.1, 0.9)) / n
        sc.put("tt-p", x, STREET - 0.12 * (i % 2), 0.32, 0.82, style="--c:%s;" % rng.choice(_COATS))
    sc.rule(
        "gnote",
        ".tt-gn{font-size:.9cqw;font-weight:700;font-style:normal}"
        "@keyframes tt-gn{0%{opacity:0;transform:translateY(0)}20%{opacity:1}"
        "100%{opacity:0;transform:translate(.5cqw,-2.2cqw)}}",
    )
    sc.themed("gnotec", ".tt-gn{color:#27405f}", ".tt-gn{color:#ffe9a8}")
    for i, x in enumerate((47.2, 48.6, 50.0)):
        style = sc.anim("tt-gn 4.5s ease-out -%.1fs infinite" % (i * 1.5)) or "opacity:.9;"
        sc.put("tt-gn", x, 2.9 + (i % 2) * 0.5, 0.8, 0.9, inner=("&#9834;", "&#9835;", "&#9834;")[i], tag="i", style=style)


def kings_x(day: dt.date) -> float:
    """Where the Three Kings stand today: waiting at 3, arriving at the stable on Jan 6."""
    return 3.0 + 30.5 * kings_progress(day)


def _kings(sc: _Scene, rng: LCG, day: dt.date) -> None:
    if not sc.xmas:
        return
    sc.sprite("stable", 37.4, 0.2, 3.2, 2.5, (30, 25), _art_stable, tip="The stable. The kings arrive January 6.")
    if not rng.chance(P_KINGS):
        return
    sc.rule("kingsk", "@keyframes tt-kings{0%,100%{opacity:0}5%,92%{opacity:1}25%,75%{transform:translateY(-.06cqw)}50%{transform:translateY(0)}}")
    left = days_to_christmas(day)
    tip = "The Three Kings set out on December 25" if left > 0 else "The Three Kings, on their way to January 6"
    sc.sprite("kings", kings_x(day), 0.15, 3.3, 1.65, (32, 16), _art_kings, tip=tip, style=sc.anim("tt-kings 140s ease-in-out -20s infinite"))


def _foreground(sc: _Scene, santa_href: str) -> None:
    sc.rule(
        "mail",
        ".tt-mail>summary{list-style:none;display:block;width:100%;height:100%;cursor:pointer;position:relative}"
        ".tt-mail>summary::-webkit-details-marker{display:none}"
        ".tt-letter{position:absolute;left:50%;bottom:calc(100% + .5cqw);translate:-50% 0;width:17cqw;"
        "background:#fffdf5;color:#3b2a1a;border:.1cqw solid #d9c9a3;border-radius:.4cqw;padding:.7cqw .9cqw;"
        "font-size:max(.8cqw,10px);z-index:100;box-shadow:0 .2cqw .8cqw rgba(0,0,0,.3)}"
        ".tt-letter b{display:block;margin-bottom:.3cqw}.tt-letter a{display:inline-block;margin-top:.4cqw;"
        "color:#b3262d;font-weight:700}"
        ".tt-say{position:absolute;left:70%;bottom:92%;background:#fff;color:#27405f;border-radius:.5cqw;"
        "padding:.2cqw .45cqw;font-size:.62cqw;font-weight:700;white-space:nowrap;"
        "box-shadow:0 .08cqw .3cqw rgba(0,0,0,.25)}",
    )
    letter = (
        '<summary class="tt-h"><span class="tt-tip">Send a letter to Santa</span></summary>'
        '<div class="tt-letter"><b>Dear Santa,</b>I have been very good with my budget this year. '
        'Here is what I am hoping for...<br><a href="%s">Send it to the North Pole &#9993;</a></div>'
    ) % html.escape(santa_href, quote=True)
    sc.sprite("mailbox", 48.6, 0.15, 1.15, 1.9, (12, 18), _art_mailbox, inner=letter, tag="details")
    sc.out[-1] = sc.out[-1].replace('class="tt-s ', 'class="tt-mail tt-s ', 1)
    if sc.winter:
        say = '<span class="tt-say">Merry Christmas!</span>'
        sc.sprite("snowman", 43.2, 0.1, 1.6, 2.2, (16, 22), _art_snowman, inner=say, tip="Merry Christmas, neighbor!")
    if sc.fall:
        sc.sprite("pumpkins", 41.6, 0.1, 2.2, 1.1, (22, 11), _art_pumpkins, tip="Pumpkin patch pickings")


def _lake_life(sc: _Scene, rng: LCG, hockey: bool) -> None:
    """Skaters and sledders: each turns up 70% of the time. Hockey takes the ice if it is on."""
    if not sc.winter or sc.still:
        return
    _person_css(sc)
    if hockey:
        sc.sprite("rink", 32.5, 1.85, 14, 2.8, (140, 28), _art_rink, when="day")
    if hockey or rng.chance(P_SKATERS):
        sc.rule(
            "skatek",
            "@keyframes tt-skate{0%,100%{transform:translate(0,0)}25%{transform:translate(var(--dx),-.5cqw)}"
            "50%{transform:translate(calc(var(--dx)*1.6),.1cqw)}75%{transform:translate(calc(var(--dx)*.7),.5cqw)}}",
        )
        n = 6 if hockey else rng.randint(2, 4)
        for i in range(n):
            x = rng.uniform(34.5, 38) if i % 2 == 0 else rng.uniform(41, 44.5)
            dx = rng.uniform(1.6, 3.2) * (1 if i % 2 == 0 else -1)
            coat = ("#c8323a", "#27405f")[i % 2] if hockey else rng.choice(_COATS)
            style = "--c:%s;--dx:%scqw;animation:tt-skate %ds ease-in-out -%ds infinite;" % (coat, _f(dx), rng.randint(7, 13), rng.randint(0, 9))
            sc.put("tt-p tt-x", x, rng.uniform(2.6, 3.4), 0.32, 0.82, style=style, when="day")
    if rng.chance(P_SLEDDERS):
        sc.rule("sledk", "@keyframes tt-sled{0%{opacity:0;transform:translate(0,0)}8%{opacity:1}42%{opacity:1}50%,100%{opacity:0;transform:translate(9.6cqw,2.3cqw)}}")
        for i in range(2):
            style = "--c:%s;height:.5cqw;border-bottom:.12cqw solid #8a4b2a;animation:tt-sled %ds ease-in -%ds infinite;" % (rng.choice(_COATS), 13 + i * 4, i * 5)
            sc.put("tt-p tt-x", 27.6, 13.9, 0.36, 0.5, style=style)


def _lights_on_the_lake(sc: _Scene) -> None:
    """Winter nights only: the lake gives way to a drive-through light show with five cars."""
    if not (sc.winter and sc.shows("night")):
        return
    sc.sprite("lotl", 32.3, 1.5, 14.4, 4.6, (144, 46), _art_lights_lake, when="night", tip="Lights on the Lake: stay in your car and enjoy")
    sc.rule(
        "lotlk",
        "@keyframes tt-twk{0%,100%{opacity:.25}50%{opacity:1}}"
        "@keyframes tt-car{0%{opacity:0;transform:translateX(0)}5%,93%{opacity:1}100%{opacity:0;transform:translateX(13.4cqw)}}",
    )
    sc.sprite("twinkle", 32.3, 1.5, 14.4, 4.6, (144, 46), _art_twinkle, when="night", style="pointer-events:none;" + sc.anim("tt-twk 2.4s ease-in-out infinite"))
    for i, color in enumerate(("#d62828", "#3a86ff", "#e9e2cf", "#2a9d8f", "#f4a261")):
        style = sc.anim("tt-car 45s linear -%ds infinite" % (i * 9)) or "left:%scqw;" % _f(33.5 + i * 2.5)
        sc.sprite("car%d" % i, 32.0, 1.86, 1.55, 0.62, (24, 10.4), _art_car(color), when="night", style=style)


def _theatre_lights(sc: _Scene) -> None:
    if sc.still or not sc.shows("night"):
        return
    sc.rule("marqk", "@keyframes tt-marq{0%,49%{opacity:1}50%,100%{opacity:.15}}")
    sc.sprite("marquee", 81.5, BASE, 5.8, 5.0 * TALL, (58, 52), _art_marquee, when="night", style="pointer-events:none;animation:tt-marq 1.6s linear infinite;")


def _weather(sc: _Scene) -> None:
    if sc.still:
        return
    if sc.winter:
        sc.rule("snowk", ".tt-drop{left:0;top:-25cqw;width:100cqw;height:50cqw;background-repeat:repeat;pointer-events:none}@keyframes tt-drop{to{transform:translateY(25cqw)}}")
        for i, (seed, n, dur) in enumerate(((3, 26, 22), (9, 16, 34))):
            cls = sc.art("snow%d" % i, (250, 250), _art_snow(seed, n))
            sc.raw('<div class="tt-s tt-drop tt-x %s" style="background-size:25cqw 25cqw;animation:tt-drop %ds linear infinite"></div>' % (cls, dur))


# -- Village landmarks and festivals ------------------------------------------------------------------------------
def _lawn(k: _K, w: float, h: float, band: float = 4) -> str:
    """A strip of the meadow's own colour so a landmark sits in the grass, not on the skyline."""
    return _r(0, h - band, w, band, k.w(_PAL[k.season]["meadow"]))


def _roof_snow(k: _K, pts) -> str:
    return _ln(pts, k.lit("#ffffff", "#c9d6ee"), 1.8) if k.snow else ""


def _art_hollis(k: _K) -> str:
    """Hollis Manor: red-brick farmhouse."""
    w, h = 46, 34
    brick, trim = k.w("#a64b32"), k.w("#f3eee2")
    out = _lawn(k, w, h)
    for x in (7, w - 10.6):
        out += _r(x, 3, 3.6, 12, k.w("#8c3d29"))
    out += _p([(3, 13), (w / 2, 4), (w - 3, 13)], k.w("#5a4a42"))
    out += _r(5, 13, w - 10, h - 17, brick) + _r(5, 13, w - 10, 1.2, trim)
    out += _roof_snow(k, [(3.4, 12.6), (w / 2, 4.6), (w - 3.4, 12.6)])
    for x in (9, 18, w - 24, w - 15):
        for y in (16, 24):
            out += _r(x, y, 5.2, 5.2, trim) + _r(x + 0.8, y + 0.8, 3.6, 3.6, k.win)
    out += _r(w / 2 - 3, 21, 6, 9, trim) + _r(w / 2 - 2.2, 22, 4.4, 8, k.w("#2f3d4a"))
    out += _r(w / 2 - 5, 20, 10, 1.2, trim)
    out += _wreath(k, w / 2, 26, 1.5)
    out += _r(w - 6, 18, 5, 12, k.w("#c9bba0")) + _r(w - 6, 17, 5.6, 1.2, k.w("#5a4a42"))
    return out


def _art_ashgrove(k: _K) -> str:
    """Ashgrove Hall: white Greek Revival house, long two-storey portico."""
    w, h = 52, 34
    white, roof = k.w("#f4f0e6"), k.w("#6b5a4f")
    out = _lawn(k, w, h)
    out += _r(2, 16, 11, h - 20, white) + _r(w - 13, 16, 11, h - 20, white)
    out += _p([(0, 17), (7.5, 11), (15, 17)], roof) + _p([(w - 15, 17), (w - 7.5, 11), (w, 17)], roof)
    out += _r(10, 10, w - 20, h - 14, white)
    out += _p([(8, 11), (w / 2, 1.5), (w - 8, 11)], white) + _ln([(8, 11), (w / 2, 1.5), (w - 8, 11)], roof, 1.6)
    out += _c(w / 2, 7, 1.6, k.w("#cdb98a"))
    out += _roof_snow(k, [(8.4, 10.6), (w / 2, 2.2), (w - 8.4, 10.6)])
    for i in range(5):
        out += _r(11 + i * 7.6, 11, 1.8, h - 15, k.w("#dcd6c6"))
    for x in (14.4, 22, 30.4):
        out += _r(x, 14, 3.4, 5, k.w("#2f4a3a")) + _r(x + 0.5, 14.5, 2.4, 4, k.win)
    out += _r(w / 2 - 2.4, 22, 4.8, 8.4, k.w("#3b2a20"))
    for x in (4.6, w - 10.6):
        out += _r(x, 20, 3.6, 5, k.w("#2f4a3a")) + _r(x + 0.5, 20.5, 2.6, 4, k.win)
    out += _wreath(k, w / 2, 26, 1.4)
    return out


def _art_courthouse(k: _K) -> str:
    """Town Hall on the Green, with a stone column out front."""
    w, h = 50, 34
    brick, trim = k.w("#b4573d"), k.w("#f1ece0")
    out = _lawn(k, w, h)
    out += _r(4, 16, 34, h - 20, brick) + _r(4, 15, 34, 1.4, trim)
    out += _r(16.5, 6, 11, 11, brick) + _r(16.5, 5, 11, 1.2, trim)
    out += _r(18.8, 1.6, 6.4, 4, trim) + _p([(18, 1.8), (22, -0.6), (26, 1.8)], k.w("#4d5562"))
    out += _c(22, 10.5, 2.6, "#fffdf2") + _ln([(22, 10.5), (22, 8.9)], "#222", 0.5) + _ln([(22, 10.5), (23.2, 11)], "#222", 0.5)
    out += _p([(12, 16), (22, 11.6), (32, 16)], trim)
    for x in (14, 18.4, 25, 29.4):
        out += _r(x, 16, 1.6, h - 20, trim)
    for x in (6.4, 9.4, 34, 36.6):
        out += _r(x, 20, 2.2, 5, k.win) + _r(x, 20, 2.2, 0.8, trim)
    out += _r(20.4, 22, 3.8, 8, k.w("#3b2a20"))
    out += _wreath(k, 22.3, 24, 1.3)
    out += _r(42, 25, 5, 5, k.w("#b8b2a4")) + _r(43.2, 10, 2.6, 15, k.w("#d6d0c2")) + _c(44.5, 9.2, 1.5, k.w("#c9c3b4"))
    return out


def _art_mill(k: _K) -> str:
    """The Old Mill: long brick works with arched windows and a tall stack."""
    w, h = 56, 30
    brick = k.w("#9c4a36")
    out = _lawn(k, w, h, 3.5)
    out += _r(46, 0, 4.2, 14, k.w("#8a3f2e")) + _r(45.6, 3, 5, 1.2, k.w("#f1ece0")) + _r(45.6, 0, 5, 1.2, k.w("#3b2a20"))
    out += _r(2, 12, 50, h - 15.5, brick) + _r(1, 10.6, 52, 2, k.w("#6e3426"))
    out += _r(8, 14.4, 22, 4.2, k.w("#f1ece0")) + _t(19, 17.8, 3.6, "OLD MILL", "#8a3f2e", 19)
    for i in range(7):
        x = 4.4 + i * 7
        out += _r(x, 21, 4, 6, k.win) + _c(x + 2, 21, 2, k.win) + _r(x + 1.8, 21, 0.4, 6, k.w("#6e3426"))
    out += _roof_snow(k, [(1, 10.2), (53, 10.2)])
    return out


def _art_flagpole(k: _K) -> str:
    out = _r(1.2, 3, 0.6, 27, k.w("#9aa0aa")) + _c(1.5, 2.6, 0.9, "#f1c84b")
    for i in range(6):
        out += _r(2, 3.6 + i * 1.4, 11, 0.7, "#d62828" if i % 2 == 0 else "#ffffff")
    return out + _r(2, 3.6, 4.6, 4.2, "#27405f")


def _heritage(sc: _Scene) -> None:
    """The village landmarks along the meadow crest, behind whatever the season puts out front."""
    items = (
        ("hollis", 0.8, 4.6, 3.4, (46, 34), _art_hollis, "Hollis Manor, the oldest house in the village"),
        ("ashgrove", 6.1, 5.2, 3.4, (52, 34), _art_ashgrove, "Ashgrove Hall"),
        ("court", 11.9, 5.0, 3.4, (50, 34), _art_courthouse, "Town Hall, on the Green"),
        ("mill", 17.3, 5.6, 3.0, (56, 30), _art_mill, "The Old Mill"),
    )
    for name, x, w, h, vb, fn, tip in items:
        y = _interp(MEADOW, x + w / 2) - 0.4
        sc.sprite(name, x, y, w, h, vb, fn, tip=tip)
    if sc.event == "founders_day":
        sc.sprite("flag1", 4.9, _interp(MEADOW, 5.4) - 0.4, 1.4, 3.0, (14, 30), _art_flagpole, tip="Flags out on Hollis Manor lawn")
        sc.sprite("flag2", 16.4, _interp(MEADOW, 16.9) - 0.4, 1.4, 3.0, (14, 30), _art_flagpole, tip="Flags out on the Green")


# -- event props --
def _art_tents(n: int, colors):
    """A row of white festival tents with coloured pennants, in a (n * 22) x 20 box."""

    def fn(k: _K) -> str:
        out = ""
        for i in range(n):
            x = i * 22
            c = colors[i % len(colors)]
            out += _r(x + 3, 8, 0.8, 11, k.w("#8a8f99")) + _r(x + 16.2, 8, 0.8, 11, k.w("#8a8f99"))
            out += _p([(x + 1, 9), (x + 10, 1.5), (x + 19, 9)], k.w("#fbfaf4")) + _ln([(x + 1, 9), (x + 10, 1.5), (x + 19, 9)], k.w("#c9c3b4"), 0.5)
            out += _r(x + 3.5, 11, 13, 7, k.w(c)) + _r(x + 5, 12.4, 10, 3, k.w("#fff8e7"))
            out += _ln([(x + 10, 1.5), (x + 10, -1)], k.w("#8a8f99"), 0.4) + _p([(x + 10, -1), (x + 13, 0), (x + 10, 1)], k.w(c))
            if k.snow:
                out += _ln([(x + 1.4, 8.6), (x + 10, 2), (x + 18.6, 8.6)], k.lit("#ffffff", "#c9d6ee"), 1.4)
        return out

    return fn


def _art_stage(banner: str, accent: str):
    """A festival stage under a banner, two musicians, lighting truss and speaker stacks."""

    def fn(k: _K) -> str:
        w, h = 80, 28
        out = _r(0, 6, 4, 18, k.w("#2b2f36")) + _r(w - 4, 6, 4, 18, k.w("#2b2f36"))
        out += _r(4, 4, w - 8, 20, k.w("#3c4350")) + _r(4, 4, w - 8, 3.4, k.w(accent))
        out += _r(6, 8, w - 12, 5.6, "#fff8e7") + _t(w / 2, 12.5, 4.4, banner, "#27405f", w - 18)
        for x in range(8, w - 6, 7):
            out += _c(x, 5.7, 0.9, k.lit("#fff3b0", "#ffe36b"))
        out += _r(4, 21, w - 8, 3, k.w("#8a6a3c"))
        out += _g(28, 12.4, _fig(k.w("#b3262d"), "top", "#2b2f36", extra=_r(8, 8, 0.8, 7, "#4a2c1a")), 0.52)
        out += _g(46, 12.4, _fig(k.w("#3a86ff"), "cap", "#27405f"), 0.52)
        out += _r(0, 24, w, 4, k.w("#6e5a3c"))
        if k.snow:
            out += _r(4, 3.2, w - 8, 1, k.lit("#ffffff", "#c9d6ee"))
        return out

    return fn


def _art_bunting(colors):
    """Swags of pennants across a street, in a 370 x 12 box."""

    def fn(k: _K) -> str:
        out = ""
        for s in range(5):
            x0 = s * 74
            pts = [(x0 + t, 1 + 6 * (1 - ((t - 37) / 37) ** 2)) for t in range(0, 75, 5)]
            out += _ln(pts, k.w("#5a4a42"), 0.7)
            for i, (x, y) in enumerate(pts[1:-1]):
                out += _p([(x - 2.4, y), (x + 2.4, y), (x, y + 4.4)], k.w(colors[(s + i) % len(colors)]))
        return out

    return fn


def _art_wheel(k: _K) -> str:
    """The Ferris wheel itself (spins), in a 40 x 40 box."""
    out = _c(20, 20, 18, "none", f" stroke='{k.w('#cfd4dc')}' stroke-width='1.2'") + _c(20, 20, 11, "none", f" stroke='{k.w('#cfd4dc')}' stroke-width='0.7'")
    for i in range(8):
        a = i * 0.7853981
        x, y = 20 + 18 * math.cos(a), 20 + 18 * math.sin(a)
        out += _ln([(20, 20), (x, y)], k.w("#cfd4dc"), 0.7)
        out += _r(x - 2.2, y - 1.6, 4.4, 4, k.w(("#d62828", "#f4c430", "#3a86ff", "#2a9d8f")[i % 4]))
    return out + _c(20, 20, 2.2, k.w("#8a8f99"))


def _art_wheel_base(k: _K) -> str:
    out = _ln([(20, 20), (9, 40)], k.w("#8a8f99"), 1.6) + _ln([(20, 20), (31, 40)], k.w("#8a8f99"), 1.6)
    return out + _r(6, 38.6, 28, 1.4, k.w("#5c4a3a"))


def _art_farmers(k: _K) -> str:
    out = ""
    for i, (sign, roof, goods) in enumerate(
        (("PRODUCE", "#2f6b4f", ("#d62828", "#7fb23a", "#e8761c")), ("FLOWERS", "#d98fa5", ("#f06292", "#ffd54f", "#ba68c8")), ("BREAD", "#8a4b2a", ("#d9b44a", "#b8860b", "#e3c585")))
    ):
        out += _g(i * 26, 0, _stand(sign, roof, "#fff8e7", goods)(k), 1.15)
    return out


def _art_blankets(k: _K) -> str:
    out = ""
    for i, c in enumerate(("#d62828", "#27405f", "#2a9d8f")):
        x = 1 + i * 11
        out += _r(x, 3, 9, 3, k.w(c)) + _r(x + 1, 3.6, 7, 1.8, k.w("#fff8e7"))
        out += _c(x + 2.4, 2.4, 1, k.w("#e8c98a")) + _c(x + 6, 2.4, 1, k.w("#f0c8a0"))
    return out


def _art_firework(color: str):
    def fn(k: _K) -> str:
        out = ""
        for i in range(12):
            a = i * 0.5236
            c, s = math.cos(a), math.sin(a)
            out += _ln([(20 + 5 * c, 20 + 5 * s), (20 + 17 * c, 20 + 17 * s)], color, 1.2)
            out += _c(20 + 18.5 * c, 20 + 18.5 * s, 1.1, "#ffffff")
        return out + _c(20, 20, 2, "#ffffff")

    return fn


def _art_luminaria(k: _K) -> str:
    """Rows of candle bags, one for every household."""
    out = ""
    for row, y in enumerate((2, 8, 14)):
        for i in range(int(46 - row * 2)):
            x = 1 + i * 2.1 + row * 1.0
            out += _r(x, y, 1.5, 2, k.w("#d9c08a")) + _c(x + 0.75, y - 0.3, 0.7, "#ffb347")
            if k.night:
                out += _c(x + 0.75, y + 0.4, 1.9, "#ffb347", " opacity='.22'")
    return out


def _art_banner(text: str, fill: str):
    """A street banner on two poles."""

    def fn(k: _K) -> str:
        out = _r(1, 3, 0.9, 20, k.w("#5a4a42")) + _r(98, 3, 0.9, 20, k.w("#5a4a42"))
        out += _r(2, 2, 96, 11, k.w(fill)) + _r(3, 3, 94, 9, k.w(fill), " stroke='#f4c430' stroke-width='.7'")
        return out + _t(50, 10, 6.4, text, "#fff8e7", 88)

    return fn


def _festival_ground(sc: _Scene, rng: LCG) -> None:
    """Event props on the meadow, in front of the heritage row."""
    ev = sc.event
    if ev == "spring_fair":
        sc.sprite("mfstage", 2.4, 6.2, 7.0, 2.3, (80, 28), _art_stage("SPRING FAIR", "#d62828"), tip="Spring Fair: music, crafts and food, last weekend of April")
        sc.sprite("mftents", 10.2, 6.3, 12.8, 2.0, (88, 20), _art_tents(4, ("#d62828", "#3a86ff", "#f4c430", "#2a9d8f")), tip="Spring Fair: vendor tents")
        sc.sprite("mfblankets", 25.4, 6.3, 3.4, 0.9, (34, 8), _art_blankets, tip="Blankets out for the music")
    elif ev == "village_market":
        sc.sprite("farmers", 14.4, 6.4, 8.4, 2.3, (84, 23), _art_farmers, tip="Village Market: Saturday mornings")
    elif ev == "founders_day":
        sc.sprite("blankets", 4.0, 6.3, 3.4, 0.9, (34, 8), _art_blankets, tip="Picnic blankets staked out for the fireworks")
        sc.sprite("blankets2", 21.0, 6.3, 3.4, 0.9, (34, 8), _art_blankets, tip="Picnic blankets staked out for the fireworks")
        for i, (x, y, color, delay) in enumerate(((7.0, 15.5, "#ff5a5f", 0), (18.5, 17.5, "#ffd23f", 3), (27.5, 14.5, "#4aa3ff", 6))):
            sc.rule("fwk", "@keyframes tt-fw{0%,8%{opacity:0;transform:scale(.2)}14%{opacity:1;transform:scale(.8)}30%{opacity:.9;transform:scale(1)}40%,100%{opacity:0;transform:scale(1.1)}}")
            style = "pointer-events:none;" + sc.anim("tt-fw 9s ease-out -%ds infinite" % delay)
            sc.sprite("fw%d" % i, x, y, 5.2, 5.2, (40, 40), _art_firework(color), when="night", style=style or "pointer-events:none;", tip="Founders' Day: fireworks at dark")
    elif ev == "harvest_fair":
        sc.sprite("wheel", 23.4, 7.1, 4.6, 4.6, (40, 40), _art_wheel_base)
        spin = sc.anim("tt-spin 70s linear infinite")
        sc.rule("spink", "@keyframes tt-spin{to{transform:rotate(360deg)}}")
        sc.sprite("wheelring", 23.4, 7.1, 4.6, 4.6, (40, 40), _art_wheel, style=spin, tip="Harvest Music Fair: the Ferris wheel")
        sc.sprite("pstage", 2.4, 6.2, 7.6, 2.5, (80, 28), _art_stage("HARVEST MUSIC FAIR", "#7a3ea1"), tip="Harvest Music Fair, last weekend of September")
        sc.sprite("ptents", 10.6, 6.3, 9.6, 2.0, (66, 20), _art_tents(3, ("#7a3ea1", "#f4c430", "#2a9d8f")), tip="Harvest Music Fair: food and craft tents")
    elif ev == "lantern_night":
        sc.rule("lumk", "@keyframes tt-lum{0%,100%{opacity:.75}50%{opacity:1}}")
        sc.sprite("luminaria", 1.4, 6.0, 22.4, 1.8, (100, 22), _art_luminaria, style=sc.anim("tt-lum 3.2s ease-in-out infinite") or "", tip="Lantern Night: a light for every neighbour, end of November")


def _festival_street(sc: _Scene) -> None:
    """Event props over the shop row and the street."""
    ev = sc.event
    if ev == "spring_fair":
        sc.sprite("bunting", 61.4, 5.0, 37.0, 1.2, (370, 12), _art_bunting(("#d62828", "#f4c430", "#3a86ff", "#2a9d8f")), tip="Spring Fair pennants")
    elif ev == "founders_day":
        sc.sprite("bunting", 61.4, 5.0, 37.0, 1.2, (370, 12), _art_bunting(("#d62828", "#ffffff", "#27405f")), tip="Founders' Day: red, white and blue")
    elif ev == "carol_weekend":
        sc.sprite("dkbanner", 47.2, 5.5, 9.6, 2.3, (100, 24), _art_banner("CAROL WEEKEND", "#7a1f2b"), tip="Carol Weekend: the whole cast is out on the street")
    elif ev == "harvest_fair":
        sc.sprite("bunting", 61.4, 5.0, 37.0, 1.2, (370, 12), _art_bunting(("#7a3ea1", "#f4c430", "#2a9d8f")), tip="Harvest Music Fair week")


# -- rotating shows ------------------------------------------------------------------------------
def show_pool(season: str, xmas: bool) -> tuple:
    if season == "winter":
        if xmas:
            return ("santa", "hockey", "snowball", "ufo", "bandits", "soldiers", "griswold", "snoopy")
        return ("hockey", "snowball", "ufo", "birds")
    if season == "fall":
        return ("ufo", "hayride", "crows", "leaves", "balloon")
    return ("balloon", "kite", "ufo", "birds")


def pick_shows(rng: LCG, season: str, xmas: bool) -> list:
    """Two or three shows from the season's pool, so the whole pool never runs at once."""
    return rng.sample(show_pool(season, xmas), rng.randint(2, 3))


def _show_ufo(sc):
    sc.rule("ufok", "@keyframes tt-ufo{0%{opacity:0;transform:translate(0,0)}6%{opacity:1}30%{transform:translate(28cqw,1.2cqw)}38%,52%{transform:translate(36cqw,2.6cqw)}80%{transform:translate(70cqw,-.6cqw)}94%{opacity:1}100%{opacity:0;transform:translate(96cqw,-2cqw)}}")
    sc.sprite("ufo", -2, 17.6, 3.0, 3.0, (30, 30), _art_ufo, style="pointer-events:none;animation:tt-ufo 75s ease-in-out -8s infinite;")


def _show_balloon(sc):
    sc.rule("ballk", "@keyframes tt-ball{0%{opacity:0;transform:translate(0,0)}5%,92%{opacity:1}50%{transform:translate(18cqw,1.6cqw)}100%{opacity:0;transform:translate(36cqw,-.6cqw)}}")
    sc.sprite("balloon", 8, 15.6, 2.0, 2.5, (20, 25), _art_balloon, style="pointer-events:none;animation:tt-ball 120s linear -30s infinite;")


def _show_birds(sc):
    sc.rule("birdk", "@keyframes tt-bird{0%{transform:translate(0,0)}50%{transform:translate(58cqw,1.4cqw)}100%{transform:translate(116cqw,0)}}")
    sc.sprite("birds", -8, 20.4, 3.6, 0.9, (36, 9), _art_birds, style="pointer-events:none;animation:tt-bird 70s linear -15s infinite;")


def _show_kite(sc):
    sc.rule("kitek", "@keyframes tt-kite{0%,100%{transform:rotate(-8deg)}50%{transform:rotate(9deg) translateY(-.4cqw)}}")
    sc.sprite("kite", 22, 10.4, 1.2, 2.5, (12, 25), _art_kite, style="transform-origin:50% 100%;animation:tt-kite 7s ease-in-out infinite;")


def _show_hayride(sc):
    sc.rule("hayk", "@keyframes tt-hay{0%{opacity:0;transform:translateX(0)}6%,92%{opacity:1}100%{opacity:0;transform:translateX(30cqw)}}")
    sc.sprite("wagon", 1, 8.7, 3.7, 1.45, (37, 14.4), _art_wagon, style="animation:tt-hay 80s linear -10s infinite;")


def _show_crows(sc):
    sc.rule("crowk", "@keyframes tt-crow{0%{transform:rotate(0) translateX(1.6cqw) rotate(0)}100%{transform:rotate(360deg) translateX(1.6cqw) rotate(-360deg)}}")
    sc.sprite("crows", 9.6, 10.2, 2.3, 0.75, (23, 7.4), _art_crows, style="pointer-events:none;animation:tt-crow 14s linear infinite;")


def _show_leaves(sc):
    sc.rule("leafk", ".tt-drop{left:0;top:-25cqw;width:100cqw;height:50cqw;background-repeat:repeat;pointer-events:none}@keyframes tt-leaf{to{transform:translate(-25cqw,25cqw)}}")
    cls = sc.art("leaves", (250, 250), _art_leaves)
    sc.raw('<div class="tt-s tt-drop tt-x %s" style="width:125cqw;background-size:25cqw 25cqw;animation:tt-leaf 26s linear infinite"></div>' % cls)


def _show_soldiers(sc):
    sc.rule("marchk", "@keyframes tt-march{0%{opacity:0;transform:translateX(0)}4%,95%{opacity:1}100%{opacity:0;transform:translateX(36cqw)}}")
    sc.sprite("soldiers", 59.5, STREET, 4.2, 1.6, (46, 18.6), _art_soldiers, style="animation:tt-march 70s linear -5s infinite;")


def _show_bandits(sc):
    sc.rule("bandk", "@keyframes tt-band{0%{opacity:0;transform:translateX(0)}5%{opacity:1}45%,55%{transform:translateX(-12.5cqw)}62%{transform:translateX(-12.2cqw) rotate(-14deg)}90%{opacity:1}100%{opacity:0;transform:translateX(0)}}")
    sc.sprite("bandits", 97, 7.0, 1.7, 1.45, (18, 16), _art_bandits, style="animation:tt-band 60s ease-in-out -4s infinite;")


def _show_snowball(sc):
    sc.sprite("snowkids", 22.6, 8.0, 6.8, 1.6, (68, 16), _art_snowkids)
    sc.rule("ballsk", ".tt-sb{border-radius:50%;background:#fff;box-shadow:0 0 0 .04cqw #9fb4cc}@keyframes tt-sb{0%{opacity:0;transform:translate(0,0)}8%{opacity:1}50%{transform:translate(calc(var(--dx)*.5),-1.1cqw)}92%{opacity:1}100%{opacity:0;transform:translate(var(--dx),0)}}")
    for x, dx, delay in ((23.6, 4.4, 0), (28.2, -4.2, -1.3)):
        sc.put("tt-sb tt-x", x, 9.0, 0.24, 0.24, style="--dx:%scqw;animation:tt-sb 2.6s linear %ss infinite;" % (_f(dx), delay))


def _show_griswold(sc):
    sc.rule("grisk", ".tt-glow{border-radius:.6cqw;background:radial-gradient(rgba(255,244,190,.85),rgba(255,244,190,0) 68%);pointer-events:none}@keyframes tt-gris{0%,18%{opacity:0}20%{opacity:.9}22%{opacity:0}26%{opacity:.9}28%{opacity:.1}34%,90%{opacity:1}100%{opacity:0}}")
    sc.put("tt-glow tt-x", 74.2, 10.0, 7.6, 5.6, style="animation:tt-gris 30s linear infinite;")


def _show_snoopy(sc):
    sc.rule("snoopk", "@keyframes tt-snoop{0%,100%{transform:translateY(0) rotate(0)}25%{transform:translateY(-.25cqw) rotate(-12deg)}75%{transform:translateY(-.25cqw) rotate(12deg)}}")
    sc.put("tt-gn tt-x", 9.6, 9.0, 0.8, 0.9, inner="&#9835;", tag="i", style="animation:tt-snoop 1.6s ease-in-out infinite;")


def _show_santa(sc):
    """The sleigh lands on the back terrace roofs; three elves run presents to the houses."""
    sc.rule(
        "santak",
        "@keyframes tt-santa{0%{opacity:0;transform:translate(-8cqw,8cqw) rotate(8deg)}4%{opacity:1}"
        "26%{transform:translate(60cqw,2.4cqw) rotate(6deg)}32%,70%{transform:translate(72cqw,0) rotate(0)}"
        "92%{opacity:1}100%{opacity:0;transform:translate(112cqw,9cqw) rotate(-10deg)}}"
        "@keyframes tt-elf{0%,34%{opacity:0;transform:translate(0,0)}36%{opacity:1}"
        "48%,54%{transform:translate(var(--dx),0)}66%{opacity:1}68%,100%{opacity:0;transform:translate(0,0)}}"
        "@keyframes tt-gift{0%,49%{opacity:0}51%,94%{opacity:1}100%{opacity:0}}",
    )
    _person_css(sc)
    anim = "100s linear -22s infinite"
    sc.sprite("sleigh", 12, 14.2, 4.4, 1.7, (42, 16.4), _art_sleigh, style="pointer-events:none;opacity:0;animation:tt-santa %s;" % anim)
    gift = sc.art("gift", (24, 15), _art_presents(2))
    for i, (door, x0) in enumerate(((87.8, 86.0), (91.5, 86.6), (69.4, 85.4))):
        sc.put("tt-p tt-x", x0, 11.0, 0.26, 0.62, style="--c:#2f7d32;--dx:%scqw;opacity:0;animation:tt-elf %s;" % (_f(door - x0), anim))
        sc.put(gift + " tt-x", door - 0.5, 11.0, 0.9, 0.5, style="opacity:0;animation:tt-gift %s;" % anim)


_SHOWS = {
    "ufo": _show_ufo,
    "balloon": _show_balloon,
    "birds": _show_birds,
    "kite": _show_kite,
    "hayride": _show_hayride,
    "crows": _show_crows,
    "leaves": _show_leaves,
    "soldiers": _show_soldiers,
    "bandits": _show_bandits,
    "snowball": _show_snowball,
    "griswold": _show_griswold,
    "snoopy": _show_snoopy,
    "santa": _show_santa,
}


# --------------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------------
def _resolve(theme: str, day: dt.date):
    """(season, christmas?) for a theme override, falling back to the calendar."""
    if theme not in THEMES:
        raise ValueError("theme must be one of %s" % ", ".join(THEMES))
    if theme == "auto":
        season = season_for(day)
        return season, season == "winter" and is_christmastide(day)
    if theme == "christmas":
        return "winter", True
    return theme, theme == "winter" and is_christmastide(day)


def train_html(
    title: str = "Trusted Train",
    *,
    mode: str = "clock",
    theme: str = "auto",
    now: dt.datetime | None = None,
    seed: int | None = None,
    still: bool = False,
    slides=None,
    santa_href: str = "mailto:?subject=Dear%20Santa",
    event: str = "auto",
) -> str:
    """Return ``<style>`` plus markup for the Trusted Train header.

    mode   "clock" (default: dawn to dusk follows ``now``, and keeps going while the page is
           open), "light", "dark", or "auto" (follow ``prefers-color-scheme``).
    theme  "auto" picks the season from ``now``; or force spring/summer/fall/winter/christmas.
    now    The moment to render for (season, carol hours, Monday/Wednesday, the kings' journey).
    seed   Seeds the chance generator. Pass one value per session so reruns keep the same cast.
    still  Turn every animation off and show a calm, static scene.
    slides Bulletin-board slides as (heading, text) pairs, one per dashboard tab.
    event  village festival to stage: "auto" follows the calendar (see ``village_event``),
           "none" turns them off, or name one of ``EVENTS``. A named event picks its own season
           when ``theme`` is "auto".
    """
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % ", ".join(MODES))
    now = now or dt.datetime.now()
    if seed is None:
        seed = time.time_ns() & 0xFFFFFFFF
    season, xmas, ev = resolve_event(event, theme, now.date())
    secs = now.hour * 3600 + now.minute * 60 + now.second
    sc = _Scene(mode, still, season, xmas, ev, secs)

    rng = LCG(seed)
    day = now.date()
    shows = [] if still else pick_shows(rng, season, xmas)

    _base_css(sc)
    _sky(sc)
    _far(sc)
    _heritage(sc)
    _festival_ground(sc, rng)
    if sc.fall:
        _fall_festival(sc, rng)
    _winter_meadow(sc)
    _billboard(sc, slides)
    _hill(sc)
    grinch = _post_office_pile(sc, rng, day)
    _lakefront(sc)
    _lights_on_the_lake(sc)
    _lake_life(sc, rng, "hockey" in shows)
    _chestnuts(sc)
    _street(sc)
    _theatre_lights(sc)
    _festival_street(sc)
    _gazebo_crowd(sc, rng)
    roamers = _dickens(sc, rng, now, MAX_WALKERS)
    _walkers(sc, rng, MAX_WALKERS - roamers)
    _staff(sc, rng, day)
    if grinch:
        _grinch(sc)
    for name in shows:
        if name in _SHOWS:
            _SHOWS[name](sc)
    _train(sc, title)
    _foreground(sc, santa_href)
    _kings(sc, rng, day)
    _weather(sc)
    if sc.clock:
        warm = day_level(WARM_STOPS, sc.secs / 3600.0)
        sc.raw('<div class="tt-s tt-full tt-warm" style="opacity:%s"></div>' % _f(warm))

    return _assemble(sc, title)


def _assemble(sc: _Scene, title: str) -> str:
    css = "".join(sc.css.values())
    if sc.mode == "auto" and sc.dark:
        css += "@media (prefers-color-scheme:dark){%s}" % "".join(sc.dark.values())
    if not sc.still:
        css += (
            "@media (prefers-reduced-motion:reduce){.tt-scene *{animation:none!important;"
            "transition:none!important}.tt-x{display:none!important}}"
        )
    cls = "tt-stage tt-%s tt-in-%s%s" % (sc.mode, sc.season, " tt-still" if sc.still else "")
    return '<style>%s</style><div class="%s" aria-label="%s"><div class="tt-scene">%s</div></div>' % (
        css,
        cls,
        html.escape("%s header scene" % title, quote=True),
        "".join(sc.out),
    )
