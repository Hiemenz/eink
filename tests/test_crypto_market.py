"""
Unit tests for modules/crypto_market.py — pure display-formatting helpers
(price/percent formatting, trend/signal labels). Data fetching and analysis
live in crypto.data / crypto.analysis and are out of scope for this module.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import crypto_market as cm


class TestFormatPrice:
    def test_large_price_no_decimals(self):
        assert cm._format_price(65000) == "$65,000"

    def test_mid_price_two_decimals(self):
        assert cm._format_price(3.456) == "$3.46"

    def test_small_price_three_decimals(self):
        assert cm._format_price(0.05) == "$0.050"

    def test_tiny_price_five_decimals(self):
        assert cm._format_price(0.00012345) == "$0.00012"

    def test_boundary_10000(self):
        assert cm._format_price(10000) == "$10,000"

    def test_boundary_1(self):
        assert cm._format_price(1) == "$1.00"

    def test_boundary_0_01(self):
        assert cm._format_price(0.01) == "$0.010"


class TestTrendSymbol:
    def test_bullish(self):
        assert cm._trend_symbol(True) == "▲"

    def test_bearish(self):
        assert cm._trend_symbol(False) == "▼"

    def test_none_returns_dash(self):
        assert cm._trend_symbol(None) == "—"


class TestPctStr:
    def test_small_pct_one_decimal(self):
        assert cm._pct_str(3.456) == "+3.5%"

    def test_negative_pct(self):
        assert cm._pct_str(-2.1) == "-2.1%"

    def test_large_pct_no_decimals(self):
        assert cm._pct_str(15.7) == "+16%"

    def test_boundary_10(self):
        assert cm._pct_str(10.0) == "+10%"

    def test_boundary_just_under_10(self):
        assert cm._pct_str(9.99) == "+10.0%"


class TestMaLabel:
    def test_golden(self):
        assert cm._ma_label("GOLDEN") == "GOLDEN"

    def test_death(self):
        assert cm._ma_label("DEATH") == "DEATH"

    def test_unknown_returns_na(self):
        assert cm._ma_label("SOMETHING_ELSE") == "N/A"

    def test_none_returns_na(self):
        assert cm._ma_label(None) == "N/A"


class TestSignalLabel:
    def test_buy(self):
        assert cm._signal_label("BUY") == ">> BUY"

    def test_sell(self):
        assert cm._signal_label("SELL") == "<< SELL"

    def test_hold(self):
        assert cm._signal_label("HOLD") == "-- HOLD"

    def test_unknown_defaults_to_hold(self):
        assert cm._signal_label("WHATEVER") == "-- HOLD"
