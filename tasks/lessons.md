# Lessons

- **Verify a data source's contract before trusting code that assumes it.** RainViewer silently
  stopped honoring `color_scheme` (all 9 schemes return byte-identical tiles), so an HSV remap tuned
  for NWS colors flattened every echo to "light rain" and the display showed no cells. When a
  rendered output disagrees with a reference (the NWS image), diff the raw inputs first
  (`md5sum` the tiles per scheme) before touching the renderer.
- **Resampling creates colors the palette never had.** Any resize of a line-art source (roads,
  labels) must be followed by a snap to exact palette colors; otherwise the quantizer inks the
  anti-aliased fringe orange. Classify the ramp (road pink: G==B, R>G) instead of one exact RGB.
- **Single-entry caches thrash when two layouts alternate** (panel <-> full). Key caches by what
  determines the data (centre, zoom), not by the viewport, and fetch a superset.
- **A generated @keyframes body is not comma-separated.** `0%{..},50%{..}` is invalid CSS and the browser
  silently drops the whole rule, so the animation just never runs; asserting on the string passes while the
  render is wrong. Join stops with "" and check a screenshot at two different times of day.
- **Fitting more into the display means rearranging, not growing chrome.** Adding a row to the bottom key (36 -> 52px)
  was rejected because it takes space from the radar. Keep strip height fixed; compact what's there first.
- **The radar is unreadable without the map on top of it.** Layering roads under echo "so rain is never
  hidden" erased them in any real storm; the user couldn't tell where the rain was. Draw thin geographic
  linework over radar, and preview display changes with heavy echo, not just a mostly clear frame.
- **A PNG preview overstates contrast on the e-ink panel.** Its blue ink is near navy: 1px black roads
  looked fine on the PC render and vanished on the real screen. Never put black linework or text on blue
  or black echo; use white there.
- **A panel-wide color swap hits icons too.** Turning white into the alert color hollowed the moon to a
  thin ring. After recoloring, check every drawn icon, not just the text.

## Check origin/main before describing what the project contains
- Run `git fetch` and compare the checked-out branch with `origin/main` first; a feature branch can be stale right after a merge (a 30-balloon branch was shown when main had 74).
