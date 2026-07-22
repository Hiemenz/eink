"""
Stocks Module — E-ink stock watchlist for a Waveshare display.

The equities sibling of modules/crypto_market.py. Renders a table of watchlist
tickers with last price, change, and percent change using colored ▲/▼ triangles.

800×480 pixels, output as BMP.

Data source (keyless — Yahoo Finance quote endpoint):
    https://query1.finance.yahoo.com/v7/finance/quote?symbols=AAPL,MSFT,GOOGL
    A browser-like User-Agent header is required or Yahoo returns 401.
    On 401/429 from query1, retries once against query2.finance.yahoo.com.
    Quotes are cached for 5 minutes in <cache_dir>/stocks_cache.json and the
    stale cache is reused if a live fetch fails.

Config section (add to config.yml):

    stocks:
      output_path: images/stocks_display.bmp
      symbols: [AAPL, MSFT, GOOGL, NVDA, TSLA, SPY]
      cache_dir: data/

If no symbols are configured, defaults to
[AAPL, MSFT, GOOGL, NVDA, TSLA, SPY].
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests
from PIL import Image, ImageDraw

# Allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("stocks")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
BG = "white"
FG = (0, 0, 0)
GRAY = (140, 140, 140)
ROW_SHADE = (244, 244, 244)
GREEN = (0, 150, 0)
RED = (200, 0, 0)

HEADER_H = 40
FOOTER_H = 26
CACHE_TTL = 300  # 5 minutes
CACHE_FILE = "stocks_cache.json"

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "SPY"]

YF_HOSTS = [
    "https://query1.finance.yahoo.com/v7/finance/quote",
    "https://query2.finance.yahoo.com/v7/finance/quote",
]
YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Column layout: (x_start, width). Symbol | Name | Price | Change
COL_SYMBOL = (12, 90)
COL_NAME = (102, 340)
COL_PRICE = (442, 150)   # right edge at 592
COL_CHANGE = (600, 190)  # right edge at 790


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, CACHE_FILE)


def _load_cache(cache_dir: str) -> Optional[dict]:
    path = _cache_path(cache_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read stocks cache: %s", e)
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning("Could not write stocks cache: %s", e)


# ── Data fetch ───────────────────────────────────────────────────────────────

def _fetch_quotes(symbols: List[str]) -> Optional[List[dict]]:
    """Fetch quotes from Yahoo Finance. Returns the raw result list or None."""
    params = {"symbols": ",".join(symbols)}
    for host in YF_HOSTS:
        try:
            resp = requests.get(host, params=params, headers=YF_HEADERS, timeout=10)
            if resp.status_code in (401, 429):
                logger.warning("Yahoo %s returned %d — trying next host",
                               host, resp.status_code)
                continue
            resp.raise_for_status()
            result = resp.json().get("quoteResponse", {}).get("result", [])
            if result:
                logger.info("Fetched %d quotes from %s", len(result), host)
                return result
            logger.warning("Yahoo %s returned empty result", host)
        except Exception as e:
            logger.warning("Yahoo fetch from %s failed: %s", host, e)
            continue
    return None


def _normalize(result: List[dict]) -> Dict[str, dict]:
    """Map a Yahoo result list into {SYMBOL: quote-fields}."""
    out: Dict[str, dict] = {}
    for q in result:
        sym = q.get("symbol")
        if not sym:
            continue
        out[sym.upper()] = {
            "symbol": sym.upper(),
            "name": q.get("shortName") or q.get("longName") or "",
            "price": q.get("regularMarketPrice"),
            "change": q.get("regularMarketChange"),
            "change_pct": q.get("regularMarketChangePercent"),
            "prev_close": q.get("regularMarketPreviousClose"),
            "market_state": q.get("marketState", ""),
        }
    return out


def _get_data(symbols: List[str], cache_dir: str) -> Dict[str, dict]:
    """Return {SYMBOL: quote} using a 5-min cache with stale fallback.

    Returns an empty dict only when there is neither live data nor any cache.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache = _load_cache(cache_dir)

    if cache and (time.time() - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached stock data (age: %.0fs)",
                    time.time() - cache["fetched_at"])
        return cache.get("quotes", {})

    result = _fetch_quotes(symbols)
    if result:
        quotes = _normalize(result)
        _save_cache(cache_dir, {"fetched_at": time.time(), "quotes": quotes})
        return quotes

    if cache:
        age_min = (time.time() - cache.get("fetched_at", 0)) / 60
        logger.warning("Live stock fetch failed — using stale cache (%.0f min old)",
                       age_min)
        return cache.get("quotes", {})

    return {}


# ── Formatting helpers ───────────────────────────────────────────────────────

def _fmt_price(price) -> str:
    if price is None:
        return "—"
    if price >= 1000:
        return f"{price:,.2f}"
    return f"{price:.2f}"


def _fmt_change(change) -> str:
    if change is None:
        return "—"
    return f"{change:+.2f}"


def _fmt_pct(pct) -> str:
    if pct is None:
        return "—"
    return f"{pct:+.2f}%"


def _truncate(draw, text: str, font, max_w: int) -> str:
    """Truncate text with an ellipsis so it fits within max_w pixels."""
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    while text and draw.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return text + ell


# ── Drawing ──────────────────────────────────────────────────────────────────

def _draw_triangle(draw, cx: int, cy: int, up: bool, color, size: int = 6) -> None:
    """Draw a small solid up/down triangle centered at (cx, cy)."""
    h = size
    w = size
    if up:
        pts = [(cx, cy - h), (cx - w, cy + h), (cx + w, cy + h)]
    else:
        pts = [(cx, cy + h), (cx - w, cy - h), (cx + w, cy - h)]
    draw.polygon(pts, fill=color)


