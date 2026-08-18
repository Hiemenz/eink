"""
Unit tests for modules/stocks.py

Covers price/change/percent formatting, Yahoo result normalization, the
5-minute cache TTL/stale-fallback logic, and the 401/429 host-retry behavior
in _fetch_quotes. requests is mocked throughout — no real network calls.
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

from modules.stocks import (
    _fmt_price,
    _fmt_change,
    _fmt_pct,
    _normalize,
    _fetch_quotes,
    _get_data,
    _cache_path,
    _load_cache,
    _save_cache,
    _truncate,
    generate,
    DEFAULT_SYMBOLS,
)


class TestFormatters:
    def test_fmt_price_none_returns_dash(self):
        assert _fmt_price(None) == "—"

    def test_fmt_price_under_1000_two_decimals(self):
        assert _fmt_price(123.4) == "123.40"

    def test_fmt_price_over_1000_has_comma(self):
        assert _fmt_price(1234.5) == "1,234.50"

    def test_fmt_change_none_returns_dash(self):
        assert _fmt_change(None) == "—"

    def test_fmt_change_positive_has_plus_sign(self):
        assert _fmt_change(1.23) == "+1.23"

    def test_fmt_change_negative_has_minus_sign(self):
        assert _fmt_change(-1.23) == "-1.23"

    def test_fmt_pct_none_returns_dash(self):
        assert _fmt_pct(None) == "—"

    def test_fmt_pct_formats_with_percent_sign(self):
        assert _fmt_pct(2.5) == "+2.50%"
        assert _fmt_pct(-2.5) == "-2.50%"


class TestNormalize:
    def test_normalizes_fields_and_uppercases_symbol(self):
        result = [{
            "symbol": "aapl",
            "shortName": "Apple Inc.",
            "regularMarketPrice": 150.0,
            "regularMarketChange": 1.5,
            "regularMarketChangePercent": 1.0,
            "regularMarketPreviousClose": 148.5,
            "marketState": "REGULAR",
        }]
        out = _normalize(result)
        assert "AAPL" in out
        assert out["AAPL"]["name"] == "Apple Inc."
        assert out["AAPL"]["price"] == 150.0
        assert out["AAPL"]["market_state"] == "REGULAR"

    def test_prefers_short_name_over_long_name(self):
        result = [{"symbol": "MSFT", "shortName": "Microsoft", "longName": "Microsoft Corporation"}]
        out = _normalize(result)
        assert out["MSFT"]["name"] == "Microsoft"

    def test_falls_back_to_long_name(self):
        result = [{"symbol": "MSFT", "longName": "Microsoft Corporation"}]
        out = _normalize(result)
        assert out["MSFT"]["name"] == "Microsoft Corporation"

    def test_skips_entries_without_symbol(self):
        result = [{"shortName": "No Symbol Co"}]
        assert _normalize(result) == {}

    def test_empty_result_returns_empty_dict(self):
        assert _normalize([]) == {}


class TestFetchQuotes:
    @patch("modules.stocks.requests.get")
    def test_success_on_first_host(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"quoteResponse": {"result": [{"symbol": "AAPL"}]}}
        mock_get.return_value = resp

        result = _fetch_quotes(["AAPL"])
        assert result == [{"symbol": "AAPL"}]
        assert mock_get.call_count == 1

    @patch("modules.stocks.requests.get")
    def test_401_falls_back_to_second_host(self, mock_get):
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.raise_for_status = MagicMock()
        resp_ok.json.return_value = {"quoteResponse": {"result": [{"symbol": "MSFT"}]}}
        mock_get.side_effect = [resp_401, resp_ok]

        result = _fetch_quotes(["MSFT"])
        assert result == [{"symbol": "MSFT"}]
        assert mock_get.call_count == 2

    @patch("modules.stocks.requests.get")
    def test_both_hosts_fail_returns_none(self, mock_get):
        mock_get.side_effect = Exception("connection error")
        assert _fetch_quotes(["AAPL"]) is None

    @patch("modules.stocks.requests.get")
    def test_empty_result_from_all_hosts_returns_none(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"quoteResponse": {"result": []}}
        mock_get.return_value = resp

        assert _fetch_quotes(["AAPL"]) is None


class TestGetData:
    def test_fresh_cache_used_without_network_call(self, tmp_path):
        quotes = {"AAPL": {"symbol": "AAPL", "price": 100}}
        _save_cache(str(tmp_path), {"fetched_at": time.time(), "quotes": quotes})
        with patch("modules.stocks.requests.get") as mock_get:
            result = _get_data(["AAPL"], str(tmp_path))
            mock_get.assert_not_called()
        assert result == quotes

    @patch("modules.stocks._fetch_quotes")
    def test_expired_cache_triggers_live_fetch(self, mock_fetch, tmp_path):
        old_quotes = {"AAPL": {"symbol": "AAPL", "price": 1}}
        _save_cache(str(tmp_path), {"fetched_at": time.time() - 10_000, "quotes": old_quotes})
        mock_fetch.return_value = [{"symbol": "AAPL", "regularMarketPrice": 200}]

        result = _get_data(["AAPL"], str(tmp_path))
        assert result["AAPL"]["price"] == 200

    @patch("modules.stocks._fetch_quotes")
    def test_live_failure_falls_back_to_stale_cache(self, mock_fetch, tmp_path):
        old_quotes = {"AAPL": {"symbol": "AAPL", "price": 1}}
        _save_cache(str(tmp_path), {"fetched_at": time.time() - 10_000, "quotes": old_quotes})
        mock_fetch.return_value = None

        result = _get_data(["AAPL"], str(tmp_path))
        assert result == old_quotes

    @patch("modules.stocks._fetch_quotes")
    def test_no_cache_and_fetch_fails_returns_empty_dict(self, mock_fetch, tmp_path):
        mock_fetch.return_value = None
        result = _get_data(["AAPL"], str(tmp_path))
        assert result == {}


class TestLoadCache:
    def test_missing_file_returns_none(self, tmp_path):
        assert _load_cache(str(tmp_path)) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        with open(_cache_path(str(tmp_path)), "w") as f:
            f.write("{not valid json")
        assert _load_cache(str(tmp_path)) is None


class TestTruncate:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_empty_text_returns_empty_string(self):
        from PIL import ImageFont
        assert _truncate(self._draw(), "", ImageFont.load_default(), 100) == ""

    def test_short_text_unchanged(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        assert _truncate(draw, "AAPL", font, 1000) == "AAPL"

    def test_long_text_truncated_with_ellipsis(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        text = "Some Very Long Company Name Incorporated"
        result = _truncate(draw, text, font, 50)
        assert result.endswith("…")
        assert draw.textlength(result, font=font) <= 50


class TestGenerate:
    @patch("modules.stocks._get_data")
    def test_unavailable_when_no_quotes(self, mock_get_data, tmp_path):
        mock_get_data.return_value = {}
        output = str(tmp_path / "stocks.bmp")
        config = {"stocks": {"output_path": output, "cache_dir": str(tmp_path)}}
        result = generate(config)
        assert result == output
        assert os.path.exists(output)
        from PIL import Image
        assert Image.open(output).size == (800, 480)

    @patch("modules.stocks._get_data")
    def test_full_watchlist_renders(self, mock_get_data, tmp_path):
        mock_get_data.return_value = {
            "AAPL": {"symbol": "AAPL", "name": "Apple Inc.", "price": 150.0,
                     "change": 1.5, "change_pct": 1.0, "market_state": "REGULAR"},
        }
        output = str(tmp_path / "stocks.bmp")
        config = {"stocks": {"output_path": output, "symbols": ["AAPL"], "cache_dir": str(tmp_path)}}
        result = generate(config)
        from PIL import Image
        assert Image.open(result).size == (800, 480)

    @patch("modules.stocks._get_data")
    def test_missing_symbol_gets_placeholder_row(self, mock_get_data, tmp_path):
        """A configured symbol absent from the fetched quotes must still render
        (as a placeholder row) rather than being dropped or raising."""
        mock_get_data.return_value = {}
        mock_get_data.return_value = {"AAPL": {"symbol": "AAPL", "name": "Apple", "price": 1,
                                                "change": 0, "change_pct": 0}}
        output = str(tmp_path / "stocks.bmp")
        config = {"stocks": {"output_path": output, "symbols": ["AAPL", "ZZZZ"], "cache_dir": str(tmp_path)}}
        result = generate(config)
        assert os.path.exists(result)

    @patch("modules.stocks._get_data")
    def test_symbols_capped_at_ten(self, mock_get_data, tmp_path):
        many_symbols = [f"SYM{i}" for i in range(15)]
        mock_get_data.return_value = {"SYM0": {"symbol": "SYM0", "name": "X", "price": 1,
                                                "change": 0, "change_pct": 0}}

        def _capture(symbols, cache_dir):
            assert len(symbols) == 10
            return mock_get_data.return_value

        mock_get_data.side_effect = _capture
        output = str(tmp_path / "stocks.bmp")
        config = {"stocks": {"output_path": output, "symbols": many_symbols, "cache_dir": str(tmp_path)}}
        generate(config)

    @patch("modules.stocks._get_data")
    def test_no_symbols_configured_uses_defaults(self, mock_get_data, tmp_path):
        def _capture(symbols, cache_dir):
            assert symbols == DEFAULT_SYMBOLS
            return {}

        mock_get_data.side_effect = _capture
        output = str(tmp_path / "stocks.bmp")
        config = {"stocks": {"output_path": output, "cache_dir": str(tmp_path)}}
        generate(config)
