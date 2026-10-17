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
