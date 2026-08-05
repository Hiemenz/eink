# Radar module audit — bugs + feature backlog

Scope: the radar pipeline in `modules/weather.py` (RainViewer fetch, storm motion,
overlays, legend, quantize) plus its call sites in `generate()`.
Baseline: `tests/test_weather.py` 43 passed.

Active config: `radar_mode: panel`, `radar_source: rainviewer`, `rainviewer_zoom: 7`,
`more_colors: False`, `radar_alerts_overlay: false`, `lightning_overlay: false`.

---

## Bugs — high severity

- [x] **B1. Alert polygons bleed over the conditions panel.** `_overlay_severe_alerts`
  (`weather.py:1594-1621`) draws onto `final_img` with no clip to the radar region.
  The only guard (`:1605`) skips polygons *entirely* outside the region; a polygon that
  straddles `x = radar_w` draws its outline and its red label box straight over the
  white conditions panel and its text. Fix: render onto a `radar_w × radar_h` layer and
  `paste` it, or clip every segment.
- [x] **B2. Alert label text is never palette-snapped.** `:1619-1621` draws
  anti-aliased white-on-red text that then passes through `quantize_to_seven_colors`.
  This is exactly the failure mode documented for the timestamp label and the legend —
  AA edge pixels scatter onto unrelated palette colors. Fix: `_snap_region_2color(canvas,
  bbox, RED, WHITE)` after drawing. Same line also calls `get_font(13, bold=True,
  config=None)` — passing `config=None` discards the configured font paths.
- [x] **B3. Panel separator renders as a bright orange line.** `:1892` draws the
  full-height separator in `(180,180,180)`, *after* the panel B/W snap at `:1841-1844`.
  Verified: `distance((180,180,180), white) = 129.9 > threshold 75`, and white is not in
  the `more_colors: False` palette, so nearest is `(255,128,0)`. A 1px orange line runs
  down the middle of the display. Fix: draw the separator in pure black (or add white to
  the palette).
- [x] **B4. `fit` mode mangles RainViewer output.** `:1793` unconditionally crops
  `(0, 24, w, h-24)` to "strip the 24px NWS title bar" — but a RainViewer image has no
  title bar. It discards 48px of real radar, letterboxes the result with white bars, and
  the baked-in timestamp label (drawn at `y = h-17..h-6`) falls entirely inside the
  stripped bottom band, so it vanishes. Gate the crop on `radar_source == "ridge"`.
- [x] **B5. The storm-motion "weak peak" gate is a no-op.** `:1022` rejects a patch when
  `corr[peak] < corr.mean() * 3.0`. On a phase-correlation surface the mean is the DC
  term over N pixels ≈ `1/(rH*rW)` ≈ 1e-4, so the threshold is effectively zero.
  Measured: 200/200 pairs of *uncorrelated random noise* patches pass the gate. Pure
  noise vectors are therefore feeding the median. Fix: `corr.mean() + 4 * corr.std()`
  (rejects 199/200 noise).
- [x] **B6. OSM road cache key omits the viewport.** `:911` keys on `lat,lon,zoom` only,
  but the Overpass bbox at `:907-908` depends on `width`/`height`. Switching
  `radar_mode` panel↔seven_color changes the radar width 520→800 and silently reuses the
  narrower cached way set for 24h — roads stop short of the frame edges. Include
  `width`/`height` in the key.
- [x] **B7. Cold-start crash on fetch failure.** `generate()` `:2074-2076`: when
  `generate_weather_image` returns `(None, False, None)`, it falls back to
  `config["quantized_path"]` and calls `calculate_non_bw_percentage` on it
  unconditionally. On a first run (or after a cleaned `radar/`) with a failed RainViewer
  fetch, that file does not exist → `FileNotFoundError` out of the module.

## Bugs — medium

- [x] **B8. Promised speed label is missing.** `_draw_storm_motion_overlay`'s docstring
  (`:1106`) claims a `"Motion: E 13 mph →"` label in the bottom-left. No such code
  exists; `config`, `km_per_px` and `frame_interval_min` are accepted and never used.
  (`km_per_px` at `:1251` is computed correctly — 0.495 km/px at z7/lat 36 — so the
  label is ~10 lines of work. See F1.)
- [x] **B9. Lat/lon of exactly 0.0 is rejected.** `:1739` (`if not rv_lat or not rv_lon`),
  `:1831` and `:2093` (`if lat and lon`) treat 0.0 as missing. Use `is None`.
- [x] **B10. Xweather secret can leak into `eink.log`.** `:1636-1640` embeds
  `client_secret` in the URL; `requests` exceptions stringify the URL, and `:1646` logs
  the exception verbatim. Pass credentials via `params=` and redact before logging.
- [x] **B11. Roads and motion arrows use the same ink as the top dBZ tier.** Both are
  drawn pure black (`:1231`, `:1118`), and the legend labels black as `">60 dBZ"`. An
  interstate is indistinguishable from extreme reflectivity. Consider a dashed/thinner
  road line, or reserving black for reflectivity.
