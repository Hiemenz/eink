# AI Brain Framework - Implementation Plan

## Architecture
- [ ] Config system (YAML loader)
- [ ] LLM interface layer (Anthropic, OpenAI, Ollama)
- [ ] Memory system (DuckDB)
- [ ] Tool system (web_search, file_manager, code_runner, scheduler, git_tools)
- [ ] Base Agent class
- [ ] Specialized agents (Research, Builder, Operator, Planner)
- [ ] Task Orchestrator
- [ ] Job Scheduler
- [ ] Plugin/Skills system with auto-discovery
- [ ] Brain Agent (central reasoning loop)
- [ ] Main entry point / daemon runner
- [ ] Example skills (crypto_monitor, weather_agent)
- [ ] README
- [ ] Update pyproject.toml

## Review
- [ ] Verify all imports resolve
- [ ] Verify modular design
- [ ] Commit and push

---

# Radar accuracy, black roads, dynamic/full radar, Discord selector (2026-10-16)

Findings (verified against live data, not assumed):
- RainViewer tiles are byte-identical for color schemes 0-8: only "Universal Blue" is served.
  `_remap_radar_seven_color` assumed NWS green/yellow/red, so every echo became light blue -> "no cells".
- Default zoom 7 window is ~±130 km; the cells on the NWS image are just outside it.
- NWS GIF draws highways in exact (156,15,15); quantizer turns that red/orange.

- [x] Remap Universal Blue ramp (blue/dark-blue/tan snow) into intensity tiers
- [x] Ridge highways -> black
- [x] Wider default view (zoom 6) and lighter Overpass query at zoom <= 6
- [x] radar_mode `full` (full-screen, uniform scale) and `dynamic` (panel -> full radar when precip nearby)
- [x] Discord: `!menu` selects (module / radar mode / display model), `!radar`, `!model`
- [x] Tests, rendered verification, docs

## Review
- Rendered panel / full / dynamic / NWS-panel against live data; RainViewer full view matches the NWS band
  (same SE line, real green/yellow cores, arrows on cells); roads pure black in both sources.
- Overpass was flaky (6 timeouts in a row): added mirror fallback, 15-min failure backoff, compact
  multi-viewport cache. Zoom-6 and zoom-7 caches pre-populated.
- 1729 tests pass. Not committed.
- Open: "Slack" request was built on the Discord bot (the only two-way control channel; Slack here is error alerts only).
