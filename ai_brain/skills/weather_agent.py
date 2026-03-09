"""
Skill: Weather Agent
Fetches local weather using Open-Meteo (free, no API key required).
"""

from __future__ import annotations

import requests

SKILL_NAME = "weather_agent"
SKILL_DESCRIPTION = "Fetches local weather forecast and stores it in memory."
SCHEDULE_INTERVAL = 21600  # run every 6 hours

# Default location — override via env vars WEATHER_LAT / WEATHER_LON
import os
DEFAULT_LAT = float(os.environ.get("WEATHER_LAT", "40.7128"))   # New York
DEFAULT_LON = float(os.environ.get("WEATHER_LON", "-74.0060"))


def run(memory, llm) -> str:
    weather = _fetch_weather(DEFAULT_LAT, DEFAULT_LON)
    if not weather:
        return "Failed to fetch weather."

    summary = llm.simple(
        f"Weather data for lat={DEFAULT_LAT}, lon={DEFAULT_LON}:\n{weather}\n\n"
        "Write a brief 2-sentence weather summary.",
        system="You are a concise weather reporter.",
    )

    memory.save_knowledge(
        topic="weather_forecast",
        summary=summary,
        source="open-meteo.com",
    )
    memory.log_event("weather_agent", "forecast_update", summary[:200])
    return summary


def _fetch_weather(lat: float, lon: float) -> str:
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,weather_code",
                "temperature_unit": "fahrenheit",
            },
            timeout=10,
        )
        resp.raise_for_status()
        current = resp.json().get("current", {})
        return (
            f"Temperature: {current.get('temperature_2m')}°F\n"
            f"Wind: {current.get('wind_speed_10m')} km/h\n"
            f"Code: {current.get('weather_code')}"
        )
    except Exception as e:
        return ""