- [x] **B12. Overlays are silently absent in `crop`/`fit`.** Alerts and lightning are
  wired only into the `panel` (`:1900-1908`) and `seven_color` (`:1943-1950`) branches,
  even though both modes support RainViewer and the projection is known.
- [x] **B13. Alerts are fetched point-only.** `_fetch_nws_alerts` `:1531` queries
  `?point=lat,lon`, so only warnings covering the display point are returned. At zoom 7
  the canvas spans ~±130 km — a tornado warning plainly visible on screen is never
  drawn. Query by area/bbox and filter polygons against the view bounds.

## Bugs — low / cleanup

- [x] **B14. Alert label anchor is wrong.** `:1611-1613` uses `min(xs)` and `min(ys)`
  from *different* vertices (bbox corner, often outside the polygon), and clamps against
  a hardcoded `120` px instead of the measured text width.
- [x] **B15. Lightning dots spill past the region edge.** `:1694-1697` bounds-checks the
  centre but draws a 12px halo, so a strike on the right edge paints ~6px into the panel.
- [x] **B16. Dead code.** `_CARTO_URL` (`:829`), `_COUNTY_URL` (`:832`),
  `NWS_KM_PER_PX` (`:27`), `frame_interval_min` (`:1203-1205`, recomputed at `:1245`),
  and the whole top-5 station machinery — `best_station` at `:2083` is assigned `None`
  and never reassigned, so `full_station_scan`/`update_top5` are unreachable from
  `generate()`.
- [x] **B17. Shadowed import.** `:1256` does `import datetime as _dt` inside
  `_fetch_rainviewer_image`, shadowing the module-level `_dt` (the `datetime` *class*,
  `:15`). Works, but the two `_dt`s mean different things ~40 lines apart.

## Performance (measured on this Pi)

- [x] **P1. `quantize_to_seven_colors` is a pure-Python per-pixel loop** (`:815-822`):
  **1.43 s** for 800×480 at `more_colors: False`, **4.89 s** at `True`. Vectorizing the
  nearest-palette search with numpy broadcasting is ~50× and behaviour-identical.
- [x] **P2. `images_are_equal` materializes two 384k-tuple lists** (`:783`): 0.19 s.
  `np.array_equal` is ~100×.
- [x] **P3. Tiles are fetched serially and never cached.** `_fetch_rv_frame` `:863-875`
  loops tiles one at a time, and the current frame plus the previous frame means up to
  8 sequential 10 s-timeout requests per render — plus Overpass. Two wins: a
  `ThreadPoolExecutor(4)` fan-out, and a small on-disk tile cache keyed by
  `(path, z, x, y)` — the previous frame was the current frame one cycle ago, so it is
  already on disk.

---

## Features

- [x] **F1. Storm speed + arrival estimate.** All the inputs already exist
  (`km_per_px`, `actual_interval_min`, motion vector, home = frame centre). Restore the
  bottom-left label and extend it: `"Cells ENE 32 mph — here ~4:20 PM"` by projecting
  the nearest upwind precip cluster onto the motion vector. Highest value-per-line here.
- [x] **F2. RainViewer nowcast frames.** `_fetch_rainviewer_image` only reads
  `data["radar"]["past"]` (`:1193`). The same free API returns `data["radar"]["nowcast"]`
  — model-projected frames out to ~30 min. Two uses: (a) a "+30 min" ghost outline over
  the current frame, (b) sample the centre pixel across nowcast frames for a
  "Rain starts in ~18 min" line in the panel. Biggest capability left on the table.
- [x] **F3. Per-cell motion vectors.** `_compute_storm_motion` computes a displacement
  for every 40×40 patch and then throws all of them away except the median (`:1044`).
  Cluster the patch vectors spatially and draw each cluster its own arrow — divergent
  storm modes (a bow echo, a splitting supercell) currently render as one averaged
  direction that is right for neither half.
- [ ] **F4. Trend badge.** Compare total reflectivity mass across the last 3 frames →
  `Intensifying` / `Steady` / `Weakening` chip next to the timestamp. Cheap: the frames
  are already fetched for motion.
- [ ] **F5. Snow/mixed-precip tier.** The tile URL requests `1_1.png` — the trailing `1`
  *is* snow colouring — but `_remap_radar_seven_color` folds snow's blue-white palette
  into the generic "~5-25 dBZ" blue. Detect the snow hue/saturation band separately and
  give it its own legend entry.
- [ ] **F6. Home marker + range rings.** A crosshair at the frame centre and 25/50/100 km
  rings. At zoom 7 there is currently no way to judge how far away a cell is.
- [ ] **F7. Staleness indicator.** The timestamp label shows the frame time but nothing
  flags a stale feed. If `now - frame_ts > 20 min`, show `"Radar 43 min old"` in red.
