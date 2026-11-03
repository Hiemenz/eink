"""
Shared helpers for the election-night displays (House / Senate / Governor).

Data comes from the NYT elections JSON feed (keyless, unofficial). Rendering
uses the panel's 7-color palette: blue = Democrat, red = Republican,
yellow/green = other, so results are in color on the ACeP display.
"""

import json
import os
import time
from datetime import datetime

import requests
from PIL import Image, ImageDraw

from utils import get_font, get_logger

logger = get_logger("election")

WIDTH, HEIGHT = 800, 480
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
BLUE, RED = (0, 0, 255), (255, 0, 0)
GREEN, YELLOW, ORANGE = (0, 255, 0), (255, 255, 0), (255, 128, 0)
PARTY_COLOR = {"DEM": BLUE, "GOP": RED, "OTH": GREEN}

FEED = "https://static01.nyt.com/elections-assets/pages/data/{date}/results-{office}.json"
UA = {"User-Agent": "Mozilla/5.0"}
DEFAULT_DATE = "2026-11-03"
CACHE_DIR = "data"


REQUIRED_KEYS = ("races", "partyControlData", "resultsMeta")


def _validate(office: str, data: dict) -> None:
    """Raise if the unofficial feed changed shape (so we alert instead of draw garbage)."""
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing or not data["races"]:
        raise ValueError(f"{office} feed missing {missing or 'races'}")
    r = data["races"][0]
    for k in ("candidate_metadata", "outcome", "top_reporting_unit"):
        if k not in r:
            raise ValueError(f"{office} race missing '{k}'")


def _alert(office: str, detail: str) -> None:
    from utils import alert_failure  # local import: keeps this module light
    alert_failure(f"eink election feed ({office})", detail)


def _clean(data: dict) -> dict:
    """Drop the feed's non-state placeholder race ('US') so it never reaches a screen."""
    data["races"] = [r for r in data.get("races", [])
                     if r.get("top_reporting_unit", {}).get("state_postal") != "US"]
    return data


def fetch_results(office: str, config: dict, max_age: int = 120) -> dict:
    return _clean(_fetch_results(office, config, max_age))


