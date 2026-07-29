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

from modules.word_of_day import (
    _daily_fallback_word,
    _fetch_rss_word,
    _fetch_mw_definition,
    _canned_definition,
    _cache_path,
    _load_cache,
    _save_cache,
    FALLBACK_WORDS,
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
