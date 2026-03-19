"""
Crypto Market Module — E-ink display grid showing top 10 cryptocurrencies
with trend indicators, moving average signals, and buy/sell/hold recommendations.

800x480 black-and-white grid layout.
"""

import os
import sys
from datetime import datetime

from PIL import Image, ImageDraw

# Add project root to path so crypto package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto.analysis import TIMEFRAMES, analyze_all
from crypto.data import fetch_all_market_data
from utils import get_font, get_logger

logger = get_logger("crypto_market")

# Layout constants
WIDTH = 800
HEIGHT = 480
BG = "white"
FG = "black"
GRAY = (160, 160, 160)
LIGHT_GRAY = (200, 200, 200)

# Column definitions: (header, x_start, width)
# | # | COIN | PRICE | 1D | 2D | 3D | 1W | 2W | MA | SIGNAL |
COL_RANK = (0, 25)
COL_COIN = (25, 55)
COL_PRICE = (80, 100)
COL_1D = (180, 70)
COL_2D = (250, 70)
COL_3D = (320, 70)
COL_1W = (390, 70)
COL_2W = (460, 70)
COL_MA = (530, 105)
COL_SIGNAL = (635, 165)

MAX_SYMBOL_LEN = 5  # truncate long symbols

COLUMNS = [
    ("#",      COL_RANK),
    ("COIN",   COL_COIN),
    ("PRICE",  COL_PRICE),
    ("1D",     COL_1D),
    ("2D",     COL_2D),
    ("3D",     COL_3D),
    ("1W",     COL_1W),
    ("2W",     COL_2W),
    ("50/200", COL_MA),
    ("SIGNAL", COL_SIGNAL),
]


def _format_price(price: float) -> str:
    """Format price for display — compact for large/small values."""
    if price >= 10000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:,.2f}"
    elif price >= 0.01:
        return f"${price:.3f}"
    else:
        return f"${price:.5f}"


def _trend_symbol(bullish: bool | None) -> str:
    """Return arrow symbol for trend direction."""
    if bullish is True:
        return "\u25b2"  # ▲
    elif bullish is False:
        return "\u25bc"  # ▼
    return "—"


def _pct_str(pct: float) -> str:
    """Format percentage change."""
    if abs(pct) >= 10:
        return f"{pct:+.0f}%"
    return f"{pct:+.1f}%"


def _ma_label(ma_sig: str) -> str:
    """Return compact MA signal label."""
    if ma_sig == "GOLDEN":
        return "GOLDEN"
    elif ma_sig == "DEATH":
        return "DEATH"
    return "N/A"


def _signal_label(signal: str) -> str:
    """Return signal text with indicator."""
    if signal == "BUY":
        return ">> BUY"
    elif signal == "SELL":
        return "<< SELL"
    return "-- HOLD"


def _draw_header(draw: ImageDraw.Draw, y: int, font, bold_font) -> int:
    """Draw the title bar and column headers. Returns y after header."""
    # Title
    title = "CRYPTO MARKET"
    draw.text((10, 4), title, fill=FG, font=bold_font)

    # Timestamp
    now = datetime.now().strftime("%b %d  %H:%M")
    ts_font = get_font(12)
    bbox = draw.textbbox((0, 0), now, font=ts_font)
    draw.text((WIDTH - (bbox[2] - bbox[0]) - 10, 8), now, fill=GRAY, font=ts_font)

    # Separator line under title
    title_bottom = 28
    draw.line([(0, title_bottom), (WIDTH, title_bottom)], fill=FG, width=2)

    # Column headers
    header_y = title_bottom + 4
    for label, (x, w) in COLUMNS:
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x + (w - text_w) // 2, header_y), label, fill=FG, font=font)

    header_bottom = header_y + 18
    draw.line([(0, header_bottom), (WIDTH, header_bottom)], fill=FG, width=1)

    return header_bottom + 2


