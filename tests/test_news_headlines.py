"""
Unit tests for modules/news_headlines.py.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import news_headlines as nh


SAMPLE_RSS = """<?xml version="1.0"?>
<rss><channel>
<title>BBC News</title>
<item>
  <title>Some &amp; Thing Happens Today in the World</title>
  <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
</item>
<item>
  <title>Another Headline With &lt;b&gt;Markup&lt;/b&gt; In It Here</title>
  <pubDate>Mon, 01 Jan 2024 13:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


class TestStripHtml:
    def test_removes_tags(self):
        assert nh._strip_html("<b>Hello</b> World") == "Hello World"

    def test_decodes_entities(self):
        text = nh._strip_html("Tom &amp; Jerry &quot;fight&quot; &#39;again&#39;")
        assert text == "Tom & Jerry \"fight\" 'again'"

    def test_decodes_curly_quotes(self):
        text = nh._strip_html("‘Hello’ “World”")
        assert text == "'Hello' \"World\""

    def test_strips_whitespace(self):
        assert nh._strip_html("  spaced out  ") == "spaced out"


class TestParseRss:
    def test_parses_valid_rss(self):
        headlines = nh._parse_rss(SAMPLE_RSS)
        assert headlines is not None
        assert len(headlines) == 2
        assert headlines[0]["title"] == "Some & Thing Happens Today in the World"
        assert headlines[0]["pub_date"] == "Mon, 01 Jan 2024 12:00:00 GMT"
        assert "<b>" not in headlines[1]["title"]

    def test_invalid_xml_returns_none(self):
        assert nh._parse_rss("not xml at all <<<") is None

    def test_skips_items_missing_title(self):
        xml = "<rss><channel><item><pubDate>x</pubDate></item></channel></rss>"
        assert nh._parse_rss(xml) == []

    def test_skips_short_titles_without_pubdate(self):
        # Short title (<20 chars) with no pubDate looks like feed-level noise.
        xml = "<rss><channel><item><title>Short</title></item></channel></rss>"
        assert nh._parse_rss(xml) == []

    def test_keeps_long_titles_without_pubdate(self):
        xml = (
            "<rss><channel><item><title>"
            "This is a sufficiently long headline title"
            "</title></item></channel></rss>"
        )
        result = nh._parse_rss(xml)
        assert len(result) == 1


class TestFetchHeadlines:
    @patch("modules.news_headlines.requests.get")
    def test_uses_bbc_when_available(self, mock_get):
        resp = MagicMock()
        resp.text = SAMPLE_RSS
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        headlines, source = nh._fetch_headlines()
        assert source == "BBC News"
        assert len(headlines) == 2
        # Only BBC should be requested since it succeeded.
        assert mock_get.call_count == 1

    @patch("modules.news_headlines.requests.get")
    def test_falls_back_to_npr_on_bbc_failure(self, mock_get):
        def side_effect(url, *args, **kwargs):
            resp = MagicMock()
            if "bbci" in url:
                raise ConnectionError("boom")
            resp.text = SAMPLE_RSS
            resp.raise_for_status = MagicMock()
            return resp

        mock_get.side_effect = side_effect
        headlines, source = nh._fetch_headlines()
        assert source == "NPR News"
        assert len(headlines) == 2
        assert mock_get.call_count == 2

    @patch("modules.news_headlines.requests.get")
    def test_both_sources_fail_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        headlines, source = nh._fetch_headlines()
        assert headlines is None
        assert source is None

    @patch("modules.news_headlines.requests.get")
    def test_empty_parse_falls_through_to_next_source(self, mock_get):
        def side_effect(url, *args, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if "bbci" in url:
                resp.text = "<rss><channel></channel></rss>"  # parses to []
            else:
                resp.text = SAMPLE_RSS
            return resp

        mock_get.side_effect = side_effect
        headlines, source = nh._fetch_headlines()
        assert source == "NPR News"
        assert len(headlines) == 2


class TestCache:
    def test_load_cache_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        assert nh._load_cache() is None

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        headlines = [{"title": "Test", "pub_date": "now"}]
        nh._save_cache(headlines, "BBC News")
        loaded = nh._load_cache()
        assert loaded is not None
        assert loaded["headlines"] == headlines
        assert loaded["source"] == "BBC News"

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        old_time = datetime.now(timezone.utc) - timedelta(minutes=nh.CACHE_MAX_MINUTES + 5)
        data = {
            "cached_at": old_time.isoformat(),
            "source": "BBC News",
            "headlines": [{"title": "Old", "pub_date": ""}],
        }
        os.makedirs(tmp_path, exist_ok=True)
        with open(nh._cache_path(), "w") as f:
            json.dump(data, f)
        assert nh._load_cache() is None

    def test_fresh_cache_returns_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        data = {
            "cached_at": recent_time.isoformat(),
            "source": "BBC News",
            "headlines": [{"title": "Fresh", "pub_date": ""}],
        }
        os.makedirs(tmp_path, exist_ok=True)
        with open(nh._cache_path(), "w") as f:
            json.dump(data, f)
        loaded = nh._load_cache()
        assert loaded is not None
        assert loaded["headlines"][0]["title"] == "Fresh"

    def test_corrupt_cache_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        os.makedirs(tmp_path, exist_ok=True)
        with open(nh._cache_path(), "w") as f:
            f.write("not valid json{{{")
        assert nh._load_cache() is None


class TestWrapText:
    def test_wraps_long_text(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        lines = nh._wrap_text(draw, "one two three four five six seven eight", font, 40)
        assert len(lines) > 1
        # All words preserved across the wrapped lines.
        assert " ".join(lines).split() == "one two three four five six seven eight".split()

    def test_short_text_stays_one_line(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        lines = nh._wrap_text(draw, "short", font, 1000)
        assert lines == ["short"]


class TestGenerate:
    def test_uses_cache_without_fetching(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        nh._save_cache([{"title": "Cached Headline", "pub_date": "now"}], "BBC News")
        output_path = str(tmp_path / "out.bmp")
        with patch("modules.news_headlines._fetch_headlines") as mock_fetch:
            result = nh.generate({"news_headlines": {"output_path": output_path}})
        mock_fetch.assert_not_called()
        assert result == output_path
        assert os.path.exists(output_path)

    def test_fetch_success_saves_cache_and_renders(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        headlines = [{"title": "Fresh Headline", "pub_date": "now"}]
        with patch("modules.news_headlines._fetch_headlines", return_value=(headlines, "NPR News")):
            result = nh.generate({"news_headlines": {"output_path": output_path}})
        assert result == output_path
        cached = nh._load_cache()
        assert cached["source"] == "NPR News"

    def test_fetch_failure_falls_back_to_offline_headlines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nh, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        with patch("modules.news_headlines._fetch_headlines", return_value=(None, None)):
            result = nh.generate({"news_headlines": {"output_path": output_path}})
        assert result == output_path
        assert os.path.exists(output_path)
        # Offline fallback shouldn't be persisted as if it were a real fetch.
        assert nh._load_cache() is None


class TestRender:
    def test_render_creates_output_file(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        headlines = [{"title": "A Headline About Something", "pub_date": "Mon, 01 Jan 2024 12:00:00 GMT"}]
        result = nh._render(headlines, "BBC News", "2024-01-01T00:00:00+00:00", output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_render_with_no_headlines_does_not_crash(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        nh._render([], "BBC News", "2024-01-01T00:00:00+00:00", output_path)
        assert os.path.exists(output_path)
