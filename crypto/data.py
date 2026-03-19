"""
Fetch cryptocurrency market data from CoinGecko (free, no API key).

Provides:
  - Top 10 coins by market cap with current prices
  - Historical daily prices (250+ days) for MA calculations
  - Price changes over 1d, 2d, 3d, 1w, 2w timeframes
  - JSON file caching with configurable TTL
"""

import json
import os
import time
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils import get_logger

logger = get_logger("crypto.data")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_FILE = os.path.join(CACHE_DIR, "crypto_cache.json")
CACHE_TTL = 6 * 3600  # 6 hours

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Rate-limit-aware session with retry
_session = requests.Session()
_retries = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503])
_session.mount("https://", HTTPAdapter(max_retries=_retries))


def _load_cache() -> dict | None:
    """Load cached data if it exists and is fresh."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) < CACHE_TTL:
            logger.info("Using cached crypto data (age: %.0f min)", (time.time() - data["timestamp"]) / 60)
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cache(data: dict) -> None:
    """Write data to cache file."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    data["timestamp"] = time.time()
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_top_coins(limit: int = 10) -> list[dict]:
    """Fetch top coins by market cap from CoinGecko.

    Returns list of dicts with keys: id, symbol, name, current_price, market_cap_rank.
    """
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": limit,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    resp = _session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    coins = resp.json()

    return [
        {
            "id": c["id"],
            "symbol": c["symbol"].upper(),
            "name": c["name"],
            "current_price": c["current_price"],
            "market_cap_rank": c["market_cap_rank"],
        }
        for c in coins
    ]


def fetch_historical_prices(coin_id: str, days: int = 250) -> list[float]:
    """Fetch daily closing prices for a coin (most recent last).

    Returns a list of floats (USD prices), one per day.
    """
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {
        "vs_currency": "usd",
        "days": days,
        "interval": "daily",
    }
    resp = _session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    prices = resp.json().get("prices", [])

    # Each entry is [timestamp_ms, price]
    return [p[1] for p in prices]


def fetch_all_market_data(limit: int = 10) -> dict:
    """Fetch and cache all data needed for the crypto display.

    Returns dict with:
      - coins: list of coin info dicts
      - historical: {coin_id: [prices]}
      - timestamp: fetch time
    """
    cached = _load_cache()
    if cached and "coins" in cached and "historical" in cached:
        return cached

    logger.info("Fetching fresh crypto data from CoinGecko...")

    coins = fetch_top_coins(limit)
    historical = {}

    for i, coin in enumerate(coins):
        coin_id = coin["id"]
        try:
            if i > 0:
                time.sleep(1.5)  # respect CoinGecko free-tier rate limit
            prices = fetch_historical_prices(coin_id, days=250)
            historical[coin_id] = prices
            logger.info("  %s: %d daily prices fetched", coin["symbol"], len(prices))
        except Exception as e:
            logger.warning("  %s: failed to fetch history — %s", coin["symbol"], e)
            historical[coin_id] = []

    data = {
        "coins": coins,
        "historical": historical,
        "fetch_time": datetime.now().isoformat(),
    }
    _save_cache(data)
    return data