- [ ] **F8. County/state boundaries.** `_COUNTY_URL` is defined and unused; the roads
  path already proved geometry beats raster tiles here. Natural Earth county/state
  polylines cached alongside `osm_roads_cache.json` would orient far better than
  interstates alone.
- [ ] **F9. Severity-styled alerts.** `_fetch_nws_alerts` already returns `severity`
  (`:1553`) and it is never used. A tornado warning should not render identically to a
  flood advisory. Pairs naturally with B13.
- [ ] **F10. Adaptive zoom.** If no precip pixels are in frame, zoom out one level; if a
  cell is within ~25 km, zoom in. Turns a blank white screen into useful context.
- [ ] **F11. Last-good-image fallback.** On total fetch failure the module returns
  `None` and the display keeps whatever was there. Persisting the last good render with
  a "stale" banner would degrade more gracefully.

---

## Review

All bugs above are fixed and F1/F2/F3 are implemented. `tests/test_weather.py`
grew from 43 to 80 tests; the full suite is 1094 passing. Verified end-to-end by
rendering all four `radar_mode`s against a fully mocked network (synthetic tiles
with two divergent storm cells, an alert polygon overhanging the panel edge, an
OSM motorway, and past + nowcast frames).

### Found while fixing — not in the original audit

- **B18. Panel header text fringed orange.** Found by rendering: the header bar
  text is drawn *after* the panel B/W snap (deliberately, so the snap can't erode
  it), which left its anti-aliased white-on-black edges to reach the quantizer
  raw. 231 pixels of the header quantized to orange. Same root cause as B2/B3 —
  a fourth instance of the family. Fixed with `_snap_region_2color` over the
  header box.
- **B19. Pure yellow was classified as heavy rain.** `_remap_radar_seven_color`
  used rounded decimal hue bounds. Hue of (255,255,0) is exactly 60° =
  0.16666…, which is *below* the literal `0.167` that began the yellow band, so
  the single most common "moderate rain" ink fell through into the orange
  (~45-50 dBZ) tier — over-reading rain intensity by a full tier everywhere.
  Band edges are now written `deg/360.0`, which reproduces the same double the
  hue math produces. Confirmed by render: yellow went from 0.08% to 4.09% of the
  canvas and orange dropped back to just its legend swatch.
- **B20. seven_color mode fetched the whole composite twice.** The top-level
  RainViewer block already fetched and remapped the image; the `seven_color`
  branch then did it all again at a different height. Now the top block sizes for
  the legend strip and the branch reuses its result.
- **B5 was under-calibrated at first.** The replacement gate `mean + 4*std` still
  let 11-21 spurious vectors through per pair of independent noise frames.
  Measured the plateau and settled on `mean + 6*std`: zero noise vectors, while a
  genuine 10px translation still recovers exactly (10, -6) with or without 5%
  background noise anywhere from 5 to 12 sigma.

### Notes on the fixes

- **B1/B15** share one mechanism: both overlays now render onto a region-sized
  layer and paste it back, so clipping is structural rather than a bounds check
  that each new drawing primitive has to remember.
- **B13** needed a way to bound the alert query. `/alerts/active` has no bbox
  parameter, and pulling all active US alerts could be megabytes during an
  outbreak. Instead `_view_state_codes` probes the view's four corners and centre
  via `/points`, reads the state off each forecast-zone id, and caches the answer
  for 30 days — the geography only changes if the location or zoom does.
- **B11** draws roads as a black core in a white casing. A white casing is the
  one thing a reflectivity core never has, so it distinguishes a road from the
  black ">60 dBZ" tier without a legend entry.
- **P1** is bit-exact against the original implementation for all four
  (more_colors × threshold) combinations, verified pixel-for-pixel: 1.43 s →
  0.115 s, and 4.89 s → 0.382 s.
- **F3** required clustering on velocity as well as position. Distance-only
  single-link chained two touching cells into one blob — exactly the
  splitting-supercell case the feature exists to resolve. Patches must now also
  agree on velocity to within 6 px/frame (~18 km/h at zoom 7).
- **F1**'s caption describes the *dominant* cell rather than the median across
  all of them: when cells diverge, the median heading is one no storm is taking.

### Deliberately not done

- F4-F11 (trend badge, snow tier, range rings, staleness, boundaries, severity
  styling, adaptive zoom, last-good fallback) remain open — only F1/F2/F3 were
  in scope.
- `full_station_scan` / `update_top5` are still unreachable from `generate()`
  (part of B16). They are public, tested, and separately used by
  `weather_generator.py`, so removing them is a wider decision than this pass.
  The dead `best_station` branch inside `generate()` is gone.

### Config added

```yaml
rainviewer_nowcast: true   # dashed +30min edge, and "rain here in ~N min"
radar_speed_units: mph     # storm speed caption units: "mph" or "kmh"
```

New cache paths (both gitignored): `data/rv_tiles/`, `data/nws_view_states.json`.
