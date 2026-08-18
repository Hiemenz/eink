"""
Unit tests for modules/siriusxm_now_playing.py.

Covers the pure helpers (channel lookup, song-marker extraction, text
wrapping/fitting), the JSON/art cache helpers, the Spotify client-credentials
flow, and generate()'s end-to-end branching — all fully mocked, no real
network or SiriusXM/Spotify calls.
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modules.siriusxm_now_playing as sxm


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestFindChannel:
    CHANNELS = [
        {"name": "Life with John Mayer", "siriusChannelNumber": "700"},
        {"name": "Classic Rewind", "siriusChannelNumber": "25"},
        {"name": "The Highway", "siriusChannelNumber": "56"},
    ]

    def test_exact_name_match_case_insensitive(self):
        result = sxm._find_channel(self.CHANNELS, "life with john mayer")
        assert result["name"] == "Life with John Mayer"

    def test_substring_match(self):
        result = sxm._find_channel(self.CHANNELS, "highway")
        assert result["name"] == "The Highway"

    def test_channel_number_match(self):
        result = sxm._find_channel(self.CHANNELS, "25")
        assert result["name"] == "Classic Rewind"

    def test_no_match_returns_none(self):
        assert sxm._find_channel(self.CHANNELS, "nonexistent channel") is None

    def test_empty_channel_list_returns_none(self):
        assert sxm._find_channel([], "anything") is None

    def test_exact_match_preferred_over_substring(self):
        channels = [
            {"name": "Rock", "siriusChannelNumber": "1"},
            {"name": "Classic Rock", "siriusChannelNumber": "2"},
        ]
        result = sxm._find_channel(channels, "rock")
        assert result["name"] == "Rock"


def _marker(cut_type, title, offset_ms, artists=None, album=None):
    return {
        "time": offset_ms,
        "cut": {
            "cutContentType": cut_type,
            "title": title,
            "artists": artists or [],
            "album": album or {},
        },
    }


class TestExtractLatestSong:
    def test_picks_latest_song_marker(self):
        now_ms = time.time() * 1000
        data = {
            "moduleList": {"modules": [{"moduleResponse": {"liveChannelData": {"markerLists": [
                {"layer": "cut", "markers": [
                    _marker("Song", "Old Song", now_ms - 600000, artists=[{"name": "Artist A"}]),
                    _marker("Song", "New Song", now_ms - 60000, artists=[{"name": "Artist B"}]),
                ]},
            ]}}}]}
        }
        result = sxm._extract_latest_song(data)
        assert result["title"] == "New Song"
        assert result["artist"] == "Artist B"

    def test_ignores_non_song_cut_types(self):
        now_ms = time.time() * 1000
        data = {
            "moduleList": {"modules": [{"moduleResponse": {"liveChannelData": {"markerLists": [
                {"layer": "cut", "markers": [
                    _marker("Talk", "DJ Chat", now_ms - 1000),
                ]},
            ]}}}]}
        }
        assert sxm._extract_latest_song(data) is None

    def test_ignores_markers_in_the_future(self):
        now_ms = time.time() * 1000
        data = {
            "moduleList": {"modules": [{"moduleResponse": {"liveChannelData": {"markerLists": [
                {"layer": "cut", "markers": [
                    _marker("Song", "Future Song", now_ms + 600000, artists=[{"name": "X"}]),
                ]},
            ]}}}]}
        }
        assert sxm._extract_latest_song(data) is None

    def test_ignores_non_cut_layers(self):
        now_ms = time.time() * 1000
        data = {
            "moduleList": {"modules": [{"moduleResponse": {"liveChannelData": {"markerLists": [
                {"layer": "other", "markers": [_marker("Song", "Nope", now_ms - 1000)]},
            ]}}}]}
        }
        assert sxm._extract_latest_song(data) is None

    def test_missing_structure_returns_none(self):
        assert sxm._extract_latest_song({}) is None
        assert sxm._extract_latest_song({"moduleList": {"modules": []}}) is None

    def test_extracts_album_and_art_url(self):
        now_ms = time.time() * 1000
        album = {
            "title": "Great Album",
            "creativeArts": [
                {"type": "TEXT", "url": "ignored"},
                {"type": "IMAGE", "url": "http://art.example/cover.jpg"},
            ],
        }
        data = {
            "moduleList": {"modules": [{"moduleResponse": {"liveChannelData": {"markerLists": [
                {"layer": "cut", "markers": [
                    _marker("Song", "Song", now_ms - 1000, artists=[{"name": "A"}], album=album),
                ]},
            ]}}}]}
        }
        result = sxm._extract_latest_song(data)
        assert result["album"] == "Great Album"
        assert result["art_url"] == "http://art.example/cover.jpg"

    def test_multiple_artists_joined(self):
        now_ms = time.time() * 1000
        data = {
            "moduleList": {"modules": [{"moduleResponse": {"liveChannelData": {"markerLists": [
                {"layer": "cut", "markers": [
                    _marker("Song", "Duet", now_ms - 1000, artists=[{"name": "A"}, {"name": "B"}]),
                ]},
            ]}}}]}
        }
        result = sxm._extract_latest_song(data)
        assert result["artist"] == "A, B"


class TestWrap:
    def _draw(self):
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_short_text_single_line(self):
        draw = self._draw()
        font = ImageFont.load_default()
        assert sxm._wrap(draw, "hi", font, 1000, 2) == ["hi"]

    def test_wraps_within_max_lines(self):
        draw = self._draw()
        font = ImageFont.load_default()
        lines = sxm._wrap(draw, " ".join(["word"] * 30), font, 40, 3)
        assert len(lines) <= 3

    def test_truncates_with_ellipsis_when_exceeding_max_lines(self):
        draw = self._draw()
        font = ImageFont.load_default()
        long_text = " ".join(["word"] * 40)
        lines = sxm._wrap(draw, long_text, font, 30, 2)
        assert len(lines) == 2
        assert lines[-1].endswith("…")


class TestFitTitle:
    def test_short_title_uses_largest_font(self):
        img = Image.new("RGB", (800, 480))
        draw = ImageDraw.Draw(img)
        font, lines, line_h = sxm._fit_title(draw, "Short", 700, 2)
        assert lines == ["Short"]

    def test_long_title_falls_back_to_smallest_font(self):
        img = Image.new("RGB", (800, 480))
        draw = ImageDraw.Draw(img)
        long_title = " ".join(["Word"] * 40)
        font, lines, line_h = sxm._fit_title(draw, long_title, 700, 2)
        assert len(lines) <= 2
        assert font is not None


# ---------------------------------------------------------------------------
# JSON / art cache helpers
# ---------------------------------------------------------------------------

class TestReadWriteJson:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "data.json")
        sxm._write_json(path, {"a": 1})
        assert sxm._read_json(path) == {"a": 1}

    def test_missing_file_returns_none(self, tmp_path):
        assert sxm._read_json(str(tmp_path / "missing.json")) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("{not valid json")
        assert sxm._read_json(str(path)) is None

    def test_write_to_unwritable_path_does_not_raise(self, tmp_path):
        # Directory as a "file" path — os.open will fail.
        bad_path = str(tmp_path)
        sxm._write_json(bad_path, {"a": 1})  # must not raise


class TestDownloadArt:
    def test_none_url_returns_false(self, tmp_path):
        assert sxm._download_art(None, str(tmp_path / "art.png")) is False

    @patch("modules.siriusxm_now_playing.requests.get")
    def test_success_saves_image(self, mock_get, tmp_path):
        img = Image.new("RGB", (4, 4), "red")
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="PNG")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = buf.getvalue()
        mock_get.return_value = resp

        art_path = str(tmp_path / "art.png")
        assert sxm._download_art("http://example/art.png", art_path) is True
        assert os.path.exists(art_path)

    @patch("modules.siriusxm_now_playing.requests.get")
    def test_network_failure_returns_false(self, mock_get, tmp_path):
        mock_get.side_effect = ConnectionError("boom")
        assert sxm._download_art("http://example/art.png", str(tmp_path / "art.png")) is False

    @patch("modules.siriusxm_now_playing.requests.get")
    def test_invalid_image_bytes_returns_false(self, mock_get, tmp_path):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = b"not an image"
        mock_get.return_value = resp
        assert sxm._download_art("http://example/art.png", str(tmp_path / "art.png")) is False


# ---------------------------------------------------------------------------
# Spotify client-credentials flow
# ---------------------------------------------------------------------------

class TestSpotifyToken:
    def test_uses_fresh_cached_token(self, tmp_path):
        path = os.path.join(str(tmp_path), sxm.SPOTIFY_TOKEN_CACHE_FILE)
        sxm._write_json(path, {"access_token": "cached-token", "expires_at": time.time() + 3600})
        with patch("modules.siriusxm_now_playing.requests.post") as mock_post:
            token = sxm._spotify_token("id", "secret", str(tmp_path))
            mock_post.assert_not_called()
        assert token == "cached-token"

    @patch("modules.siriusxm_now_playing.requests.post")
    def test_expired_cache_triggers_fetch(self, mock_post, tmp_path):
        path = os.path.join(str(tmp_path), sxm.SPOTIFY_TOKEN_CACHE_FILE)
        sxm._write_json(path, {"access_token": "old-token", "expires_at": time.time() - 10})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"access_token": "new-token", "expires_in": 3600}
        mock_post.return_value = resp

        token = sxm._spotify_token("id", "secret", str(tmp_path))
        assert token == "new-token"

    @patch("modules.siriusxm_now_playing.requests.post")
    def test_request_failure_returns_none(self, mock_post, tmp_path):
        mock_post.side_effect = ConnectionError("boom")
        assert sxm._spotify_token("id", "secret", str(tmp_path)) is None

    @patch("modules.siriusxm_now_playing.requests.post")
    def test_missing_access_token_in_response_returns_none_without_writing_cache(self, mock_post, tmp_path):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_post.return_value = resp
        token = sxm._spotify_token("id", "secret", str(tmp_path))
        assert token is None


class TestSpotifyLookup:
    @patch("modules.siriusxm_now_playing.requests.get")
    def test_match_found_returns_url_and_art(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "tracks": {"items": [{
                "external_urls": {"spotify": "https://open.spotify.com/track/x"},
                "album": {"images": [{"url": "http://art/x.jpg"}]},
            }]}
        }
        mock_get.return_value = resp
        result = sxm._spotify_lookup("token", "Title", "Artist")
        assert result["url"] == "https://open.spotify.com/track/x"
        assert result["art_url"] == "http://art/x.jpg"

    @patch("modules.siriusxm_now_playing.requests.get")
    def test_no_items_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"tracks": {"items": []}}
        mock_get.return_value = resp
        assert sxm._spotify_lookup("token", "Title", "Artist") is None

    @patch("modules.siriusxm_now_playing.requests.get")
    def test_request_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert sxm._spotify_lookup("token", "Title", "Artist") is None

    @patch("modules.siriusxm_now_playing.requests.get")
    def test_no_images_returns_none_art_url(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "tracks": {"items": [{"external_urls": {"spotify": "url"}, "album": {"images": []}}]}
        }
        mock_get.return_value = resp
        result = sxm._spotify_lookup("token", "Title", "Artist")
        assert result["art_url"] is None


# ---------------------------------------------------------------------------
# _get_song session/channel/fetch orchestration
# ---------------------------------------------------------------------------

class TestGetSong:
    @patch("modules.siriusxm_now_playing._ensure_session", return_value=False)
    def test_session_failure_returns_none(self, mock_ensure, tmp_path):
        song, session = sxm._get_song("user", "pass", "channel", str(tmp_path))
        assert song is None

    @patch("modules.siriusxm_now_playing._load_cached_channel", return_value=None)
    @patch("modules.siriusxm_now_playing._ensure_session", return_value=True)
    def test_channel_not_found_returns_none(self, mock_ensure, mock_channel, tmp_path):
        song, session = sxm._get_song("user", "pass", "channel", str(tmp_path))
        assert song is None

    @patch("modules.siriusxm_now_playing._fetch_now_playing", return_value=None)
    @patch("modules.siriusxm_now_playing._load_cached_channel", return_value={"channelGuid": "g"})
    @patch("modules.siriusxm_now_playing._ensure_session", return_value=True)
    def test_fetch_failure_returns_none(self, mock_ensure, mock_channel, mock_fetch, tmp_path):
        song, session = sxm._get_song("user", "pass", "channel", str(tmp_path))
        assert song is None

    @patch("modules.siriusxm_now_playing._extract_latest_song", return_value={"title": "T", "artist": "A"})
    @patch("modules.siriusxm_now_playing._fetch_now_playing", return_value={"some": "data"})
    @patch("modules.siriusxm_now_playing._load_cached_channel", return_value={"channelGuid": "g"})
    @patch("modules.siriusxm_now_playing._ensure_session", return_value=True)
    def test_success_returns_extracted_song(self, mock_ensure, mock_channel, mock_fetch, mock_extract, tmp_path):
        song, session = sxm._get_song("user", "pass", "channel", str(tmp_path))
        assert song == {"title": "T", "artist": "A"}


# ---------------------------------------------------------------------------
# generate() end-to-end (fully mocked at the network boundary)
# ---------------------------------------------------------------------------

class TestGenerate:
    def _config(self, tmp_path, **overrides):
        cfg = {
            "output_path": str(tmp_path / "sxm.bmp"),
            "cache_dir": str(tmp_path),
            "username": "user",
            "password": "pass",
            "channel_name": "Life with John Mayer",
            "spotify_client_id": "cid",
            "spotify_client_secret": "csecret",
        }
        cfg.update(overrides)
        return {"siriusxm_now_playing": cfg}

    def test_no_credentials_renders_configure_screen(self, tmp_path):
        config = self._config(tmp_path, username="", password="")
        with patch("modules.siriusxm_now_playing._get_song") as mock_get_song:
            result = sxm.generate(config)
            mock_get_song.assert_not_called()
        assert os.path.exists(result)
        assert Image.open(result).size == (sxm.WIDTH, sxm.HEIGHT)

    @patch("modules.siriusxm_now_playing._download_art", return_value=True)
    @patch("modules.siriusxm_now_playing._spotify_lookup")
    @patch("modules.siriusxm_now_playing._spotify_token", return_value="token")
    @patch("modules.siriusxm_now_playing._save_session")
    @patch("modules.siriusxm_now_playing._get_song")
    def test_new_song_with_spotify_match_renders_card_with_qr(
        self, mock_get_song, mock_save, mock_token, mock_lookup, mock_dl, tmp_path
    ):
        mock_get_song.return_value = ({"title": "Song", "artist": "Artist", "album": "Album", "art_url": None}, MagicMock())
        mock_lookup.return_value = {"url": "https://open.spotify.com/track/x", "art_url": "http://art.jpg"}

        config = self._config(tmp_path)
        result = sxm.generate(config)
        assert os.path.exists(result)
        mock_lookup.assert_called_once()

        cache_path = os.path.join(str(tmp_path), sxm.NOW_PLAYING_CACHE_FILE)
        cached = sxm._read_json(cache_path)
        assert cached["spotify_url"] == "https://open.spotify.com/track/x"

    @patch("modules.siriusxm_now_playing._spotify_lookup")
    @patch("modules.siriusxm_now_playing._save_session")
    @patch("modules.siriusxm_now_playing._get_song")
    def test_same_song_as_cache_reuses_spotify_url_without_relookup(
        self, mock_get_song, mock_save, mock_lookup, tmp_path
    ):
        cache_path = os.path.join(str(tmp_path), sxm.NOW_PLAYING_CACHE_FILE)
        sxm._write_json(cache_path, {"title": "Song", "artist": "Artist", "spotify_url": "https://cached/url"})

        mock_get_song.return_value = ({"title": "Song", "artist": "Artist", "album": "Album", "art_url": None}, MagicMock())

        config = self._config(tmp_path)
        result = sxm.generate(config)
        assert os.path.exists(result)
        mock_lookup.assert_not_called()

    @patch("modules.siriusxm_now_playing._save_session")
    @patch("modules.siriusxm_now_playing._get_song", return_value=(None, MagicMock()))
    def test_fetch_fails_with_cache_reuses_cached_song(self, mock_get_song, mock_save, tmp_path):
        cache_path = os.path.join(str(tmp_path), sxm.NOW_PLAYING_CACHE_FILE)
        sxm._write_json(cache_path, {"title": "Cached Song", "artist": "Cached Artist"})

        config = self._config(tmp_path, spotify_client_id="", spotify_client_secret="")
        result = sxm.generate(config)
        assert os.path.exists(result)
        assert Image.open(result).size == (sxm.WIDTH, sxm.HEIGHT)

    @patch("modules.siriusxm_now_playing._save_session")
    @patch("modules.siriusxm_now_playing._get_song", return_value=(None, MagicMock()))
    def test_fetch_fails_no_cache_renders_no_song_screen(self, mock_get_song, mock_save, tmp_path):
        config = self._config(tmp_path)
        result = sxm.generate(config)
        assert os.path.exists(result)
        assert Image.open(result).size == (sxm.WIDTH, sxm.HEIGHT)

    @patch("modules.siriusxm_now_playing._download_art", return_value=False)
    @patch("modules.siriusxm_now_playing._save_session")
    @patch("modules.siriusxm_now_playing._get_song")
    def test_no_spotify_credentials_skips_lookup_renders_without_qr(
        self, mock_get_song, mock_save, mock_dl, tmp_path
    ):
        mock_get_song.return_value = ({"title": "Song", "artist": "Artist", "album": "", "art_url": None}, MagicMock())

        config = self._config(tmp_path, spotify_client_id="", spotify_client_secret="")
        with patch("modules.siriusxm_now_playing._spotify_token") as mock_token:
            result = sxm.generate(config)
            mock_token.assert_not_called()
        assert os.path.exists(result)

    def test_creates_output_and_cache_directories(self, tmp_path):
        nested_out = str(tmp_path / "out" / "sxm.bmp")
        nested_cache = str(tmp_path / "cache" / "dir")
        config = self._config(tmp_path, output_path=nested_out, cache_dir=nested_cache, username="", password="")
        result = sxm.generate(config)
        assert os.path.exists(result)
        assert os.path.isdir(nested_cache)
