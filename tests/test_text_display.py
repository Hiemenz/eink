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
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.text_display import wrap_text, get_random_question, generate_content, generate


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
