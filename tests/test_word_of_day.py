"""
Unit tests for modules/word_of_day.py

Covers the deterministic daily fallback word, RSS title parsing, M-W API
response parsing (including malformed responses), the canned-definition
fallback, and the per-day JSON cache. requests/network calls are mocked.
"""

import sys
import os
import json
from datetime import date
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw

from modules.word_of_day import (
    _daily_fallback_word,
    _fetch_rss_word,
    _fetch_mw_definition,
    _canned_definition,
    _cache_path,
    _load_cache,
    _save_cache,
    _wrap,
    _render,
    generate,
    FALLBACK_WORDS,
    WIDTH,
    HEIGHT,
)


class TestDailyFallbackWord:
    def test_returns_word_from_fallback_list(self):
        word = _daily_fallback_word()
        assert word in FALLBACK_WORDS

    def test_deterministic_for_same_day(self):
        assert _daily_fallback_word() == _daily_fallback_word()


class TestFetchRssWord:
    @patch("modules.word_of_day.requests.get")
    def test_parses_word_before_colon(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = (
            "<rss><channel><item>"
            "<title>ephemeral : meaning and usage</title>"
            "</item></channel></rss>"
        )
        mock_get.return_value = resp

        assert _fetch_rss_word() == "ephemeral"

    @patch("modules.word_of_day.requests.get")
    def test_title_without_colon_used_verbatim_lowercased(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = "<rss><channel><item><title>Serendipity</title></item></channel></rss>"
        mock_get.return_value = resp

        assert _fetch_rss_word() == "serendipity"

    @patch("modules.word_of_day.requests.get")
    def test_no_items_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = "<rss><channel></channel></rss>"
        mock_get.return_value = resp

        assert _fetch_rss_word() is None

    @patch("modules.word_of_day.requests.get")
    def test_request_exception_returns_none(self, mock_get):
        mock_get.side_effect = Exception("network down")
        assert _fetch_rss_word() is None

    @patch("modules.word_of_day.requests.get")
    def test_malformed_xml_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = "not xml at all <<<"
        mock_get.return_value = resp

        assert _fetch_rss_word() is None


class TestFetchMwDefinition:
    @patch("modules.word_of_day.requests.get")
    def test_parses_full_entry(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{
            "hwi": {"hw": "e*phem*er*al", "prs": [{"mw": "i-ˈfem-rəl"}]},
            "fl": "adjective",
            "shortdef": ["lasting a short time", "transitory"],
        }]
        mock_get.return_value = resp

        result = _fetch_mw_definition("ephemeral", "fake-key")
        assert result["word"] == "ephemeral"
        assert result["pos"] == "adjective"
        assert result["definitions"] == ["lasting a short time", "transitory"]
        assert result["pronunciation"] == "i-fem-rl"

    @patch("modules.word_of_day.requests.get")
    def test_headword_falls_back_to_meta_id(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{
            "meta": {"id": "ephemeral:1"},
            "fl": "adjective",
            "shortdef": ["short-lived"],
        }]
        mock_get.return_value = resp

        result = _fetch_mw_definition("ephemeral", "fake-key")
        assert result["word"] == "ephemeral"

    @patch("modules.word_of_day.requests.get")
    def test_limits_to_three_definitions(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{
            "hwi": {"hw": "word"},
            "fl": "noun",
            "shortdef": ["one", "two", "three", "four", "five"],
        }]
        mock_get.return_value = resp

        result = _fetch_mw_definition("word", "fake-key")
        assert len(result["definitions"]) == 3

    @patch("modules.word_of_day.requests.get")
    def test_non_list_response_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"error": "not found"}
        mock_get.return_value = resp

        assert _fetch_mw_definition("gibberish", "fake-key") is None

    @patch("modules.word_of_day.requests.get")
    def test_list_of_strings_response_returns_none(self, mock_get):
        # M-W API returns a list of spelling suggestions (strings) when the
        # word isn't found — not a list of entry dicts.
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = ["ephemera", "ephemeral"]
        mock_get.return_value = resp

        assert _fetch_mw_definition("ephemerall", "fake-key") is None

    @patch("modules.word_of_day.requests.get")
    def test_empty_list_response_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_get.return_value = resp

        assert _fetch_mw_definition("word", "fake-key") is None

    @patch("modules.word_of_day.requests.get")
    def test_request_exception_returns_none(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        assert _fetch_mw_definition("word", "fake-key") is None


class TestCannedDefinition:
    def test_known_word_returns_canned_data(self):
        result = _canned_definition("ephemeral")
        assert result["pos"] == "adjective"
        assert len(result["definitions"]) == 1

    def test_unknown_word_returns_empty_skeleton(self):
        result = _canned_definition("supercalifragilistic")
        assert result["word"] == "supercalifragilistic"
        assert result["pos"] == ""
        assert result["definitions"] == []


class TestCache:
    def test_load_missing_cache_returns_none(self, tmp_path):
        assert _load_cache(str(tmp_path)) is None

    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"word": "ephemeral", "pos": "adjective", "pronunciation": "", "definitions": ["x"]}
        _save_cache(data, str(tmp_path))
        assert _load_cache(str(tmp_path)) == data

    def test_cache_path_includes_todays_date(self, tmp_path):
        path = _cache_path(str(tmp_path))
        assert date.today().isoformat() in path

    def test_corrupt_cache_returns_none(self, tmp_path):
        path = _cache_path(str(tmp_path))
        with open(path, "w") as f:
            f.write("{not valid")
        assert _load_cache(str(tmp_path)) is None


class TestWrap:
    def _draw(self):
        return ImageDraw.Draw(Image.new("RGB", (800, 480)))

    def test_short_text_single_line(self):
        draw = self._draw()
        font = draw.getfont()
        assert _wrap(draw, "short", font, 700) == ["short"]

    def test_empty_text_returns_one_empty_line(self):
        draw = self._draw()
        font = draw.getfont()
        assert _wrap(draw, "", font, 700) == [""]

    def test_wraps_long_text_into_multiple_lines(self):
        draw = self._draw()
        font = draw.getfont()
        text = " ".join(["word"] * 40)
        lines = _wrap(draw, text, font, 100)
        assert len(lines) > 1


class TestRenderFull:
    def _full_word_data(self):
        return {
            "word": "ephemeral",
            "pos": "adjective",
            "pronunciation": "i-fem-rl",
            "definitions": ["lasting a short time", "transitory", "fleeting"],
        }

    def test_renders_full_entry(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = _render(self._full_word_data(), output_path, {})
        assert result == output_path
        img = Image.open(output_path)
        assert img.size == (WIDTH, HEIGHT)

    def test_renders_without_pronunciation(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        data = self._full_word_data()
        data["pronunciation"] = ""
        result = _render(data, output_path, {})
        assert os.path.exists(result)

    def test_renders_without_pos(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        data = self._full_word_data()
        data["pos"] = ""
        result = _render(data, output_path, {})
        assert os.path.exists(result)

    def test_renders_no_definitions_shows_placeholder(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        data = self._full_word_data()
        data["definitions"] = []
        result = _render(data, output_path, {})
        assert os.path.exists(result)

    def test_renders_missing_fields_gracefully(self, tmp_path):
        """word_data with only 'word' set -- pos/pronunciation/definitions absent entirely."""
        output_path = str(tmp_path / "out.bmp")
        result = _render({"word": "test"}, output_path, {})
        assert os.path.exists(result)

    def test_very_long_word_falls_back_to_min_font_size(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        data = self._full_word_data()
        data["word"] = "supercalifragilisticexpialidocious" * 2
        result = _render(data, output_path, {})
        assert os.path.exists(result)

    def test_many_long_definitions_get_clipped_not_crashed(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        data = self._full_word_data()
        data["definitions"] = [
            " ".join(["longdefinitionword"] * 30),
            " ".join(["another"] * 30),
            " ".join(["third"] * 30),
        ]
        result = _render(data, output_path, {})
        assert os.path.exists(result)

    def test_creates_parent_dirs(self, tmp_path):
        output_path = str(tmp_path / "nested" / "dir" / "out.bmp")
        _render(self._full_word_data(), output_path, {})
        assert os.path.exists(output_path)


class TestGenerateIntegration:
    def _word_data(self):
        return {"word": "ephemeral", "pos": "adjective", "pronunciation": "", "definitions": ["x"]}

    def test_uses_cache_when_present_skips_all_fetching(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "cache")
        _save_cache(self._word_data(), cache_dir)

        with patch("modules.word_of_day._fetch_rss_word") as mock_rss, \
             patch("modules.word_of_day._fetch_mw_definition") as mock_api:
            result = generate({
                "word_of_day": {"output_path": output_path, "cache_dir": cache_dir}
            })
            mock_rss.assert_not_called()
            mock_api.assert_not_called()

        assert result == output_path
        assert os.path.exists(output_path)

    @patch("modules.word_of_day._fetch_mw_definition")
    @patch("modules.word_of_day._fetch_rss_word")
    def test_api_key_set_uses_api_definition(self, mock_rss, mock_api, tmp_path):
        mock_rss.return_value = "ephemeral"
        mock_api.return_value = {
            "word": "ephemeral", "pos": "adjective", "pronunciation": "", "definitions": ["from api"]
        }
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "cache")

        result = generate({
            "word_of_day": {"output_path": output_path, "cache_dir": cache_dir, "api_key": "key123"}
        })

        assert os.path.exists(result)
        mock_api.assert_called_once_with("ephemeral", "key123")
        cached = _load_cache(cache_dir)
        assert cached["definitions"] == ["from api"]

    @patch("modules.word_of_day._fetch_mw_definition")
    @patch("modules.word_of_day._fetch_rss_word")
    def test_api_failure_falls_back_to_canned(self, mock_rss, mock_api, tmp_path):
        mock_rss.return_value = "ephemeral"
        mock_api.return_value = None
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "cache")

        result = generate({
            "word_of_day": {"output_path": output_path, "cache_dir": cache_dir, "api_key": "key123"}
        })

        assert os.path.exists(result)
        cached = _load_cache(cache_dir)
        assert cached["pos"] == "adjective"  # canned def for "ephemeral"

    @patch("modules.word_of_day._fetch_mw_definition")
    @patch("modules.word_of_day._fetch_rss_word")
    def test_no_api_key_never_calls_api(self, mock_rss, mock_api, tmp_path):
        mock_rss.return_value = "ephemeral"
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "cache")

        generate({"word_of_day": {"output_path": output_path, "cache_dir": cache_dir}})

        mock_api.assert_not_called()

    @patch("modules.word_of_day._daily_fallback_word")
    @patch("modules.word_of_day._fetch_rss_word")
    def test_rss_failure_uses_daily_fallback_word(self, mock_rss, mock_fallback, tmp_path):
        mock_rss.return_value = None
        mock_fallback.return_value = "sanguine"
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "cache")

        generate({"word_of_day": {"output_path": output_path, "cache_dir": cache_dir}})

        mock_fallback.assert_called_once()
        cached = _load_cache(cache_dir)
        assert cached["word"] == "sanguine"

    @patch("modules.word_of_day._fetch_rss_word")
    def test_default_output_path_and_cache_dir(self, mock_rss, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_rss.return_value = "ephemeral"

        result = generate({})

        assert result == "images/wotd_display.bmp"
        assert os.path.exists(result)
        assert os.path.exists(_cache_path("data/"))
