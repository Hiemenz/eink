"""
Sports Scores Module — E-ink display showing live/recent game scores.

Fetches data from ESPN's unofficial public scoreboard API (no key required)
and renders an 800x480 BMP with a 2-column game card grid.

Config section (in config.yml):

    sports_scores:
      output_path: images/sports_display.bmp
      sport: football
      league: nfl
      cache_ttl_seconds: 300
      max_games: 6           # max games to display (up to 6 fit in 2-column grid)
      cache_dir: data/

ESPN API URL template:
  https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageDraw

# Ensure project root is importable when run directly (e.g. poetry run python modules/sports_scores.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("sports_scores")

# ── Layout constants ──────────────────────────────────────────────────────────
WIDTH = 800
HEIGHT = 480
HEADER_H = 36
CARD_ROWS = 3
CARD_COLS = 2
CARD_W = WIDTH // CARD_COLS          # 400
CARD_H = (HEIGHT - HEADER_H) // CARD_ROWS  # 148

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (160, 160, 160)
LIGHT_GRAY = (220, 220, 220)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}


# ── Data layer ────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str, league: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"sports_cache_{league}.json")


def _load_cache(cache_dir: str, league: str, ttl: int) -> Optional[List[Dict]]:
    path = _cache_path(cache_dir, league)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        age = time.time() - data.get("fetched_at", 0)
        if age < ttl:
            logger.info("Sports cache hit for %s (age %.0fs)", league, age)
            return data["games"]
        logger.info("Sports cache expired for %s (age %.0fs)", league, age)
    except Exception as e:
        logger.warning("Cache read error for %s: %s", league, e)
    return None


def _save_cache(cache_dir: str, league: str, games: List[Dict]) -> None:
    path = _cache_path(cache_dir, league)
    try:
        with open(path, "w") as f:
            json.dump({"fetched_at": time.time(), "games": games}, f)
        logger.info("Sports cache saved: %s", path)
    except Exception as e:
        logger.warning("Cache write error: %s", e)


def _parse_events(events: List[Dict]) -> List[Dict]:
    """Parse ESPN scoreboard events into simplified game dicts."""
    games = []
    for event in events:
        try:
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])

            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)

            if not home or not away:
                continue

            home_abbr = home.get("team", {}).get("abbreviation", "?")
            away_abbr = away.get("team", {}).get("abbreviation", "?")
            home_score = home.get("score", "")
            away_score = away.get("score", "")
            status = event.get("status", {}).get("type", {}).get("shortDetail", "")

            games.append({
                "home": home_abbr,
                "away": away_abbr,
                "home_score": str(home_score) if home_score != "" else "",
                "away_score": str(away_score) if away_score != "" else "",
                "status": status,
            })
        except Exception as e:
            logger.debug("Skipping malformed event: %s", e)
    return games


def fetch_games(sport: str, league: str, cache_dir: str, ttl: int) -> Optional[List[Dict]]:
    """Fetch today's games from ESPN, with local cache and graceful fallback."""
    cached = _load_cache(cache_dir, league, ttl)
    if cached is not None:
        return cached

    url = ESPN_BASE.format(sport=sport, league=league)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("ESPN fetch failed for %s/%s: %s", sport, league, e)
        # Return stale cache even if expired, rather than nothing
        stale = _load_cache(cache_dir, league, ttl=999_999)
        if stale is not None:
            logger.warning("Returning stale cache for %s", league)
        return stale

    events = data.get("events", [])
    games = _parse_events(events)
    _save_cache(cache_dir, league, games)
    logger.info("Fetched %d games for %s", len(games), league.upper())
    return games


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _draw_header(draw: ImageDraw.ImageDraw, league: str, config: dict) -> None:
    """Draw the full-width black header bar with league name and today's date."""
    draw.rectangle([(0, 0), (WIDTH - 1, HEADER_H - 1)], fill=BLACK)

    league_label = league.upper() + " Scores"
    date_label = datetime.now().strftime("%a %b %-d")

    font_bold = get_font(18, bold=True, config=config)
    font_date = get_font(16, config=config)

    # League name — left-aligned with small margin
    draw.text((10, (HEADER_H - 18) // 2), league_label, fill=WHITE, font=font_bold)

    # Date — right-aligned
    bbox = draw.textbbox((0, 0), date_label, font=font_date)
    date_w = bbox[2] - bbox[0]
    draw.text((WIDTH - date_w - 10, (HEADER_H - 16) // 2), date_label, fill=WHITE, font=font_date)


def _draw_game_card(draw: ImageDraw.ImageDraw, game: Dict, card_x: int, card_y: int, config: dict) -> None:
    """Draw a single game card in the grid cell."""
    x0, y0 = card_x, card_y
    x1, y1 = card_x + CARD_W - 1, card_y + CARD_H - 1

    # Card border
    draw.rectangle([(x0, y0), (x1, y1)], outline=LIGHT_GRAY, width=1)

    inner_w = CARD_W - 4  # inside border margin
    cx = x0 + CARD_W // 2  # horizontal center

    # Fonts
    team_font = get_font(26, bold=True, config=config)
    score_font = get_font(26, config=config)
    sep_font = get_font(14, config=config)
    status_font = get_font(12, config=config)

    home = game["home"]
    away = game["away"]
    home_score = game["home_score"]
    away_score = game["away_score"]
    status = game["status"]

    has_scores = bool(home_score or away_score)

    # ── Vertical layout ────────────────────────────────────────────────────
    # Allocate: top row (away), middle sep, bottom row (home), status line
    status_h = 20
    sep_h = 18
    team_h = (CARD_H - status_h - sep_h - 8) // 2  # ~split remaining in two

    away_y = y0 + 6
    sep_y = away_y + team_h
    home_y = sep_y + sep_h
    status_y = y1 - status_h - 2

    def _draw_team_row(team: str, score: str, row_y: int, align: str) -> None:
        """Draw team abbreviation + score on the same row."""
        # Team label width
        tb = draw.textbbox((0, 0), team, font=team_font)
        team_w = tb[2] - tb[0]
        team_h_px = tb[3] - tb[1]

        if score and has_scores:
            sb = draw.textbbox((0, 0), score, font=score_font)
            score_w = sb[2] - sb[0]
            gap = 8
            total_w = team_w + gap + score_w
        else:
            total_w = team_w
            score_w = 0
            gap = 0

        # Clamp to inner width
        pad = 12
        if align == "left":
            start_x = x0 + pad
        else:
            start_x = x1 - pad - total_w

        # Vertically center within the row band
        text_y = row_y + (team_h - team_h_px) // 2

        draw.text((start_x, text_y), team, fill=BLACK, font=team_font)
        if score and has_scores:
            draw.text((start_x + team_w + gap, text_y), score, fill=BLACK, font=score_font)

    # Away team (left-aligned)
    _draw_team_row(away, away_score, away_y, align="left")

    # Separator ("@" pre-game, "vs" once started/final)
    if has_scores:
        sep_text = "vs"
    else:
        sep_text = "@"
    sb = draw.textbbox((0, 0), sep_text, font=sep_font)
    sep_w = sb[2] - sb[0]
    sep_h_px = sb[3] - sb[1]
    draw.text(
        (cx - sep_w // 2, sep_y + (sep_h - sep_h_px) // 2),
        sep_text, fill=GRAY, font=sep_font,
    )

    # Home team (right-aligned)
    _draw_team_row(home, home_score, home_y, align="right")

    # Status line — small gray centered text at bottom
    if status:
        # Truncate if needed
        trunc = status
        while trunc:
            bb = draw.textbbox((0, 0), trunc, font=status_font)
            if (bb[2] - bb[0]) <= inner_w - 8:
                break
            trunc = trunc[:-1]
        bb = draw.textbbox((0, 0), trunc, font=status_font)
        st_w = bb[2] - bb[0]
        st_h = bb[3] - bb[1]
        draw.text(
            (cx - st_w // 2, status_y + (status_h - st_h) // 2),
            trunc, fill=GRAY, font=status_font,
        )


def _draw_no_games(draw: ImageDraw.ImageDraw, league: str, config: dict) -> None:
    """Draw a centered 'No games scheduled' message in the content area."""
    msg_font = get_font(22, bold=True, config=config)
    sub_font = get_font(16, config=config)

    msg = "No games scheduled"
    sub = f"for {league.upper()} today"

    mb = draw.textbbox((0, 0), msg, font=msg_font)
    sb = draw.textbbox((0, 0), sub, font=sub_font)

    total_h = (mb[3] - mb[1]) + 12 + (sb[3] - sb[1])
    content_center_y = HEADER_H + (HEIGHT - HEADER_H) // 2

    msg_x = (WIDTH - (mb[2] - mb[0])) // 2
    msg_y = content_center_y - total_h // 2
    draw.text((msg_x, msg_y), msg, fill=BLACK, font=msg_font)

    sub_x = (WIDTH - (sb[2] - sb[0])) // 2
    sub_y = msg_y + (mb[3] - mb[1]) + 12
    draw.text((sub_x, sub_y), sub, fill=GRAY, font=sub_font)


def _draw_unavailable(draw: ImageDraw.ImageDraw, config: dict) -> None:
    """Draw error state when data cannot be fetched."""
    font = get_font(22, bold=True, config=config)
    msg = "Scores unavailable"
    mb = draw.textbbox((0, 0), msg, font=font)
    draw.text(
        ((WIDTH - (mb[2] - mb[0])) // 2, HEADER_H + (HEIGHT - HEADER_H) // 2 - (mb[3] - mb[1]) // 2),
        msg, fill=BLACK, font=font,
    )


def _draw_overflow_notice(draw: ImageDraw.ImageDraw, n_more: int, config: dict) -> None:
    """Draw '...and N more' notice at the very bottom of the last card row."""
    font = get_font(13, config=config)
    msg = f"...and {n_more} more"
    bb = draw.textbbox((0, 0), msg, font=font)
    x = WIDTH - (bb[2] - bb[0]) - 12
    y = HEIGHT - (bb[3] - bb[1]) - 4
    draw.text((x, y), msg, fill=GRAY, font=font)


# ── Public API ────────────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """Generate the sports scores display image.

    Reads ``config["sports_scores"]`` for module settings, fetches today's
    games from the ESPN public API (cached), and renders an 800x480 BMP.

    Returns the path to the output BMP file.
    """
    cfg = config.get("sports_scores", {})
    output_path = cfg.get("output_path", "images/sports_display.bmp")
    sport = cfg.get("sport", "football")
    league = cfg.get("league", "nfl")
    ttl = int(cfg.get("cache_ttl_seconds", 300))
    max_games = int(cfg.get("max_games", 6))
    cache_dir = cfg.get("cache_dir", "data/")

    logger.info("Generating sports scores display: %s/%s", sport, league)

    # Ensure output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Create canvas
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    # Header always drawn (even on error/empty)
    _draw_header(draw, league, config)

    # Fetch games
    games = fetch_games(sport, league, cache_dir, ttl)

    if games is None:
        # Total failure: no live data and no cache at all
        _draw_unavailable(draw, config)
        img.save(output_path)
        logger.info("Saved sports display (unavailable) to %s", output_path)
        return output_path

    if not games:
        _draw_no_games(draw, league, config)
        img.save(output_path)
        logger.info("Saved sports display (no games) to %s", output_path)
        return output_path

    # Determine how many games to show
    max_slots = CARD_ROWS * CARD_COLS  # 6
    display_games = games[:min(max_games, max_slots)]
    n_more = len(games) - len(display_games)

    # Draw game cards in a 2-column grid
    for i, game in enumerate(display_games):
        col = i % CARD_COLS
        row = i // CARD_COLS
        card_x = col * CARD_W
        card_y = HEADER_H + row * CARD_H
        _draw_game_card(draw, game, card_x, card_y, config)

    # If there were more games than fit, show overflow notice
    if n_more > 0:
        _draw_overflow_notice(draw, n_more, config)

    img.save(output_path)
    logger.info("Saved sports display (%d games) to %s", len(display_games), output_path)
    return output_path


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    # Ensure project root is on the path so utils/eink_generator import cleanly
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from eink_generator import load_config

    config = load_config("config.yml")

    # Allow quick sport/league override via argv: python sports_scores.py basketball nba
    if len(sys.argv) >= 3:
        if "sports_scores" not in config:
            config["sports_scores"] = {}
        config["sports_scores"]["sport"] = sys.argv[1]
        config["sports_scores"]["league"] = sys.argv[2]

    out = generate(config)
    print(f"Generated: {out}")
