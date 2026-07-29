"""
Unit tests for modules/claude_news.py — CHANGELOG.md markdown parsing, cache
TTL, npm fallback, and text truncation (network calls mocked).
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import claude_news as cn


class TestParseChangelog:
    def test_parses_versions_and_bullets(self):
        text = (
            "## 2.1.77\n"
            "- Added feature A\n"
            "- Fixed bug B\n"
            "\n"
            "## 2.1.76\n"
            "- Improved thing C\n"
        )
        releases = cn._parse_changelog(text)
        assert len(releases) == 2
        assert releases[0]["version"] == "v2.1.77"
        assert releases[0]["items"] == ["Added feature A", "Fixed bug B"]
        assert releases[1]["version"] == "v2.1.76"

    def test_bracketed_version_heading(self):
        text = "## [3.0.0]\n- Big rewrite\n"
        releases = cn._parse_changelog(text)
        assert releases[0]["version"] == "v3.0.0"

    def test_strips_markdown_formatting_from_bullets(self):
        text = "## 1.0.0\n- Uses `code` and **bold** and _italic_\n"
        releases = cn._parse_changelog(text)
        assert releases[0]["items"] == ["Uses code and bold and italic"]

    def test_limits_to_max_releases(self):
        lines = []
        for i in range(10):
            lines.append(f"## {i}.0.0")
            lines.append("- item")
        text = "\n".join(lines)
        releases = cn._parse_changelog(text)
        assert len(releases) == cn.MAX_RELEASES

    def test_limits_items_per_release(self):
        text = "## 1.0.0\n" + "\n".join(f"- item {i}" for i in range(10))
        releases = cn._parse_changelog(text)
        assert len(releases[0]["items"]) == cn.MAX_ITEMS

    def test_no_version_headings_returns_empty(self):
        assert cn._parse_changelog("just some text\n- a bullet\n") == []

    def test_empty_bullet_after_stripping_skipped(self):
        text = "## 1.0.0\n- ***\n- Real item\n"
        releases = cn._parse_changelog(text)
        assert releases[0]["items"] == ["Real item"]


class TestCache:
    def test_load_cache_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cn, "CACHE_DIR", str(tmp_path))
        assert cn._load_cache() is None

    def test_save_and_load_within_ttl(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cn, "CACHE_DIR", str(tmp_path))
        cn._save_cache([{"version": "v1.0.0", "items": ["x"]}])
        loaded = cn._load_cache()
        assert loaded == [{"version": "v1.0.0", "items": ["x"]}]

    def test_load_cache_expired_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cn, "CACHE_DIR", str(tmp_path))
        path = os.path.join(str(tmp_path), "claude_news_cache.json")
        with open(path, "w") as f:
            json.dump({"fetched_at": 0, "releases": [{"version": "v1.0.0", "items": ["x"]}]}, f)
        assert cn._load_cache() is None

    def test_load_cache_corrupt_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cn, "CACHE_DIR", str(tmp_path))
        path = os.path.join(str(tmp_path), "claude_news_cache.json")
        with open(path, "w") as f:
            f.write("{not json")
        assert cn._load_cache() is None


class TestFetchChangelog:
    def test_success_filters_empty_releases(self):
        text = "## 1.0.0\n- Something\n\n## 0.9.0\n"  # second release has no items
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = text
        with patch.object(cn.requests, "get", return_value=mock_resp):
            releases = cn._fetch_changelog()
        assert len(releases) == 1
        assert releases[0]["version"] == "v1.0.0"

    def test_network_failure_returns_none(self):
        with patch.object(cn.requests, "get", side_effect=Exception("timeout")):
            assert cn._fetch_changelog() is None

    def test_all_releases_empty_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "## 1.0.0\n"
        with patch.object(cn.requests, "get", return_value=mock_resp):
            assert cn._fetch_changelog() is None


class TestFetchNpmFallback:
    def test_success_sorts_by_time_descending(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "time": {
                "1.0.0": "2026-01-01T00:00:00.000Z",
                "1.1.0": "2026-02-01T00:00:00.000Z",
                "modified": "2026-02-01T00:00:00.000Z",  # non-version key, should be skipped
            }
        }
        with patch.object(cn.requests, "get", return_value=mock_resp):
            releases = cn._fetch_npm_fallback()
        assert releases[0]["version"] == "v1.1.0"
        assert releases[1]["version"] == "v1.0.0"

    def test_network_failure_returns_none(self):
        with patch.object(cn.requests, "get", side_effect=Exception("timeout")):
            assert cn._fetch_npm_fallback() is None


class TestGetReleases:
    def test_uses_cache_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cn, "CACHE_DIR", str(tmp_path))
        cn._save_cache([{"version": "vCached", "items": ["x"]}])
        with patch.object(cn, "_fetch_changelog") as mock_fetch:
            releases = cn._get_releases()
        mock_fetch.assert_not_called()
        assert releases[0]["version"] == "vCached"

    def test_falls_back_to_npm_then_static_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cn, "CACHE_DIR", str(tmp_path))
        with patch.object(cn, "_fetch_changelog", return_value=None), \
             patch.object(cn, "_fetch_npm_fallback", return_value=None):
            releases = cn._get_releases()
        assert releases[0]["version"] == "Claude Code"


class TestTruncate:
    def test_short_text_unchanged(self):
        img = Image.new("RGB", (100, 50))
        draw = ImageDraw.Draw(img)
        font = cn._font(12)
        assert cn._truncate("short", font, draw, 1000) == "short"

    def test_long_text_truncated(self):
        img = Image.new("RGB", (100, 50))
        draw = ImageDraw.Draw(img)
        font = cn._font(12)
        result = cn._truncate("a" * 200, font, draw, 50)
        assert result.endswith("…")
        assert len(result) < 200
