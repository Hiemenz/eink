"""
Unit tests for modules/now_playing.py.
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import now_playing as np


class TestLargestImageUrl:
    def test_picks_last_nonempty_url(self):
        images = [
            {"#text": "", "size": "small"},
            {"#text": "http://x/med.jpg", "size": "medium"},
            {"#text": "http://x/large.jpg", "size": "large"},
        ]
        assert np._largest_image_url(images) == "http://x/large.jpg"

    def test_skips_trailing_empty_entries(self):
        images = [
            {"#text": "http://x/med.jpg", "size": "medium"},
            {"#text": "", "size": "large"},
        ]
        assert np._largest_image_url(images) == "http://x/med.jpg"

    def test_empty_list_returns_none(self):
        assert np._largest_image_url([]) is None

    def test_none_input_returns_none(self):
        assert np._largest_image_url(None) is None

    def test_all_empty_returns_none(self):
        images = [{"#text": ""}, {"#text": "  "}]
        assert np._largest_image_url(images) is None


class TestFetchTrack:
    @patch("modules.now_playing.requests.get")
    def test_now_playing_track(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "recenttracks": {
                "track": [{
                    "name": "Song A",
                    "artist": {"#text": "Artist A"},
                    "album": {"#text": "Album A"},
                    "image": [{"#text": "http://img/large.jpg"}],
                    "@attr": {"nowplaying": "true"},
                }]
            }
        }
        mock_get.return_value = resp
        track = np._fetch_track("key", "user")
        assert track["name"] == "Song A"
        assert track["artist"] == "Artist A"
        assert track["album"] == "Album A"
        assert track["nowplaying"] is True
        assert track["played_uts"] is None
        assert track["image_url"] == "http://img/large.jpg"

    @patch("modules.now_playing.requests.get")
    def test_last_played_track_has_timestamp(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "recenttracks": {
                "track": [{
                    "name": "Song B",
                    "artist": {"#text": "Artist B"},
                    "album": {"#text": ""},
                    "image": [],
                    "date": {"uts": "1700000000"},
                }]
            }
        }
        mock_get.return_value = resp
        track = np._fetch_track("key", "user")
        assert track["nowplaying"] is False
        assert track["played_uts"] == 1700000000

    @patch("modules.now_playing.requests.get")
    def test_single_track_dict_not_list(self, mock_get):
        """Last.fm returns a bare dict (not a list) when there's exactly one track."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "recenttracks": {
                "track": {
                    "name": "Solo Song",
                    "artist": {"#text": "Solo Artist"},
                }
            }
        }
        mock_get.return_value = resp
        track = np._fetch_track("key", "user")
        assert track["name"] == "Solo Song"

    @patch("modules.now_playing.requests.get")
    def test_missing_name_defaults_to_unknown(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "recenttracks": {"track": [{"artist": {}, "album": {}}]}
        }
        mock_get.return_value = resp
        track = np._fetch_track("key", "user")
        assert track["name"] == "Unknown Track"
        assert track["artist"] == "Unknown Artist"

    @patch("modules.now_playing.requests.get")
    def test_no_tracks_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"recenttracks": {"track": []}}
        mock_get.return_value = resp
        assert np._fetch_track("key", "user") is None

    @patch("modules.now_playing.requests.get")
    def test_api_error_field_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"error": 10, "message": "Invalid API key"}
        mock_get.return_value = resp
        assert np._fetch_track("key", "user") is None

    @patch("modules.now_playing.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert np._fetch_track("key", "user") is None

    @patch("modules.now_playing.requests.get")
    def test_malformed_json_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("bad json")
        mock_get.return_value = resp
        assert np._fetch_track("key", "user") is None

    @patch("modules.now_playing.requests.get")
    def test_bad_date_uts_handled_gracefully(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "recenttracks": {
                "track": [{
                    "name": "Song",
                    "artist": {"#text": "Artist"},
                    "date": {"uts": "not-a-number"},
                }]
            }
        }
        mock_get.return_value = resp
        track = np._fetch_track("key", "user")
        assert track["played_uts"] is None


class TestRelativeTime:
    def test_none_returns_empty(self):
        assert np._relative_time(None) == ""

    def test_just_now(self):
        assert np._relative_time(int(time.time()) - 5) == "just now"

    def test_minutes_ago(self):
        assert np._relative_time(int(time.time()) - 120) == "2 min ago"

    def test_one_hour_ago_singular(self):
        assert np._relative_time(int(time.time()) - 3600) == "1 hr ago"

    def test_multiple_hours_ago_plural(self):
        assert np._relative_time(int(time.time()) - 3 * 3600) == "3 hrs ago"

    def test_one_day_ago_singular(self):
        assert np._relative_time(int(time.time()) - 86400) == "1 day ago"

    def test_multiple_days_ago_plural(self):
        assert np._relative_time(int(time.time()) - 3 * 86400) == "3 days ago"


class TestWrap:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_wraps_within_max_lines(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        lines = np._wrap(draw, "one two three four five six seven eight nine", font, 40, 3)
        assert len(lines) <= 3

    def test_short_text_single_line(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        lines = np._wrap(draw, "hi", font, 1000, 3)
        assert lines == ["hi"]

    def test_truncation_adds_ellipsis(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        long_text = " ".join(["word"] * 40)
        lines = np._wrap(draw, long_text, font, 30, 2)
        assert len(lines) == 2
        assert lines[-1].endswith("…")


class TestGetTrack:
    def test_serves_fresh_cache_without_network(self, tmp_path):
        cache_json, art_path = np._cache_paths(str(tmp_path))
        fresh_track = {"name": "Cached Song", "fetched_at": int(time.time())}
        with open(cache_json, "w") as f:
            json.dump(fresh_track, f)

        with patch("modules.now_playing._fetch_track") as mock_fetch:
            track, art_available = np._get_track("key", "user", str(tmp_path))
            mock_fetch.assert_not_called()
        assert track["name"] == "Cached Song"

    def test_stale_cache_triggers_fetch(self, tmp_path):
        cache_json, art_path = np._cache_paths(str(tmp_path))
        stale_track = {"name": "Old Song", "fetched_at": int(time.time()) - 1000}
        with open(cache_json, "w") as f:
            json.dump(stale_track, f)

        new_track = {"name": "New Song", "image_url": None, "fetched_at": int(time.time())}
        with patch("modules.now_playing._fetch_track", return_value=new_track):
            track, art_available = np._get_track("key", "user", str(tmp_path))
        assert track["name"] == "New Song"

    def test_fetch_failure_falls_back_to_stale_cache(self, tmp_path):
        cache_json, art_path = np._cache_paths(str(tmp_path))
        stale_track = {"name": "Old Song", "fetched_at": int(time.time()) - 1000}
        with open(cache_json, "w") as f:
            json.dump(stale_track, f)

        with patch("modules.now_playing._fetch_track", return_value=None):
            track, art_available = np._get_track("key", "user", str(tmp_path))
        assert track["name"] == "Old Song"

    def test_fetch_failure_no_cache_returns_none(self, tmp_path):
        with patch("modules.now_playing._fetch_track", return_value=None):
            track, art_available = np._get_track("key", "user", str(tmp_path))
        assert track is None
        assert art_available is False

    def test_art_redownloaded_when_url_changes(self, tmp_path):
        cache_json, art_path = np._cache_paths(str(tmp_path))
        old_track = {"name": "Old", "image_url": "http://old.jpg", "fetched_at": int(time.time()) - 1000}
        with open(cache_json, "w") as f:
            json.dump(old_track, f)
        with open(art_path, "w") as f:
            f.write("fake art bytes")

        new_track = {"name": "New", "image_url": "http://new.jpg", "fetched_at": int(time.time())}
        with patch("modules.now_playing._fetch_track", return_value=new_track), \
             patch("modules.now_playing._download_art", return_value=True) as mock_dl:
            np._get_track("key", "user", str(tmp_path))
            mock_dl.assert_called_once_with("http://new.jpg", art_path)

    def test_art_not_redownloaded_when_url_unchanged(self, tmp_path):
        cache_json, art_path = np._cache_paths(str(tmp_path))
        same_url = "http://same.jpg"
        old_track = {"name": "Old", "image_url": same_url, "fetched_at": int(time.time()) - 1000}
        with open(cache_json, "w") as f:
            json.dump(old_track, f)
        with open(art_path, "w") as f:
            f.write("fake art bytes")

        new_track = {"name": "New", "image_url": same_url, "fetched_at": int(time.time())}
        with patch("modules.now_playing._fetch_track", return_value=new_track), \
             patch("modules.now_playing._download_art") as mock_dl:
            np._get_track("key", "user", str(tmp_path))
            mock_dl.assert_not_called()
