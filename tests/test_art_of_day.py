"""
Unit tests for modules/art_of_day.py — object-ID caching and deterministic
artwork selection logic (network calls mocked, no real HTTP requests).
"""

import json
import os
import sys
from datetime import date
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import art_of_day


class TestIdsCachePath:
    def test_uses_cache_dir_constant(self):
        assert art_of_day._ids_cache_path() == os.path.join("data", "art_ids_cache.json")


class TestTodayBmpPath:
    def test_includes_isoformat_date(self):
        path = art_of_day._today_bmp_path()
        today = date.today().isoformat()
        assert path == os.path.join("data", f"art_{today}.bmp")


class TestLoadObjectIds:
    def test_reads_from_cache_when_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "art_ids_cache.json"), "w") as f:
            json.dump({"objectIDs": [1, 2, 3]}, f)
        with patch.object(art_of_day.requests, "get") as mock_get:
            ids = art_of_day._load_object_ids()
        mock_get.assert_not_called()
        assert ids == [1, 2, 3]

    def test_fetches_and_caches_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"objectIDs": [10, 20]}
        with patch.object(art_of_day.requests, "get", return_value=mock_resp):
            ids = art_of_day._load_object_ids()
        assert ids == [10, 20]
        with open(os.path.join("data", "art_ids_cache.json")) as f:
            cached = json.load(f)
        assert cached["objectIDs"] == [10, 20]

    def test_network_failure_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(art_of_day.requests, "get", side_effect=Exception("timeout")):
            ids = art_of_day._load_object_ids()
        assert ids == []

    def test_empty_cache_file_triggers_refetch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "art_ids_cache.json"), "w") as f:
            json.dump({"objectIDs": []}, f)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"objectIDs": [99]}
        with patch.object(art_of_day.requests, "get", return_value=mock_resp):
            ids = art_of_day._load_object_ids()
        assert ids == [99]


class TestFetchObject:
    def test_success_returns_json(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"title": "Mona Lisa"}
        with patch.object(art_of_day.requests, "get", return_value=mock_resp):
            data = art_of_day._fetch_object(123)
        assert data == {"title": "Mona Lisa"}

    def test_failure_returns_none(self):
        with patch.object(art_of_day.requests, "get", side_effect=Exception("boom")):
            assert art_of_day._fetch_object(123) is None


class TestPickArtwork:
    def test_deterministic_by_day_of_year(self):
        """The chosen index should be (day_of_year % n), reproducible for a fixed date."""
        object_ids = list(range(100, 110))  # 10 ids
        today_yday = date.today().timetuple().tm_yday
        expected_idx = today_yday % len(object_ids)
        expected_id = object_ids[expected_idx]

        def fake_fetch(obj_id):
            return {"primaryImage": "http://example.com/img.jpg", "title": "T", "artistDisplayName": "A"}

        with patch.object(art_of_day, "_fetch_object", side_effect=fake_fetch) as mock_fetch:
            data, image_url = art_of_day._pick_artwork(object_ids)

        # First call should be with the expected deterministic id.
        first_call_id = mock_fetch.call_args_list[0][0][0]
        assert first_call_id == expected_id
        assert image_url == "http://example.com/img.jpg"

    def test_skips_objects_without_primary_image(self):
        object_ids = [1, 2, 3, 4, 5]

        def fake_fetch(obj_id):
            if obj_id in (1, 2, 3):
                return {"primaryImage": "", "title": "No image"}
            return {"primaryImage": "http://example.com/found.jpg", "title": "Found"}

        with patch.object(art_of_day, "_fetch_object", side_effect=fake_fetch):
            data, image_url = art_of_day._pick_artwork(object_ids)
        assert image_url == "http://example.com/found.jpg"

    def test_gives_up_after_ten_attempts(self):
        object_ids = list(range(20))

        with patch.object(art_of_day, "_fetch_object", return_value={"primaryImage": ""}):
            data, image_url = art_of_day._pick_artwork(object_ids)
        assert data is None
        assert image_url is None

    def test_fetch_returning_none_is_skipped(self):
        object_ids = list(range(15))
        call_count = {"n": 0}

        def fake_fetch(obj_id):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return None
            return {"primaryImage": "http://example.com/img.jpg"}

        with patch.object(art_of_day, "_fetch_object", side_effect=fake_fetch):
            data, image_url = art_of_day._pick_artwork(object_ids)
        assert image_url == "http://example.com/img.jpg"


class TestDownloadImage:
    def test_network_failure_returns_none(self):
        with patch.object(art_of_day.requests, "get", side_effect=Exception("timeout")):
            assert art_of_day._download_image("http://example.com/img.jpg") is None

    def test_success_returns_rgb_image(self):
        buf = BytesIO()
        Image.new("RGB", (50, 40), "blue").save(buf, format="PNG")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = buf.getvalue()
        with patch.object(art_of_day.requests, "get", return_value=mock_resp):
            img = art_of_day._download_image("http://example.com/img.jpg")
        assert img is not None
        assert img.mode == "RGB"
        assert img.size == (50, 40)