def _draw_row(draw: ImageDraw.Draw, y: int, row_h: int, coin: dict, font, bold_font, small_font) -> None:
    """Draw a single coin row in the grid."""
    center_y = y + (row_h - 16) // 2  # vertically center text

    # Rank
    rank = str(coin["market_cap_rank"])
    x, w = COL_RANK
    bbox = draw.textbbox((0, 0), rank, font=small_font)
    draw.text((x + (w - (bbox[2] - bbox[0])) // 2, center_y), rank, fill=GRAY, font=small_font)

    # Symbol (truncate long names)
    x, w = COL_COIN
    symbol = coin["symbol"][:MAX_SYMBOL_LEN]
    draw.text((x + 2, center_y), symbol, fill=FG, font=bold_font)

    # Price
    x, w = COL_PRICE
    price_str = _format_price(coin["current_price"])
    draw.text((x + 2, center_y), price_str, fill=FG, font=font)

    # Timeframe columns (1D through 2W)
    tf_cols = [COL_1D, COL_2D, COL_3D, COL_1W, COL_2W]
    tf_labels = ["1D", "2D", "3D", "1W", "2W"]
    for (x, w), label in zip(tf_cols, tf_labels):
        tf_data = coin["timeframes"].get(label, {})
        bullish = tf_data.get("bullish")
        pct = tf_data.get("pct", 0.0)

        # Arrow
        arrow = _trend_symbol(bullish)
        arrow_bbox = draw.textbbox((0, 0), arrow, font=font)
        arrow_w = arrow_bbox[2] - arrow_bbox[0]

        # Pct text
        pct_text = _pct_str(pct)
        pct_bbox = draw.textbbox((0, 0), pct_text, font=small_font)
        pct_w = pct_bbox[2] - pct_bbox[0]

        total_w = arrow_w + 2 + pct_w
        start_x = x + (w - total_w) // 2

        draw.text((start_x, center_y - 1), arrow, fill=FG, font=font)
        draw.text((start_x + arrow_w + 2, center_y + 1), pct_text, fill=FG, font=small_font)

    # MA column
    x, w = COL_MA
    ma_text = _ma_label(coin["ma_signal"])
    ma_bbox = draw.textbbox((0, 0), ma_text, font=small_font)
    ma_w = ma_bbox[2] - ma_bbox[0]
    draw.text((x + (w - ma_w) // 2, center_y), ma_text, fill=FG, font=small_font)

    # Signal column
    x, w = COL_SIGNAL
    sig_text = _signal_label(coin["signal"])
    sig_font = bold_font
    sig_bbox = draw.textbbox((0, 0), sig_text, font=sig_font)
    sig_w = sig_bbox[2] - sig_bbox[0]
    draw.text((x + (w - sig_w) // 2, center_y), sig_text, fill=FG, font=sig_font)

    # Row separator
    draw.line([(0, y + row_h), (WIDTH, y + row_h)], fill=LIGHT_GRAY, width=1)


def _draw_legend(draw: ImageDraw.Draw, y: int, font) -> None:
    """Draw legend at the bottom of the display."""
    legend_items = [
        "\u25b2 = Bull",
        "\u25bc = Bear",
        "GOLDEN = 50MA > 200MA",
        "DEATH = 50MA < 200MA",
        ">> BUY  << SELL  -- HOLD",
    ]
    text = "    ".join(legend_items)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(((WIDTH - text_w) // 2, y), text, fill=GRAY, font=font)


def generate(config: dict) -> str:
    """Generate the crypto market grid image.

    Args:
        config: Full project config dict.

    Returns:
        Path to the generated BMP file.
    """
    crypto_cfg = config.get("crypto_market", {})
    output_path = crypto_cfg.get("output_path", "images/crypto_market.bmp")
    num_coins = crypto_cfg.get("num_coins", 10)

    logger.info("Generating crypto market display...")

    # Fetch and analyze
    try:
        market_data = fetch_all_market_data(limit=num_coins)
        analysis = analyze_all(market_data)
    except Exception as e:
        logger.error("Failed to fetch crypto data: %s", e)
        # Generate error display
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(img)
        err_font = get_font(24, bold=True, config=config)
        draw.text((WIDTH // 2 - 150, HEIGHT // 2 - 20), "CRYPTO DATA UNAVAILABLE", fill=FG, font=err_font)
        small = get_font(14, config=config)
        draw.text((WIDTH // 2 - 100, HEIGHT // 2 + 20), str(e)[:60], fill=GRAY, font=small)
        img.save(output_path)
        return output_path

    if not analysis:
        logger.warning("No coins to display")
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(img)
        err_font = get_font(24, bold=True, config=config)
        draw.text((WIDTH // 2 - 100, HEIGHT // 2), "NO DATA", fill=FG, font=err_font)
        img.save(output_path)
        return output_path

    # Create canvas
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Fonts
    header_font = get_font(13, config=config)
    bold_font = get_font(14, bold=True, config=config)
    body_font = get_font(13, config=config)
    small_font = get_font(11, config=config)
    title_font = get_font(18, bold=True, config=config)
    legend_font = get_font(10, config=config)

    # Draw header
    content_y = _draw_header(draw, 0, header_font, title_font)

    # Calculate row height to fill available space
    num_rows = len(analysis)
    legend_height = 20
    available = HEIGHT - content_y - legend_height
    row_h = available // num_rows

    # Draw rows
    for i, coin in enumerate(analysis):
        row_y = content_y + i * row_h
        _draw_row(draw, row_y, row_h, coin, body_font, bold_font, small_font)

    # Draw legend
    legend_y = HEIGHT - legend_height + 2
    draw.line([(0, legend_y - 4), (WIDTH, legend_y - 4)], fill=FG, width=1)
    _draw_legend(draw, legend_y, legend_font)

    # Save
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("Saved crypto display to %s", output_path)

    return output_path
