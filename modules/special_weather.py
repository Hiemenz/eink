"""Active National Weather Service alerts for the display's own location."""

from datetime import datetime
from typing import Any, Dict, Optional

import requests

from utils import get_logger

logger = get_logger("special_weather")

# Most urgent first. Colors are exact e-ink palette entries.
ALERT_LEVELS = {
    "Warning":  {"color": (255, 0, 0),   "text": (255, 255, 255)},
    "Watch":    {"color": (255, 128, 0), "text": (255, 255, 255)},
    "Advisory": {"color": (0, 255, 0),   "text": (255, 255, 255)},
}


def pick_alert(features: list) -> Optional[Dict[str, Any]]:
    """The most urgent warning, watch or advisory among GeoJSON alert features.

    The level is the last word of the event name. Anything else (special weather
    statements, outlooks) is ignored, as are test and cancellation messages.
    """
    ranks = list(ALERT_LEVELS)
    best = None
    for feature in features:
        props = feature.get("properties") or {}
        event = props.get("event") or ""
        level = event.rsplit(" ", 1)[-1]
        if (level not in ALERT_LEVELS or props.get("status", "Actual") != "Actual"
                or props.get("messageType") == "Cancel"):
            continue
        if best is None or ranks.index(level) < ranks.index(best["level"]):
            best = {"event": event, "level": level, "ends": props.get("ends")}
    return best


def alert_headline(alert: Dict[str, Any]) -> str:
    """Banner text, e.g. "Winter Weather Advisory until 9:00 PM"."""
    try:
        ends = datetime.fromisoformat(alert["ends"])
    except (KeyError, TypeError, ValueError):
        return alert["event"]
    today = ends.date() == datetime.now(ends.tzinfo).date()
    return f"{alert['event']} until {ends.strftime('%-I:%M %p' if today else '%a %-I:%M %p')}"


def get_local_alert(config: dict, headers: dict) -> Optional[Dict[str, Any]]:
    """The alert to show for `forecast_location`, or None. Never raises."""
    location = config.get("forecast_location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    if not config.get("check_special_weather", True) or lat is None or lon is None:
        return None
    try:
        resp = requests.get(
            f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}",
            headers={**headers, "Accept": "application/geo+json"}, timeout=15,
        )
        resp.raise_for_status()
        alert = pick_alert(resp.json().get("features", []))
    except Exception as e:
        logger.warning("NWS alert check failed: %s", e)
        return None
    if alert:
        logger.info("Active alert: %s", alert_headline(alert))
    return alert
