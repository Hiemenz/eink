"""
Unit tests for modules/sports_scores.py

Covers ESPN event parsing, cache TTL expiry/read/write, and the fetch_games
fallback behavior (live fetch, cache hit, stale-cache-on-error), all with
requests mocked so no real network calls are made.
"""

import sys
import os
import json
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.sports_scores import (
    _parse_events,
    _load_cache,
    _save_cache,
    _cache_path,
    fetch_games,
)


def _make_event(home_abbr="NYG", away_abbr="DAL", home_score="21", away_score="14",
                 status="Final", home_away_present=True):
    competitors = []
    if home_away_present:
        competitors.append({
            "homeAway": "home",
            "team": {"abbreviation": home_abbr},
            "score": home_score,
        })
        competitors.append({
            "homeAway": "away",
            "team": {"abbreviation": away_abbr},
            "score": away_score,
        })
    return {
        "competitions": [{"competitors": competitors}],
        "status": {"type": {"shortDetail": status}},
    }


class TestParseEvents:
    def test_parses_valid_event(self):
        events = [_make_event()]
        games = _parse_events(events)
        assert len(games) == 1
        game = games[0]
        assert game["home"] == "NYG"
        assert game["away"] == "DAL"
        assert game["home_score"] == "21"
        assert game["away_score"] == "14"
        assert game["status"] == "Final"

    def test_skips_event_missing_home_or_away(self):
        events = [_make_event(home_away_present=False)]
        assert _parse_events(events) == []

    def test_skips_malformed_event_gracefully(self):
        events = [{"competitions": "not-a-list-of-dicts"}]
        assert _parse_events(events) == []

    def test_empty_score_becomes_empty_string(self):
        events = [_make_event(home_score="", away_score="")]
        games = _parse_events(events)
        assert games[0]["home_score"] == ""
        assert games[0]["away_score"] == ""

    def test_multiple_events_all_parsed(self):
        events = [_make_event(home_abbr="A", away_abbr="B"),
                  _make_event(home_abbr="C", away_abbr="D")]
        games = _parse_events(events)
        assert [g["home"] for g in games] == ["A", "C"]

    def test_empty_events_list_returns_empty(self):
        assert _parse_events([]) == []


class TestCache:
    def test_load_cache_missing_file_returns_none(self, tmp_path):
        assert _load_cache(str(tmp_path), "nfl", ttl=300) is None

    def test_save_then_load_cache_roundtrip(self, tmp_path):
        games = [{"home": "A", "away": "B", "home_score": "1",
                  "away_score": "2", "status": "Final"}]
        _save_cache(str(tmp_path), "nfl", games)
        loaded = _load_cache(str(tmp_path), "nfl", ttl=300)
        assert loaded == games

    def test_expired_cache_returns_none(self, tmp_path):
        path = _cache_path(str(tmp_path), "nfl")
        with open(path, "w") as f:
            json.dump({"fetched_at": time.time() - 1000, "games": []}, f)
        assert _load_cache(str(tmp_path), "nfl", ttl=300) is None

    def test_corrupt_cache_file_returns_none(self, tmp_path):
        path = _cache_path(str(tmp_path), "nfl")
        with open(path, "w") as f:
            f.write("not valid json{{{")
        assert _load_cache(str(tmp_path), "nfl", ttl=300) is None


class TestFetchGames:
    def test_returns_cached_games_without_network_call(self, tmp_path):
        games = [{"home": "A", "away": "B", "home_score": "", "away_score": "", "status": ""}]
        _save_cache(str(tmp_path), "nfl", games)
        with patch("modules.sports_scores.requests.get") as mock_get:
            result = fetch_games("football", "nfl", str(tmp_path), ttl=300)
            mock_get.assert_not_called()
        assert result == games

    @patch("modules.sports_scores.requests.get")
    def test_live_fetch_success_saves_cache(self, mock_get, tmp_path):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"events": [_make_event()]}
        mock_get.return_value = mock_resp

        result = fetch_games("football", "nfl", str(tmp_path), ttl=300)
        assert len(result) == 1
        assert result[0]["home"] == "NYG"

        # Cache should now be populated
        cached = _load_cache(str(tmp_path), "nfl", ttl=300)
        assert cached == result

    @patch("modules.sports_scores.requests.get")
    def test_fetch_failure_falls_back_to_stale_cache(self, mock_get, tmp_path):
        stale_games = [{"home": "X", "away": "Y", "home_score": "", "away_score": "", "status": ""}]
        path = _cache_path(str(tmp_path), "nfl")
        with open(path, "w") as f:
            json.dump({"fetched_at": time.time() - 10_000, "games": stale_games}, f)

        mock_get.side_effect = Exception("network down")

        result = fetch_games("football", "nfl", str(tmp_path), ttl=300)
        assert result == stale_games

    @patch("modules.sports_scores.requests.get")
    def test_fetch_failure_no_cache_returns_none(self, mock_get, tmp_path):
        mock_get.side_effect = Exception("network down")
        result = fetch_games("football", "nfl", str(tmp_path), ttl=300)
        assert result is None
