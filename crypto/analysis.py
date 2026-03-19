"""
Crypto market analysis — moving averages, trend detection, and trade signals.

Logic:
  - Bull/Bear per timeframe: price went up or down over that period
  - 50-day / 200-day MA: golden cross (50 > 200) = bullish, death cross = bearish
  - Buy/Sell/Hold: weighted scoring combining all signals
"""

from utils import get_logger

logger = get_logger("crypto.analysis")

# Timeframes as (label, days_back)
TIMEFRAMES = [
    ("1D", 1),
    ("2D", 2),
    ("3D", 3),
    ("1W", 7),
    ("2W", 14),
]


def moving_average(prices: list[float], window: int) -> float | None:
    """Calculate simple moving average over the last `window` prices."""
    if len(prices) < window:
        return None
    return sum(prices[-window:]) / window


def price_change_pct(prices: list[float], days_back: int) -> float | None:
    """Percentage change from `days_back` ago to most recent price."""
    if len(prices) < days_back + 1:
        return None
    old = prices[-(days_back + 1)]
    new = prices[-1]
    if old == 0:
        return None
    return ((new - old) / old) * 100


def is_bullish_timeframe(prices: list[float], days_back: int) -> bool | None:
    """True if price is up over the timeframe, False if down, None if insufficient data."""
    pct = price_change_pct(prices, days_back)
    if pct is None:
        return None
    return pct > 0


def ma_signal(prices: list[float]) -> str:
    """Return MA crossover signal based on 50-day and 200-day moving averages.

    Returns: 'GOLDEN' (50 > 200), 'DEATH' (50 < 200), or 'N/A'.
    """
    ma50 = moving_average(prices, 50)
    ma200 = moving_average(prices, 200)

    if ma50 is None or ma200 is None:
        return "N/A"
    if ma50 > ma200:
        return "GOLDEN"
    return "DEATH"


def compute_signal(prices: list[float]) -> str:
    """Compute Buy/Sell/Hold signal using weighted scoring.

    Scoring:
      - Each bullish timeframe: +1 point
      - Each bearish timeframe: -1 point
      - Golden cross (50MA > 200MA): +3 points
      - Death cross (50MA < 200MA): -3 points

    Thresholds:
      - Score >= 3: BUY
      - Score <= -3: SELL
      - Otherwise: HOLD
    """
    score = 0

    # Timeframe trends
    for _, days_back in TIMEFRAMES:
        bull = is_bullish_timeframe(prices, days_back)
        if bull is True:
            score += 1
        elif bull is False:
            score -= 1

    # MA crossover (heavier weight)
    ma = ma_signal(prices)
    if ma == "GOLDEN":
        score += 3
    elif ma == "DEATH":
        score -= 3

    if score >= 3:
        return "BUY"
    elif score <= -3:
        return "SELL"
    return "HOLD"


def analyze_coin(coin: dict, prices: list[float]) -> dict:
    """Run full analysis on a single coin.

    Returns dict with:
      - symbol, name, current_price, market_cap_rank
      - timeframes: {label: {bullish: bool, pct: float}}
      - ma_signal: GOLDEN / DEATH / N/A
      - signal: BUY / SELL / HOLD
      - ma50, ma200: float values
    """
    result = {
        "symbol": coin["symbol"],
        "name": coin["name"],
        "current_price": coin["current_price"],
        "market_cap_rank": coin.get("market_cap_rank", "?"),
        "timeframes": {},
        "ma_signal": ma_signal(prices),
        "signal": compute_signal(prices),
        "ma50": moving_average(prices, 50),
        "ma200": moving_average(prices, 200),
    }

    for label, days_back in TIMEFRAMES:
        bull = is_bullish_timeframe(prices, days_back)
        pct = price_change_pct(prices, days_back)
        result["timeframes"][label] = {
            "bullish": bull,
            "pct": pct if pct is not None else 0.0,
        }

    return result


def analyze_all(market_data: dict) -> list[dict]:
    """Analyze all coins from the fetched market data.

    Args:
        market_data: dict from data.fetch_all_market_data()

    Returns:
        List of analysis dicts, one per coin, sorted by market cap rank.
    """
    results = []
    coins = market_data.get("coins", [])
    historical = market_data.get("historical", {})

    for coin in coins:
        prices = historical.get(coin["id"], [])
        if not prices:
            logger.warning("No price data for %s — skipping", coin["symbol"])
            continue
        results.append(analyze_coin(coin, prices))

    results.sort(key=lambda x: x.get("market_cap_rank", 999))
    return results
