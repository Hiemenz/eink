"""
Skill: Crypto Monitor
Fetches BTC/ETH prices and stores a market summary in memory.
"""

from __future__ import annotations

import requests

SKILL_NAME = "crypto_monitor"
SKILL_DESCRIPTION = "Fetches cryptocurrency prices and stores market summaries."
SCHEDULE_INTERVAL = 3600  # run every hour


def run(memory, llm) -> str:
    """Fetch crypto prices and store a summary."""
    prices = _fetch_prices()
    if not prices:
        return "Failed to fetch crypto prices."

    price_text = "\n".join(f"{coin}: ${price:,.2f}" for coin, price in prices.items())

    summary = llm.simple(
        f"Current crypto prices:\n{price_text}\n\n"
        "Write a 2-sentence market observation.",
        system="You are a concise crypto market analyst.",
    )

    memory.save_knowledge(
        topic="crypto_prices",
        summary=f"{price_text}\n\nAnalysis: {summary}",
        source="coingecko",
    )
    memory.log_event("crypto_monitor", "price_update", price_text)
    return summary


def _fetch_prices() -> dict:
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "BTC": data.get("bitcoin", {}).get("usd", 0),
            "ETH": data.get("ethereum", {}).get("usd", 0),
            "SOL": data.get("solana", {}).get("usd", 0),
        }
    except Exception as e:
        return {}
