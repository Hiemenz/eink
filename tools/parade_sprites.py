"""Export the holiday parade art as seven-color sprites for the e-ink module.

The balloons are drawn once, in parade/holiday_parade.html. This renders each one with headless
Chromium and converts it to the panel's exact inks, so the display itself needs no browser.
Re-run after changing the art:

    uv run python tools/parade_sprites.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "parade" / "holiday_parade.html"
OUT = ROOT / "parade" / "sprites"
WINDOW_CHROME = 87  # headless Chromium's window is this much taller than its viewport
MIN_WINDOW = 640  # Chromium won't go narrower than about 500, which would crop the art

K, WH, GR, BL, RD, YL, OR = (
    (0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255), (255, 0, 0), (255, 255, 0), (255, 128, 0))

# Each art color -> (base ink, second ink, share of the second ink). The panel has no browns,
# pinks or pastels, so those become fine two-ink patterns.
MIXES = {
    "#1D1D1F": (K, K, 0), "#FBF8F1": (WH, WH, 0), "#FFFFFF": (WH, WH, 0), "#E0483B": (RD, RD, 0),
    "#2E59C9": (BL, BL, 0), "#F2BE35": (YL, OR, .25), "#EC7C30": (OR, OR, 0), "#4B9560": (GR, GR, 0),
    "#2F6B47": (GR, K, .5), "#86BEDD": (WH, BL, .4), "#EC95AE": (WH, RD, .4), "#D8A56E": (OR, WH, .5),
    "#80523A": (RD, K, .35), "#BDB8AE": (WH, K, .2), "#6C55A8": (BL, RD, .45), "#F3C9A0": (WH, OR, .25),
    "#F6E3C4": (WH, YL, .35), "#CFE5EE": (WH, BL, .12), "#8FD0B8": (WH, GR, .6), "#4E9C86": (GR, BL, .3),
    "#7E2226": (RD, K, .35), "#E8737A": (RD, WH, .35), "#D9786A": (RD, WH, .3), "#EC9C8C": (WH, RD, .45),
    "#D7A53C": (OR, YL, .4), "#C4703F": (OR, RD, .35), "#9E5530": (RD, K, .3), "#C98748": (OR, K, .15),
    "#C97A38": (OR, K, .15), "#B9652C": (OR, RD, .45), "#8C8A92": (WH, K, .45), "#45424C": (K, WH, .12),
    "#A8662F": (OR, K, .35), "#D9792E": (OR, RD, .15), "#BC7A3E": (OR, RD, .4), "#4E5560": (K, WH, .25),
    "#666E7A": (K, WH, .4), "#2F343D": (K, K, 0), "#D9CDEE": (WH, BL, .2), "#523F86": (BL, K, .35),
    "#9C86D6": (BL, WH, .35), "#3E5568": (BL, K, .55), "#5E7A90": (BL, WH, .3), "#B9B1A0": (K, WH, .35), "#CBC3B2": (K, WH, .45), "#6A402D": (RD, K, .5),
    "#A97F63": (OR, K, .4), "#D9D2C2": (WH, K, .15), "#6F695E": (K, K, 0), "#E9E3B8": (WH, YL, .45),
    "#5C3826": (K, RD, .3), "#B9352C": (RD, K, .15),
}
BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16


def to_panel_inks(image: Image.Image) -> Image.Image:
    """Snap an RGBA render to exact panel inks with a hard-edged alpha."""
    arr = np.array(image.convert("RGBA"))
    rgb = arr[..., :3].astype(np.int32)
    keys = np.array([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in MIXES])
    nearest = ((rgb[..., None, :] - keys[None, None]) ** 2).sum(-1).argmin(-1)
    base = np.array([m[0] for m in MIXES.values()], np.uint8)[nearest]
    second = np.array([m[1] for m in MIXES.values()], np.uint8)[nearest]
    share = np.array([m[2] for m in MIXES.values()])[nearest]
    height, width = nearest.shape
    threshold = np.tile(BAYER, (height // 4 + 1, width // 4 + 1))[:height, :width]
    out = np.where((threshold < share)[..., None], second, base)
    alpha = np.where(arr[..., 3] > 127, 255, 0).astype(np.uint8)
    out[alpha == 0] = 255
    return Image.fromarray(np.dstack([out, alpha]).astype(np.uint8))


def balloon_names() -> list[str]:
    block = re.search(r"const BALLOONS=\[\n(.*?)\]\];", PAGE.read_text(), re.S).group(1)
    return re.findall(r'\["([^"]+)",', block + "]")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def render(browser: str, sprite: str, width: int, height: int, scale: float, work: str) -> Image.Image:
    shot = os.path.join(work, "shot.png")
    if os.path.exists(shot):
        os.remove(shot)
    window = max(width, MIN_WINDOW)  # the art is centred, so crop the middle back out
    subprocess.run(
        [browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
         f"--force-device-scale-factor={scale}", f"--window-size={window},{height + WINDOW_CHROME}",
         "--virtual-time-budget=2000", f"--screenshot={shot}", f"{PAGE.as_uri()}?sprite={sprite}"],
        check=True, capture_output=True, timeout=90)
    left = round((window - width) / 2 * scale)
    return Image.open(shot).convert("RGBA").crop((left, 0, left + round(width * scale), round(height * scale)))


def main() -> None:
    browser = shutil.which("chromium") or shutil.which("chromium-browser")
    if not browser:
        sys.exit("Chromium is needed to export the sprites.")
    OUT.mkdir(parents=True, exist_ok=True)
    names = balloon_names()
    manifest = []
    with tempfile.TemporaryDirectory() as work:
        for index, name in enumerate(names + ["Santa"]):
            sprite = "santa" if name == "Santa" else str(index)
            santa = name == "Santa"
            width, height = (430, 420) if santa else (340, 300)  # Santa is taller than the rest
            for size, scale in (("small", 0.6 if santa else 0.75), ("large", 0.85 if santa else 1.3)):
                image = to_panel_inks(render(browser, sprite, width, height, scale, work))
                image.save(OUT / f"{slug(name)}-{size}.png", optimize=True)
            if name != "Santa":
                manifest.append({"name": name, "file": slug(name)})
        finale = to_panel_inks(render(browser, "finale", 970, 646, 0.62, work))
        finale.save(OUT / "finale.png", optimize=True)
    (OUT / "balloons.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"Exported {len(manifest)} balloons, Santa and the finale to {OUT}")


if __name__ == "__main__":
    main()
