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

---

# Trusted Train header scene + weather audit (2026-11-10)

Repo note: this repo has no Streamlit app, no "Global Home" page and no `master` branch. The scene is
built as a self-contained module (`dashboard/trusted_train.py`) plus a small demo page; bulletin-board
slides are a parameter with placeholder defaults. Work is on `feature/trusted-train` (never on main).

- [x] Branch, dev tooling (black, isort), optional `dashboard` extra (streamlit)
- [ ] Static scene: sky, far hills, crest + plateau, terraces, lake, rails, title car, buildings; screenshot
- [ ] Preview/perf tool (headless Chrome over DevTools Protocol)
- [ ] Core interactions: hover tips, bulletin board, mailbox, far train, seeded chance walkers
- [ ] Monday/Wednesday staff walk with generated ground-following keyframes
- [ ] Fall festival
- [ ] Winter/Christmas: structures, post office + Grinch, kings, Dickens + carol clock, set pieces
- [ ] Night: lit windows, theatre, Lights on the Lake
- [ ] Rotating shows (2-3 per load)
- [ ] Still mode, reduced motion
- [ ] Tests (every season x mode), black + isort, perf measurement
- [ ] Weather display audit: bugs + feature suggestions (report only)

---

# Franklin, TN landmarks + calendar-driven festivals (2026-11-12)

Scope confirmed with user: Franklin landmarks, seasonal festivals, real-date events; same CSS-animated
scene (no e-ink version). Skaneateles look (lakefront, inn, storefront) stays as is.

- [x] Calendar: `franklin_event(day)` (Main Street Festival, Farmers Market Sat, Fourth, Pilgrimage, luminaria, Dickens of a Christmas) + `event=` override on `train_html`
- [x] Heritage row on the meadow: Carter House, Carnton, Williamson County courthouse + Public Square, Factory at Franklin
- [x] Event props: spring tents + stage, farmers market, July 4 bunting/flags/night fireworks, Pilgrimage stage + Ferris wheel, luminaria, Dickens banner + full cast
- [x] `tools/train_preview.py --event`, tests (calendar windows, every event renders, bad event/theme rejected), rendered check of each event

## Review
- Rendered every event (light + dark where it matters) in headless Chrome; heritage row visible behind the stage/tents.
- 41 new tests in `tests/test_trusted_train.py` (calendar windows, resolve rules, every event x mode, still mode).
- Not committed. Event dates are approximations of Franklin's usual weekends, not a live feed.

---

# Make-believe village + day cycle (2026-11-12)

User asked for a make-believe town that changes through the day, more village than city. Decisions: rename the
Franklin/Skaneateles names to invented ones; day cycle follows the real clock.

- [x] Rename: events (spring_fair, village_market, founders_day, harvest_fair, lantern_night, carol_weekend), landmarks (Hollis Manor, Ashgrove Hall, Town Hall on the Green, Old Mill), shops (Bakery, Grocer, Market Hall, Village Hall); `village_event()`
- [x] `mode="clock"` (new default): night art fades over day art, sun/moon arcs, golden-hour tint, night-only/day-only layers fade; same level table drives CSS keyframes and the still snapshot
- [x] Tests: day levels, orbit, clock offsets, still snapshot, no real-world names left
- [ ] Not done: windmill / well / more cottages (village layout beyond renames), hour-based people (commuters, shops closing)

## Review
- Rendered 6:20, 12:20, 18:20, 22:20: early light, midday sun, golden hour, full night with lit windows and moon.
- Bug caught by looking at the render, not the tests: keyframe stops were comma-joined (invalid CSS), so nothing animated and the scene sat at the wrong hour. Lesson added.
- Page size grew ~150KB -> ~260KB (every sprite carries a night image). Real-browser perf not re-measured.
