"""
Unit tests for modules/text_display.py

Covers text wrapping, random-question selection from CSV, the generate()
config-driven skip/passthrough behavior, and generate_content()'s HTTP
error handling (mocked, no real network calls).
"""

import sys
import os
import csv
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.text_display import (
    wrap_text, get_random_question, generate_content, generate, generate_image, _load_font,
)


class TestWrapText:
    def _draw(self):
        img = Image.new("RGB", (800, 480))
        return ImageDraw.Draw(img)

    def test_short_text_not_wrapped(self):
        draw = self._draw()
        font = draw.getfont()
        result = wrap_text("hi there", font, draw, 800)
        assert result == "hi there"

    def test_long_text_wraps_into_multiple_lines(self):
        draw = self._draw()
        font = draw.getfont()
        text = " ".join(["word"] * 50)
        result = wrap_text(text, font, draw, 100)
        assert "\n" in result

    def test_empty_text_returns_empty_string(self):
        draw = self._draw()
        font = draw.getfont()
        # words[0] on an empty split list would raise; function guards with
        # `words[0] if words else ""`.
        result = wrap_text("", font, draw, 800)
        assert result == ""

    def test_single_overlong_word_is_not_split(self):
        """A single word wider than max_width has no break point — it ships as-is."""
        draw = self._draw()
        font = draw.getfont()
        word = "a" * 200
        result = wrap_text(word, font, draw, 10)
        assert result == word

    def test_whitespace_only_text_returns_empty_string(self):
        draw = self._draw()
        font = draw.getfont()
        result = wrap_text("   ", font, draw, 800)
        assert result == ""


class TestGetRandomQuestion:
    def test_returns_a_question_from_csv(self, tmp_path):
        csv_path = tmp_path / "questions.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["question"])
            writer.writeheader()
            writer.writerow({"question": "  What is your favorite color?  "})
            writer.writerow({"question": "What is your quest?"})

        result = get_random_question(str(csv_path))
        assert result in ("What is your favorite color?", "What is your quest?")

    def test_empty_csv_raises_value_error(self, tmp_path):
        csv_path = tmp_path / "empty.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["question"])
            writer.writeheader()

        with pytest.raises(ValueError):
            get_random_question(str(csv_path))


class TestLoadFont:
    @patch("modules.text_display.os.path.exists", return_value=False)
    def test_no_candidate_paths_exist_falls_back_to_default(self, mock_exists):
        # No candidate is "found" on disk, so the loop never calls truetype()
        # and falls through to ImageFont.load_default().
        font = _load_font(20)
        assert font is not None

    def test_truetype_failure_on_real_paths_falls_back_to_default(self):
        # Fail only for our candidate-path strings; load_default()'s own
        # internal truetype() call (a BytesIO, not a path) passes through
        # untouched, so this isolates just the candidate-loading failure.
        real_truetype = ImageFont.truetype

        def flaky_truetype(font_arg, *args, **kwargs):
            if isinstance(font_arg, str):
                raise OSError("bad font file")
            return real_truetype(font_arg, *args, **kwargs)

        with patch("modules.text_display.os.path.exists", return_value=True), \
             patch("modules.text_display.ImageFont.truetype", side_effect=flaky_truetype):
            font = _load_font(20)
        assert font is not None


class TestGenerateImage:
    def test_saves_file_at_given_path(self, tmp_path):
        path = str(tmp_path / "out.bmp")
        result = generate_image("Hello", 400, 200, path)
        assert result == path
        assert os.path.exists(path)

    def test_creates_parent_directories(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "out.bmp")
        generate_image("Hi", 400, 200, path)
        assert os.path.exists(path)

    def test_long_text_shrinks_font_to_fit_small_canvas(self, tmp_path):
        path = str(tmp_path / "small.bmp")
        long_text = " ".join(["word"] * 40)
        # A tiny canvas forces the font-shrink loop to run past its default 60pt start.
        result = generate_image(long_text, 100, 80, path)
        assert result == path
        img = Image.open(path)
        assert img.size == (100, 80)

    def test_output_is_rgb_image_of_requested_size(self, tmp_path):
        path = str(tmp_path / "out.bmp")
        generate_image("Sized", 300, 150, path)
        img = Image.open(path)
        assert img.size == (300, 150)
        assert img.mode == "RGB"


class TestGenerateContent:
    def test_no_api_key_raises_value_error(self):
        with pytest.raises(ValueError):
            generate_content("some prompt", api_key=None)

    @patch("modules.text_display.requests.post")
    def test_success_returns_json(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        mock_post.return_value = resp

        result = generate_content("prompt", api_key="fake-key")
        assert result["candidates"][0]["content"]["parts"][0]["text"] == "hi"

    @patch("modules.text_display.requests.post")
    def test_http_error_raises_exception(self, mock_post):
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "server error"
        mock_post.return_value = resp

        with pytest.raises(Exception):
            generate_content("prompt", api_key="fake-key")


class TestGenerate:
    def test_no_message_configured_returns_none(self, tmp_path):
        config = {"text": {}, "width": 800, "height": 480}
        assert generate(config) is None

    def test_blank_message_returns_none(self, tmp_path):
        config = {"text": {"message": "   "}, "width": 800, "height": 480}
        assert generate(config) is None

    def test_message_set_generates_image(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        config = {
            "text": {"message": "Hello world", "output_path": output_path},
            "width": 200,
            "height": 100,
        }
        result = generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_missing_text_section_returns_none(self):
        assert generate({"width": 800, "height": 480}) is None

    def test_non_dict_text_section_returns_none(self):
        # config.get('text') guarded with isinstance() check — a stray scalar
        # (e.g. from a bad !set) must not raise.
        config = {"text": "oops a string", "width": 800, "height": 480}
        assert generate(config) is None

    def test_non_string_message_returns_none(self):
        config = {"text": {"message": 12345}, "width": 800, "height": 480}
        assert generate(config) is None

    def test_default_output_path_used_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {"text": {"message": "Hi"}, "width": 200, "height": 100}
        result = generate(config)
        assert result == "images/text_display.bmp"
        assert os.path.exists(result)
