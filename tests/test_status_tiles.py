"""Unit tests for modules/status_tiles.py — status mapping, grid fitting, rendering."""

import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import status_tiles as st


def test_normalize_aliases_and_unknown():
    assert st.normalize_status("Green") == "ok"
    assert st.normalize_status("degraded") == "warn"
    assert st.normalize_status("red") == "fail"
    assert st.normalize_status("???") == "fail"
    assert st.normalize_status(None) == "fail"


def test_get_statuses_default_and_config():
    assert st.get_statuses({}) == st._SAMPLE
    cfg = {"status_tiles": {"items": [{"name": "a", "status": "yellow"}, "b"]}}
    assert st.get_statuses(cfg) == [("a", "warn"), ("b", "ok")]


def test_grid_layout_fits_and_handles_empty():
    assert st.grid_layout(0, 100, 100) == (0, 0, 0, 0)
    for n in (1, 7, 18, 60, 150):
        cols, rows, tw, th = st.grid_layout(n, 784, 400)
        assert cols * rows >= n
        assert cols * tw + (cols - 1) * st.GAP <= 784
        assert rows * th + (rows - 1) * st.GAP <= 400


def test_generate_renders_all_three_colors(tmp_path):
    out = tmp_path / "s.bmp"
    cfg = {"status_tiles": {"output_path": str(out), "items": [
        {"name": "a", "status": "green"}, {"name": "b", "status": "yellow"},
        {"name": "c", "status": "red"}]}}
    assert st.generate(cfg) == str(out)
    img = Image.open(out)
    assert img.size == (800, 480)
    colors = {c for _, c in img.getcolors(100000)}
    assert {st.GREEN, st.YELLOW, st.RED} <= colors


def test_generate_many_tiles(tmp_path):
    cfg = {"status_tiles": {"output_path": str(tmp_path / "s.bmp"),
                            "items": [{"name": f"m{i}", "status": "ok"} for i in range(120)]}}
    st.generate(cfg)
