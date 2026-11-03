"""
Congress bills display: recent House roll-call votes (clerk.house.gov XML,
keyless) and the latest floor action on bills (api.congress.gov).

The congress.gov key comes from config congress_bills.api_key or the
CONGRESS_API_KEY env var; it falls back to the rate-limited public DEMO_KEY,
so the bill feed is cached for an hour.
"""

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from modules import election_common as ec
from utils import get_font, get_logger

logger = get_logger("congress_bills")

UA = {"User-Agent": "eink-display/1.0"}
ROLL_URL = "https://clerk.house.gov/evs/{year}/roll{n:03d}.xml"
BILL_URL = "https://api.congress.gov/v3/bill"
CACHE = "data/congress_bills.json"
BILL_TTL = 3600
SKIP_QUESTIONS = ("journal", "adjourn", "quorum", "call of the house")
FLOOR_WORDS = ("passed", "agreed to", "cloture", "presented to president",
               "became public law", "placed on", "vetoed", "failed")


def _roll_exists(year, n):
    r = requests.get(ROLL_URL.format(year=year, n=n), headers=UA, timeout=20)
    # The clerk answers 200 with an "Error sanitizing file" stub for missing rolls.
    return r.status_code == 200 and b"<rollcall-vote" in r.content, r


def _latest_roll(year, hint=0):
    """Highest existing roll number (binary search, starting from a cached hint)."""
    lo = max(hint, 1)
    if not _roll_exists(year, lo)[0]:
        lo = 1
    hi = max(lo * 2, 64)
    while _roll_exists(year, hi)[0]:
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if _roll_exists(year, mid)[0] else (lo, mid)
    return lo


def parse_roll(xml_text):
    """Parse one clerk roll-call XML into a dict, or None for procedural votes."""
    root = ET.fromstring(xml_text)
    m = root.find("vote-metadata")
    g = lambda t: (m.findtext(t) or "").strip()  # noqa: E731
    question = g("vote-question")
    if g("legis-num").upper() == "QUORUM" or any(w in question.lower() for w in SKIP_QUESTIONS):
        return None
    tot = root.find("vote-metadata/vote-totals/totals-by-vote")
    if tot is None:
        tot = root.find(".//totals-by-vote")
    return {
        "num": g("rollcall-num"), "bill": g("legis-num"), "question": question,
        "result": g("vote-result"), "desc": g("vote-desc"), "date": g("action-date"),
        "yea": int(tot.findtext("yea-total") or 0) if tot is not None else 0,
        "nay": int(tot.findtext("nay-total") or 0) if tot is not None else 0,
    }


def fetch_house_votes(year, count=7):
    cache = _read_cache()
    latest = _latest_roll(year, cache.get("latest_roll", 0))
    cache["latest_roll"] = latest
    _write_cache(cache)
    votes, n = [], latest
    while len(votes) < count and n > 0 and latest - n < 40:
        ok, r = _roll_exists(year, n)
        if ok:
            v = parse_roll(r.content)
            if v:
                votes.append(v)
        n -= 1
    return votes


def fetch_bill_actions(config, count=6):
    cfg = config.get("congress_bills", {})
    key = cfg.get("api_key") or os.environ.get("CONGRESS_API_KEY") or "DEMO_KEY"
    cache = _read_cache()
    if cache.get("bills") and time.time() - cache.get("bills_at", 0) < BILL_TTL:
        return cache["bills"]
    try:
        r = requests.get(BILL_URL, headers=UA, timeout=30, params={
            "api_key": key, "format": "json", "limit": 60, "sort": "updateDate+desc"})
        r.raise_for_status()
        out = []
        for b in r.json().get("bills", []):
            act = (b.get("latestAction") or {})
            text = act.get("text", "")
            if any(w in text.lower() for w in FLOOR_WORDS):
                out.append({"bill": f"{b['type']} {b['number']}", "title": b.get("title", ""),
                            "action": text, "date": act.get("actionDate", "")})
        out = out[:count]
        cache.update(bills=out, bills_at=time.time())
        _write_cache(cache)
        return out
    except Exception as exc:
        logger.warning("congress.gov fetch failed: %s", exc)
        return cache.get("bills", [])


def _read_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(c, f)


def _changed_at(votes, bills):
    """Time the displayed content last changed (stable while nothing new arrives)."""
    sig = json.dumps([votes, bills], sort_keys=True)
    cache = _read_cache()
    if cache.get("sig") != sig:
        cache.update(sig=sig, changed_at=datetime.now().strftime("%b %-d, %-I:%M %p"))
        _write_cache(cache)
    return cache["changed_at"]


def _clip(d, text, font, width):
    while text and d.textlength(text, font=font) > width:
        text = text[:-2]
    return text


def generate(config):
    out = config.get("congress_bills", {}).get("output_path", "images/congress_bills.bmp")
    year = datetime.now().year
    try:
        votes = fetch_house_votes(year)
    except Exception as exc:
        logger.warning("House vote fetch failed: %s", exc)
        votes = []
    bills = fetch_bill_actions(config)

    stamp = _changed_at(votes, bills)
    img, d, y = ec.canvas(config, "CONGRESS", "House votes & bills", stamp=stamp)
    bf, tf, sf = (get_font(14, bold=True, config=config), get_font(13, config=config),
                  get_font(12, config=config))
    d.text((14, y - 2), "RECENT HOUSE VOTES", fill=ec.BLACK, font=bf)
    y += 20
    for v in votes:
        passed = v["result"].lower().startswith(("pass", "agreed"))
        col = ec.GREEN if passed else ec.RED
        d.rectangle([14, y + 1, 30, y + 17], fill=col, outline=ec.BLACK)
        d.text((38, y), v["bill"], fill=ec.BLACK, font=bf)
        d.text((130, y + 1), _clip(d, v["desc"] or v["question"], tf, 480), fill=ec.BLACK, font=tf)
        tally = f"{v['yea']}-{v['nay']}"
        d.text((WIDTH_R - d.textlength(tally, font=bf), y), tally, fill=ec.BLACK, font=bf)
        y += 22
    if not votes:
        d.text((14, y), "No House vote data", fill=ec.BLACK, font=tf)
        y += 22
    y += 8
    d.line([14, y, ec.WIDTH - 14, y], fill=ec.BLACK, width=2)
    d.text((14, y + 6), "LATEST FLOOR ACTION ON BILLS", fill=ec.BLACK, font=bf)
    y += 28
    for b in bills:
        d.rectangle([14, y + 1, 30, y + 17], fill=ec.YELLOW, outline=ec.BLACK)
        d.text((38, y), b["bill"], fill=ec.BLACK, font=bf)
        d.text((130, y + 1), _clip(d, f"{b['title']} - {b['action']}", tf, 600), fill=ec.BLACK, font=tf)
        y += 22
        if y > ec.HEIGHT - 26:
            break
    d.text((14, ec.HEIGHT - 20), "Green = passed | Red = failed | Yellow = bill action  "
           "", fill=ec.BLACK, font=sf)
    ec.updated_label(d, config, stamp)
    return ec.finish(img, out)


WIDTH_R = ec.WIDTH - 14