def _fetch_results(office: str, config: dict, max_age: int = 120) -> dict:
    """Fetch (and cache on disk) the NYT results JSON for house/senate/governor.

    Falls back to the last good cache (flagged `_stale`) and raises an alert when
    the feed is unreachable or has changed shape.
    """
    cfg = config.get("election", {})
    date = cfg.get("date", DEFAULT_DATE)
    if cfg.get("replay_dir"):  # dry run: read synthetic results, never touch the network
        with open(os.path.join(cfg["replay_dir"], f"election_{office}.json")) as f:
            return json.load(f)
    path = os.path.join(CACHE_DIR, f"election_{office}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path) as f:
            return json.load(f)
    try:
        r = requests.get(FEED.format(date=date, office=office), headers=UA, timeout=60)
        r.raise_for_status()
        data = r.json()
        _validate(office, data)
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
        return data
    except Exception as exc:
        logger.warning("Election fetch failed (%s): %s", office, exc)
        _alert(office, f"{type(exc).__name__}: {exc}")
        if os.path.exists(path):  # stale beats blank
            with open(path) as f:
                data = json.load(f)
            data["_stale"] = True
            return data
        raise


def feed_time(data) -> str:
    """Local 'Nov 3, 9:55 PM' time the NYT feed last refreshed its results."""
    try:
        ts = datetime.fromisoformat(data["resultsMeta"]["resultsUpdatedAt"].replace("Z", "+00:00"))
        return ts.astimezone().strftime("%b %-d, %-I:%M %p") + (" (STALE)" if data.get("_stale") else "")
    except (KeyError, ValueError):
        return datetime.now().strftime("%b %-d, %-I:%M %p")


def control(data: dict, office_id: str) -> dict:
    """Return the national party-control block for an office ('H' or 'S')."""
    return data["partyControlData"]["results"][0]["offices"][office_id]


def party_of(race: dict, cand_id: str) -> str:
    p = race["candidate_metadata"][cand_id]["party"]["nyt_id"]
    return p if p in ("DEM", "GOP") else "OTH"


def race_summary(race: dict) -> dict:
    """Condense a race into leader/called/votes info for rendering."""
    unit = race["top_reporting_unit"]
    meta = race["candidate_metadata"]
    cands = []
    for c in unit["candidates"]:
        cm = meta.get(c["nyt_id"], {})
        cands.append({
            "id": c["nyt_id"],
            "name": cm.get("last_name", c["nyt_id"]),
            "party": party_of(race, c["nyt_id"]) if cm else "OTH",
            "votes": c["votes"]["total"],
            "leader": c.get("leader", False),
        })
    cands.sort(key=lambda c: -c["votes"])
    won = race["outcome"]["won"]
    winner = next((c for c in cands if c["id"] in won), None)
    total = sum(c["votes"] for c in cands)
    return {
        "state": unit.get("state_abb") or unit["name"],
        "postal": unit.get("state_postal"),
        "seat": race.get("seat_number"),
        "cands": cands,
        "winner": winner,
        "called": bool(won),
        "total": total,
        "reporting": unit.get("eevp") or 0,
        "rating": race.get("rating"),
        "id": race["nyt_id"],
        "flip": bool(race["outcome"].get("gained")),
    }


def rating_color(rating):
    """Pre-call color from the NYT rating: blue/red lean, yellow toss-up."""
    r = (rating or "").lower()
    if "dem" in r:
        return BLUE
    if "rep" in r:
        return RED
    return YELLOW if r else BLACK


CALLS_FILE = os.path.join(CACHE_DIR, "election_calls.json")
RECENT_CALL_SECONDS = 30 * 60


def notify(config, text: str) -> None:
    """Post to a Discord webhook (election.discord_webhook or ELECTION_DISCORD_WEBHOOK)."""
    url = (config or {}).get("election", {}).get("discord_webhook") or os.environ.get("ELECTION_DISCORD_WEBHOOK")
    if not url:
        return
    try:
        requests.post(url, json={"content": text}, timeout=10)
    except Exception as exc:  # never let a notification break the display
        logger.warning("Election notification failed: %s", exc)


def _worth_notifying(office, s, config) -> bool:
    """Senate/Governor calls, flips, and anything in the home state."""
    home = (config or {}).get("election", {}).get("home_state", "TN")
    return office in ("senate", "governor") or s["flip"] or s["postal"] == home


def track_calls(sums, label_fn, config=None, office=None) -> list:
    """Remember which races are called; return those called in the last 30 min.

    The first run only baselines (existing calls are not 'just called').
    """
    try:
        with open(CALLS_FILE) as f:
            seen = json.load(f)
        baseline = False
    except (OSError, ValueError):
        seen, baseline = {}, True
    now, recent, changed = time.time(), [], False
    for s in sums:
        if not s["called"] or not s["winner"]:
            continue
        if s["id"] not in seen:
            seen[s["id"]] = 0 if baseline else now
            changed = True
            if not baseline and _worth_notifying(office, s, config):
                w = s["winner"]
                notify(config, f"CALLED: {label_fn(s)} - {w['name']} ({w['party'][0]})"
                               f"{' FLIP' if s['flip'] else ''}")
        if now - seen[s["id"]] < RECENT_CALL_SECONDS:
            recent.append((seen[s["id"]], s, label_fn(s)))
    if changed:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CALLS_FILE, "w") as f:
            json.dump(seen, f)
    return [(s, lbl) for _, s, lbl in sorted(recent, key=lambda t: -t[0])]


def flip_text(sums) -> str:
    n = {"DEM": 0, "GOP": 0}
    for s in sums:
        if s["flip"] and s["winner"] and s["winner"]["party"] in n:
            n[s["winner"]["party"]] += 1
    return f"  Flips D+{n['DEM']} R+{n['GOP']}" if any(n.values()) else ""


def banner(d, config, y, recent):
    """Strip announcing the newest race call; returns the y below it."""
    if not recent:
        return y
    s, label = recent[0]
    w = s["winner"]
    col = PARTY_COLOR[w["party"]]
    d.rectangle([14, y, WIDTH - 14, y + 24], fill=col, outline=BLACK, width=2)
    more = f"  (+{len(recent) - 1} more)" if len(recent) > 1 else ""
    txt = f"CALLED  {label}: {w['name']} ({w['party'][0]}){'  FLIP' if s['flip'] else ''}{more}"
    d.text((22, y + 3), txt, fill=WHITE, font=get_font(15, bold=True, config=config))
    return y + 30