def _draw_header(draw, market_state: str, config: dict) -> None:
    draw.rectangle([(0, 0), (WIDTH, HEADER_H)], fill=FG)
    title_font = get_font(22, bold=True, config=config)
    draw.text((14, (HEADER_H - 24) // 2), "Watchlist", fill="white", font=title_font)

    state = "OPEN" if str(market_state).upper() in ("REGULAR", "OPEN") else "CLOSED"
    now = datetime.now().strftime("%b %d  %H:%M")
    right_text = f"{state}   {now}"
    info_font = get_font(15, config=config)
    tw = draw.textlength(right_text, font=info_font)
    draw.text((WIDTH - tw - 14, (HEADER_H - 17) // 2), right_text,
              fill="white", font=info_font)


def _draw_footer(draw, config: dict) -> None:
    y = HEIGHT - FOOTER_H
    draw.line([(0, y), (WIDTH, y)], fill=(210, 210, 210), width=1)
    font = get_font(12, config=config)
    stamp = datetime.now().strftime("%H:%M:%S")
    left = "Yahoo Finance"
    draw.text((12, y + 6), left, fill=GRAY, font=font)
    right = f"Updated {stamp}"
    tw = draw.textlength(right, font=font)
    draw.text((WIDTH - tw - 12, y + 6), right, fill=GRAY, font=font)


def _draw_row(draw, y: int, row_h: int, quote: dict, config: dict, shaded: bool) -> None:
    if shaded:
        draw.rectangle([(0, y), (WIDTH, y + row_h)], fill=ROW_SHADE)

    sym_font = get_font(19, bold=True, config=config)
    name_font = get_font(14, config=config)
    price_font = get_font(19, bold=True, config=config)
    change_font = get_font(15, bold=True, config=config)

    text_y = y + (row_h - 20) // 2

    # Symbol (bold, left)
    x, _ = COL_SYMBOL
    draw.text((x, text_y), quote["symbol"], fill=FG, font=sym_font)

    # Company name (gray, truncated)
    x, w = COL_NAME
    name = _truncate(draw, quote.get("name", ""), name_font, w)
    draw.text((x, y + (row_h - 16) // 2), name, fill=GRAY, font=name_font)

    # Price (right-aligned, bold)
    x, w = COL_PRICE
    price_str = _fmt_price(quote.get("price"))
    pw = draw.textlength(price_str, font=price_font)
    draw.text((x + w - pw, text_y), price_str, fill=FG, font=price_font)

    # Change + percent (colored, with triangle)
    x, w = COL_CHANGE
    change = quote.get("change")
    pct = quote.get("change_pct")
    if change is None or pct is None:
        text = "—"
        tw = draw.textlength(text, font=change_font)
        draw.text((x + w - tw, text_y), text, fill=GRAY, font=change_font)
    else:
        up = change >= 0
        color = GREEN if up else RED
        combo = f"{_fmt_change(change)}  {_fmt_pct(pct)}"
        tw = draw.textlength(combo, font=change_font)
        # Triangle sits to the left of the text block, right edge aligned.
        tri_gap = 16
        text_x = x + w - tw
        draw.text((text_x, text_y), combo, fill=color, font=change_font)
        tri_cx = text_x - tri_gap + 6
        _draw_triangle(draw, tri_cx, text_y + 10, up, color, size=5)


def _draw_unavailable(img: Image.Image, config: dict) -> None:
    draw = ImageDraw.Draw(img)
    font = get_font(34, bold=True, config=config)
    msg = "Market data unavailable"
    bb = draw.textbbox((0, 0), msg, font=font)
    x = (WIDTH - (bb[2] - bb[0])) // 2
    yy = (HEIGHT - (bb[3] - bb[1])) // 2
    draw.text((x, yy), msg, fill=GRAY, font=font)


# ── Main generate() ──────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """Generate the stock watchlist display and return the output BMP path."""
    cfg = config.get("stocks", {})
    output_path = cfg.get("output_path", "images/stocks_display.bmp")
    symbols = cfg.get("symbols") or DEFAULT_SYMBOLS
    symbols = [str(s).upper() for s in symbols][:10]
    cache_dir = cfg.get("cache_dir", "data/")

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
                exist_ok=True)

    logger.info("Generating stocks display for %s", ", ".join(symbols))

    quotes = _get_data(symbols, cache_dir)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)

    if not quotes:
        logger.warning("No stock data available (no live data, no cache)")
        _draw_unavailable(img, config)
        img.save(output_path)
        logger.info("Saved stocks display (unavailable) to %s", output_path)
        return output_path

    draw = ImageDraw.Draw(img)

    # Market state: use the first resolved symbol that has one.
    market_state = next((q.get("market_state") for q in quotes.values()
                         if q.get("market_state")), "")
    _draw_header(draw, market_state, config)
    _draw_footer(draw, config)

    # Build display rows in configured order; unresolved symbols show placeholders.
    rows = []
    for sym in symbols:
        if sym in quotes:
            rows.append(quotes[sym])
        else:
            rows.append({"symbol": sym, "name": "", "price": None,
                         "change": None, "change_pct": None})

    top = HEADER_H
    available = HEIGHT - HEADER_H - FOOTER_H
    row_h = available // max(len(rows), 1)

    for i, quote in enumerate(rows):
        row_y = top + i * row_h
        _draw_row(draw, row_y, row_h, quote, config, shaded=(i % 2 == 1))
        # Thin separator between rows
        draw.line([(0, row_y + row_h), (WIDTH, row_y + row_h)],
                  fill=(225, 225, 225), width=1)

    img.save(output_path)
    logger.info("Saved stocks display to %s", output_path)
    return output_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml"
    )
    try:
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}

    result = generate(cfg)
    print(f"Generated: {result}")
