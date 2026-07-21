# E-Ink Display — Feature Roadmap

Backlog of enhancement ideas. Effort is a rough estimate (S = <1hr, M = a few hrs,
L = a day+). "Data source" notes the free API; "no key" means no signup required.

---

## Shipped

- [x] RainViewer high-resolution radar source
- [x] 7-color radar mode (`radar_mode: seven_color`) — uses all Waveshare ink channels
- [x] Radar frame timestamp in panel mode (bottom-left corner)
- [x] Per-module update intervals (`MODULE_INTERVALS` in `utils.py`)
- [x] Web preview route (`/preview`, `/preview/<name>`, `/preview-all`)
- [x] Air Quality Index module (AirNow)
- [x] Countdown timer module (config-driven)
- [x] Sports scores module (ESPN, no key)
- [x] Word of the Day module (Merriam-Webster)

---

## Radar / Weather

- [ ] **48-hour forecast graph** — line/area chart of temp + precip probability.
      Reuses the Open-Meteo data already fetched in `weather.py`. No new API. (S)
- [ ] **Aurora forecast** — NOAA SWPC Kp-index gauge + "visible tonight?" verdict.
      Data source: services.swpc.noaa.gov (no key). (M)
- [ ] **Lightning overlay** — RainViewer lightning tile path, semi-transparent strike
      markers over the radar canvas. (M)
- [ ] **Severe weather polygon overlay** — NWS `api.weather.gov/alerts/active` GeoJSON,
      draw hatched warning boxes over radar. (L)
- [ ] **Pollen count** — Google Pollen or Ambee API. Color-tiered tile like AQI. (M)
- [ ] **RainViewer animation** — animate last 6–12 frames as a GIF/slideshow.
      Note: each e-ink refresh is slow (~15s on 7-color), so this is a slideshow. (M)

## New Display Modules

- [ ] **Calendar / agenda** — today's events as a clean list. Highest daily value.
      Data source: Google Calendar (MCP auth available). (M)
- [ ] **ISS pass predictions** — "Next visible pass: 9:42 PM, 6 min, NW→SE."
      Data source: N2YO or Open-Notify (no key). (S)
- [ ] **Recent earthquakes** — plot USGS GeoJSON feed on a small map. Reuses the
      staticmap + lat/lon projection code from `flight_radar.py`. (M)
- [ ] **Stock watchlist** — equities sibling to crypto module.
      Data source: Yahoo Finance / Alpha Vantage free tier. (M)
- [ ] **Electricity grid carbon intensity** — "clean vs dirty right now" color tile.
      Data source: ElectricityMaps / WattTime. E-ink-native use case. (M)
- [ ] **Now playing** — Spotify / Last.fm recent track + album art. (M)
- [ ] **XKCD / comic of the day** — image-based, trivially easy like NASA APOD. (S)
- [ ] **Local traffic** — TomTom / HERE free-tier incident data, delay + count. (M)

## Infrastructure / Quality of Life

- [ ] **Composite dashboard mode** — tile 2–4 modules on one 800×480 screen
      (e.g. weather + agenda + AQI). Biggest layout upgrade; the module `generate()`
      contract makes it feasible. (L)
- [ ] **Scheduled night sleep** — skip refreshes overnight to reduce panel wear /
      power. (S)
- [ ] **Periodic full-clear** — every N refreshes, full white/black flush to clear
      7-color ghosting. (S)
- [ ] **Alert-driven auto-override** — `module_cycler` force-switches to radar when a
      severe-weather alert fires, until it clears. (M)
- [ ] **Discord `!status`** — reply with current module, last update time, radar frame
      age, and stale-data flags from the state file. (M)
- [ ] **Home Assistant / MQTT bridge** — publish current module state; let HA trigger
      module switches. (L)
- [ ] **Watchdog + health check** — auto-restart on crash, log last-successful-refresh
      per module. (M)

---

## High signal-per-effort picks

1. **48h forecast graph** — zero new deps, reuses fetched data.
2. **Calendar / agenda** — most useful daily glance; real data via MCP.
3. **Composite dashboard mode** — multiplies the value of every existing module.