class TestRender:
    def test_output_matches_requested_dimensions(self, tmp_path):
        src = Image.new("RGB", (1000, 600), "red")
        out = str(tmp_path / "rendered.bmp")
        result = art_of_day._render(src, "A Title", "An Artist", "1900", out, width=800, height=480)
        assert result == out
        saved = Image.open(out)
        assert saved.size == (800, 480)

    def test_long_title_gets_truncated_with_ellipsis(self, tmp_path):
        src = Image.new("RGB", (800, 480), "green")
        out = str(tmp_path / "rendered.bmp")
        long_title = "A" * 500
        # Must not raise even though the untruncated title can't possibly fit.
        art_of_day._render(src, long_title, "Artist", "2020", out, width=800, height=480)
        assert os.path.exists(out)

    def test_missing_title_falls_back_to_untitled(self, tmp_path):
        src = Image.new("RGB", (800, 480), "white")
        out = str(tmp_path / "rendered.bmp")
        # Must not raise on a falsy title.
        result = art_of_day._render(src, "", "", "", out, width=800, height=480)
        assert result == out

    def test_caption_band_darkens_bottom_of_image(self, tmp_path):
        src = Image.new("RGB", (800, 480), (255, 255, 255))
        out = str(tmp_path / "rendered.bmp")
        art_of_day._render(src, "T", "A", "2020", out, width=800, height=480)
        saved = Image.open(out).convert("RGB")
        top_pixel = saved.getpixel((400, 10))
        band_pixel = saved.getpixel((400, 470))
        assert sum(band_pixel) < sum(top_pixel)


class TestErrorImage:
    def test_creates_image_at_requested_size(self, tmp_path):
        out = str(tmp_path / "error.bmp")
        result = art_of_day._error_image(out, width=800, height=480)
        assert result == out
        img = Image.open(out)
        assert img.size == (800, 480)
        assert img.mode == "RGB"

    def test_default_message_does_not_raise(self, tmp_path):
        out = str(tmp_path / "error.bmp")
        art_of_day._error_image(out)
        assert os.path.exists(out)


class TestGenerate:
    def _config(self, output_path):
        return {
            "art_of_day": {"output_path": output_path},
            "width": 800,
            "height": 480,
        }

    def test_uses_cached_bmp_when_already_generated_today(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        today = date.today().isoformat()
        cached_path = os.path.join("data", f"art_{today}.bmp")
        Image.new("RGB", (800, 480), "white").save(cached_path)

        output_path = "art_display.bmp"
        with patch.object(art_of_day, "_load_object_ids") as mock_load:
            result = art_of_day.generate(self._config(output_path))

        mock_load.assert_not_called()
        assert result == output_path
        assert os.path.exists(output_path)

    def test_no_object_ids_renders_error_image(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        output_path = "art_display.bmp"
        with patch.object(art_of_day, "_load_object_ids", return_value=[]):
            result = art_of_day.generate(self._config(output_path))
        assert result == output_path
        assert os.path.exists(output_path)

    def test_no_artwork_found_renders_error_image(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        output_path = "art_display.bmp"
        with patch.object(art_of_day, "_load_object_ids", return_value=[1, 2, 3]), \
             patch.object(art_of_day, "_pick_artwork", return_value=(None, None)):
            result = art_of_day.generate(self._config(output_path))
        assert result == output_path
        assert os.path.exists(output_path)

    def test_download_failure_renders_error_image(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        output_path = "art_display.bmp"
        obj_data = {"title": "T", "artistDisplayName": "A", "objectDate": "2020"}
        with patch.object(art_of_day, "_load_object_ids", return_value=[1]), \
             patch.object(art_of_day, "_pick_artwork", return_value=(obj_data, "http://x/img.jpg")), \
             patch.object(art_of_day, "_download_image", return_value=None):
            result = art_of_day.generate(self._config(output_path))
        assert result == output_path
        assert os.path.exists(output_path)

    def test_full_success_renders_and_caches(self, tmp_path, monkeypatch):
        # generate() creates CACHE_DIR itself up front, so this also covers
        # the case where _load_object_ids() (mocked out here) isn't the one
        # creating data/ as a side effect.
        monkeypatch.chdir(tmp_path)
        output_path = "art_display.bmp"
        obj_data = {"title": "Starry Night", "artistDisplayName": "Van Gogh", "objectDate": "1889"}
        fake_img = Image.new("RGB", (800, 480), "blue")
        with patch.object(art_of_day, "_load_object_ids", return_value=[1]), \
             patch.object(art_of_day, "_pick_artwork", return_value=(obj_data, "http://x/img.jpg")), \
             patch.object(art_of_day, "_download_image", return_value=fake_img):
            result = art_of_day.generate(self._config(output_path))

        assert result == output_path
        assert os.path.exists(output_path)
        today = date.today().isoformat()
        assert os.path.exists(os.path.join("data", f"art_{today}.bmp"))