def control_winner(data, office_id):
    """Party id ('DEM'/'GOP'/'OTH') that has been projected to control the chamber, else None."""
    for pid, b in control(data, office_id)["party_balance"].items():
        if b.get("winner"):
            return pid
    return None


def balance_text(dem_secured, gop_secured, dem_proj, gop_proj, need_dem, need_gop) -> str:
    """'Proj D 48-R 52 | Dems need 3 | GOP need 0' line under the seat bar."""
    return (f"Proj D {dem_proj}-R {gop_proj}  |  Dems need {max(need_dem - dem_secured, 0)}"
            f"  |  GOP need {max(need_gop - gop_secured, 0)}")


def canvas(config, title: str, subtitle: str = "", stamp: str = None):
    """New white canvas with a colored title bar. Returns (img, draw, y_start)."""
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"  # no anti-aliasing: crisp text, no dithered edge pixels on the panel
    d.rectangle([0, 0, WIDTH, 44], fill=BLACK)
    d.rectangle([0, 44, WIDTH // 2, 48], fill=BLUE)
    d.rectangle([WIDTH // 2, 44, WIDTH, 48], fill=RED)
    d.text((14, 6), title, fill=WHITE, font=get_font(30, bold=True, config=config))
    # The stamp is the *data* time, not render time, so an unchanged feed renders
    # a byte-identical image and main.py skips the slow panel refresh.
    stamp = "Updated " + (stamp or datetime.now().strftime("%b %-d, %-I:%M %p"))
    f = get_font(14, config=config)
    right = f"{subtitle}  {stamp}".strip()
    w = d.textlength(right, font=f)
    d.text((WIDTH - w - 14, 14), right, fill=YELLOW, font=f)
    return img, d, 58


def control_bar(d, config, y, dem, gop, oth, needed, label):
    """Draw a tug-of-war seat bar with majority marker. Returns next y."""
    total = max(dem + gop + oth, 1)
    x0, x1, h = 14, WIDTH - 14, 34
    span = x1 - x0
    x = x0
    for n, col in ((dem, BLUE), (oth, GREEN), (0, WHITE)):
        w = int(span * n / total)
        d.rectangle([x, y, x + w, y + h], fill=col)
        x += w
    wr = int(span * gop / total)
    d.rectangle([x1 - wr, y, x1, y + h], fill=RED)
    d.rectangle([x0, y, x1, y + h], outline=BLACK, width=2)
    mx = x0 + int(span * needed / total)
    d.line([mx, y - 6, mx, y + h + 6], fill=BLACK, width=3)
    big = get_font(40, bold=True, config=config)
    d.text((x0, y + h + 8), str(dem), fill=BLUE, font=big)
    rw = d.textlength(str(gop), font=big)
    d.text((x1 - rw, y + h + 8), str(gop), fill=RED, font=big)
    f = get_font(16, config=config)
    mid = f"{label}  •  {needed} to win"
    d.text(((WIDTH - d.textlength(mid, font=f)) // 2, y + h + 22), mid, fill=BLACK, font=f)
    return y + h + 58


def compact_bar(d, config, y, dem, gop, oth, needed, label):
    """One-line seat bar with the totals at each end. Returns next y."""
    total = max(dem + gop + oth, 1)
    big = get_font(30, bold=True, config=config)
    lw = 70
    x0, x1, h = 14 + lw, WIDTH - 14 - lw, 24
    span = x1 - x0
    wd, wg = int(span * dem / total), int(span * gop / total)
    d.rectangle([x0, y, x0 + wd, y + h], fill=BLUE)
    d.rectangle([x0 + wd, y, x1 - wg, y + h], fill=GREEN if oth else WHITE)
    d.rectangle([x1 - wg, y, x1, y + h], fill=RED)
    d.rectangle([x0, y, x1, y + h], outline=BLACK, width=2)
    mx = x0 + int(span * needed / total)
    d.line([mx, y - 4, mx, y + h + 4], fill=BLACK, width=3)
    d.text((14, y - 6), str(dem), fill=BLUE, font=big)
    d.text((WIDTH - 14 - d.textlength(str(gop), font=big), y - 6), str(gop), fill=RED, font=big)
    f = get_font(13, config=config)
    t = label
    d.text(((WIDTH - d.textlength(t, font=f)) // 2, y + h + 4), t, fill=BLACK, font=f)
    return y + h + 24


FIPS_POSTAL = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY",
}
MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "us_states_albers.json")
_shapes = None


def _load_shapes():
    """Decode the us-atlas TopoJSON (Albers, AK/HI insets) -> {postal: [rings]}."""
    global _shapes
    if _shapes is not None:
        return _shapes
    with open(MAP_FILE) as f:
        topo = json.load(f)
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        pts = []
        for i in idx:
            seg = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(seg[1:] if pts else seg)
        return pts

    shapes = {}
    for g in topo["objects"]["states"]["geometries"]:
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        shapes[FIPS_POSTAL[g["id"]]] = [ring(r) for poly in polys for r in poly[:1]]
    _shapes = shapes
    return shapes


def state_map(d, config, y0, y1, info):
    """Draw the real US state map (Albers) fitted into y0..y1.

    info maps postal -> dict(color, solid, sub). Called states are filled solid,
    leading/rated-only states get a thick colored outline, no-race states stay white.
    """
    shapes = _load_shapes()
    allp = [p for rings in shapes.values() for r in rings for p in r]
    minx, maxx = min(p[0] for p in allp), max(p[0] for p in allp)
    miny, maxy = min(p[1] for p in allp), max(p[1] for p in allp)
    scale = min((WIDTH - 28) / (maxx - minx), (y1 - y0) / (maxy - miny))
    ox = (WIDTH - (maxx - minx) * scale) / 2 - minx * scale
    oy = y0 + ((y1 - y0) - (maxy - miny) * scale) / 2 - miny * scale

    def xf(r):
        return [(x * scale + ox, y * scale + oy) for x, y in r]

    pf = get_font(14, bold=True, config=config)
    sf = get_font(11, config=config)
    labels = []
    for postal, rings in shapes.items():
        e = info.get(postal)
        col = (e or {}).get("color") or BLACK
        solid = bool(e) and e.get("solid", True)
        fill = col if solid else WHITE
        for r in rings:
            pts = xf(r)
            d.polygon(pts, fill=fill)
            d.line(pts + [pts[0]], fill=BLACK, width=1)
            if e and not solid:
                d.line(pts + [pts[0]], fill=col, width=3, joint="curve")
        big = max(rings, key=lambda r: (max(p[0] for p in r) - min(p[0] for p in r)))
        pts = xf(big)
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(p[1] for p in pts) - min(p[1] for p in pts)
        cx = (max(p[0] for p in pts) + min(p[0] for p in pts)) / 2
        cy = (max(p[1] for p in pts) + min(p[1] for p in pts)) / 2
        fg = WHITE if (solid and col in (BLUE, RED, BLACK)) else BLACK
        labels.append((postal, cx, cy, w, h, fg, (e or {}).get("sub", "")))
    for postal, cx, cy, w, h, fg, sub in labels:
        if w < 22 or h < 14:
            continue
        tw = d.textlength(postal, font=pf)
        two = bool(sub) and h >= 30 and w >= 34
        ty0 = cy - (13 if two else 8)
        d.text((cx - tw / 2, ty0), postal, fill=fg, font=pf)
        if two:
            d.text((cx - d.textlength(sub, font=sf) / 2, ty0 + 15), sub, fill=fg, font=sf)


DISTRICT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "us_districts.json")
_districts = None


def _load_districts():
    """120th-Congress district shapes (built by tools/build_district_map.py)."""
    global _districts
    if _districts is None:
        with open(DISTRICT_FILE) as f:
            _districts = json.load(f)
    return _districts


def _stripes(color, size):
    """Diagonal-striped tile (party color on white) for leading-but-uncalled seats."""
    tile = Image.new("RGB", size, WHITE)
    td = ImageDraw.Draw(tile)
    for i in range(-size[1], size[0], 5):
        td.line([(i, size[1]), (i + size[1], 0)], fill=color, width=2)
    return tile


def district_map(img, d, config, x0, y0, x1, y1, info):
    """Draw the 435 congressional districts fitted into the box.

    info maps (postal, seat) -> dict(color, solid). Called seats are solid, seats
    that are only leading/rated are striped, seats without a race stay white.
    State borders are drawn heavier than district borders.
    """
    doc = _load_districts()
    scale = min((x1 - x0) / doc["width"], (y1 - y0) / doc["height"])
    ox = x0 + ((x1 - x0) - doc["width"] * scale) / 2
    oy = y0 + ((y1 - y0) - doc["height"] * scale) / 2

    def pts(flat):
        return [(ox + flat[i] * scale, oy + flat[i + 1] * scale) for i in range(0, len(flat), 2)]

    tiles = {}
    for postal, seat, rings in doc["districts"]:
        e = info.get((postal, 1 if seat == 0 else seat))
        for r in rings:
            poly = pts(r)
            if e and e["color"] and e.get("solid", True):
                d.polygon(poly, fill=e["color"])
            elif e and e["color"]:
                xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                bx0, by0, bx1, by1 = int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1
                if bx1 - bx0 < 1 or by1 - by0 < 1:
                    continue
                mask = Image.new("L", (bx1 - bx0, by1 - by0), 0)
                ImageDraw.Draw(mask).polygon([(x - bx0, y - by0) for x, y in poly], fill=255)
                tile = tiles.setdefault((e["color"], bx1 - bx0, by1 - by0),
                                        _stripes(e["color"], (bx1 - bx0, by1 - by0)))
                img.paste(tile, (bx0, by0), mask)
            else:
                d.polygon(poly, fill=WHITE)
            d.line(poly + [poly[0]], fill=BLACK, width=1)
    for postal, rings in doc["states"].items():
        for r in rings:
            poly = pts(r)
            d.line(poly + [poly[0]], fill=BLACK, width=2, joint="curve")


def view(config) -> str:
    """'map' or 'list'; set per-run by election_cycle, default map."""
    return config.get("_election_view", "map")


def rating_label(rating) -> str:
    return (rating or "unrated").replace("-", " ").replace("rep", "Rep.").replace("dem", "Dem.").title().replace("Tossup", "Toss-up")


def race_list(d, config, y0, y1, rows):
    """Two-column list. rows: dict(color, solid, label, detail)."""
    per_col = max((len(rows) + 1) // 2, 1)
    rh = min((y1 - y0) // per_col, 24)
    cw = (WIDTH - 28) // 2
    lf = get_font(14, bold=True, config=config)
    df = get_font(13, config=config)
    for i, r in enumerate(rows):
        x = 14 + (i // per_col) * cw
        y = y0 + (i % per_col) * rh
        col = r["color"] or BLACK
        d.rectangle([x, y + 2, x + 16, y + rh - 3], fill=col if r.get("solid", True) else WHITE,
                    outline=col, width=2)
        d.text((x + 24, y + 2), r["label"], fill=BLACK, font=lf)
        d.text((x + 80, y + 3), r["detail"][:34], fill=BLACK, font=df)


PANEL = [WHITE, BLACK, GREEN, BLUE, RED, YELLOW, ORANGE]


def legend(d, config, note=None):
    """Footer key: what the colors mean."""
    f = get_font(13, bold=True, config=config)
    items = [(BLUE, "Democrat"), (RED, "Republican"), (YELLOW, "Toss-up"), (GREEN, "Other")]
    x, y = 14, HEIGHT - 22
    for col, label in items:
        d.rectangle([x, y + 2, x + 14, y + 16], fill=col, outline=BLACK)
        d.text((x + 20, y), label, fill=BLACK, font=f)
        x += 30 + int(d.textlength(label, font=f)) + 16
    note = note or "solid = called  |  outline = leading / rated"
    d.text((WIDTH - 14 - d.textlength(note, font=get_font(12, config=config)), y + 2),
           note, fill=BLACK, font=get_font(12, config=config))


def updated_label(d, config, stamp=None):
    """Bottom-right 'Updated' stamp for screens without a legend."""
    f = get_font(12, config=config)
    t = "Updated " + (stamp or datetime.now().strftime("%b %-d, %-I:%M %p"))
    d.text((WIDTH - 14 - d.textlength(t, font=f), HEIGHT - 20), t, fill=BLACK, font=f)


def finish(img, path) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Snap every pixel to the 7 ACeP inks so the panel never has to dither.
    pal = Image.new("P", (1, 1))
    flat = [c for rgb in PANEL for c in rgb]
    pal.putpalette(flat + [0] * (768 - len(flat)))
    img = img.quantize(palette=pal, dither=Image.Dither.NONE).convert("RGB")
    img.save(path)
    return path
