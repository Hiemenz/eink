"""
Unit tests for display.py's driver dispatch: selecting between the 7-color
ACeP panel (epd7in3f) and the black-and-white panel (epd7in5_V2), plus the
locking and periodic-deep-clear behavior around them.

The real waveshare_epd EPD classes talk to GPIO/SPI hardware in init(),
Clear(), display(), and sleep() -- those are always mocked here so tests
never touch actual hardware, even though this runs on the target Pi.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import display


@pytest.fixture(autouse=True)
def isolated_runtime_files(tmp_path, monkeypatch):
    """Redirect the lock file and refresh counter to tmp_path so tests never
    touch the real /tmp state a live display service might depend on."""
    monkeypatch.setattr(display, "DISPLAY_LOCK_FILE", str(tmp_path / "eink_display.lock"))
    monkeypatch.setattr(display, "_REFRESH_COUNTER_FILE", str(tmp_path / "eink_refresh_count.txt"))


@pytest.fixture
def sample_image(tmp_path):
    path = tmp_path / "frame.bmp"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(path)
    return str(path)


def _mock_epd():
    epd = MagicMock()
    epd.getbuffer.return_value = b"\x00"
    return epd


class TestDriverSelection:
    def test_epd7in3f_model_uses_color_driver(self, sample_image):
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in3f.EPD", return_value=mock_epd) as EPD3f, \
             patch("waveshare_epd.epd7in5_V2.EPD") as EPD_v2:
            display.display_color_image(sample_image, model="epd7in3f")

        EPD3f.assert_called_once()
        EPD_v2.assert_not_called()
        mock_epd.init.assert_called_once()
        mock_epd.display.assert_called_once()
        mock_epd.sleep.assert_called_once()

    def test_epd7in5_v2_model_uses_bw_driver(self, sample_image):
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in5_V2.EPD", return_value=mock_epd) as EPD_v2, \
             patch("waveshare_epd.epd7in3f.EPD") as EPD3f:
            display.display_color_image(sample_image, model="epd7in5_V2")

        EPD_v2.assert_called_once()
        EPD3f.assert_not_called()
        mock_epd.init.assert_called_once()
        mock_epd.display.assert_called_once()
        mock_epd.sleep.assert_called_once()

    def test_unknown_model_falls_back_to_bw_driver(self, sample_image):
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in5_V2.EPD", return_value=mock_epd) as EPD_v2, \
             patch("waveshare_epd.epd7in3f.EPD") as EPD3f:
            display.display_color_image(sample_image, model="nonexistent_model")

        EPD_v2.assert_called_once()
        EPD3f.assert_not_called()


class TestFullClearInterval:
    def test_deep_clear_only_applies_to_color_driver(self, sample_image):
        """full_clear_interval must be a no-op for the B/W V2 driver even if set."""
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in5_V2.EPD", return_value=mock_epd), \
             patch("waveshare_epd.epd7in3f.EPD"):
            display.display_color_image(sample_image, model="epd7in5_V2", full_clear_interval=1)

        # Only the baseline epd.Clear() call, no extra deep-clear pass.
        assert mock_epd.Clear.call_count == 1

    def test_deep_clear_triggers_on_color_driver_at_interval(self, sample_image):
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in3f.EPD", return_value=mock_epd), \
             patch("waveshare_epd.epd7in5_V2.EPD"):
            display.display_color_image(sample_image, model="epd7in3f", full_clear_interval=3)
            display.display_color_image(sample_image, model="epd7in3f", full_clear_interval=3)
            display.display_color_image(sample_image, model="epd7in3f", full_clear_interval=3)

        # Baseline Clear() every call (3) + 2 extra deep-clear passes on the 3rd call.
        assert mock_epd.Clear.call_count == 3 + 2

    def test_deep_clear_disabled_when_interval_zero(self, sample_image):
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in3f.EPD", return_value=mock_epd), \
             patch("waveshare_epd.epd7in5_V2.EPD"):
            display.display_color_image(sample_image, model="epd7in3f", full_clear_interval=0)

        assert mock_epd.Clear.call_count == 1


class TestLocking:
    def test_skips_display_when_lock_already_held(self, sample_image, tmp_path):
        import fcntl
        lock_path = str(tmp_path / "eink_display.lock")
        display.DISPLAY_LOCK_FILE = lock_path
        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch("waveshare_epd.epd7in3f.EPD") as EPD3f, \
                 patch("waveshare_epd.epd7in5_V2.EPD") as EPD_v2:
                display.display_color_image(sample_image, model="epd7in3f")

            EPD3f.assert_not_called()
            EPD_v2.assert_not_called()
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()

    def test_lock_released_after_display(self, sample_image):
        import fcntl
        mock_epd = _mock_epd()
        with patch("waveshare_epd.epd7in3f.EPD", return_value=mock_epd), \
             patch("waveshare_epd.epd7in5_V2.EPD"):
            display.display_color_image(sample_image, model="epd7in3f")

        # Lock must be released -- acquiring it again non-blocking must succeed.
        fd = open(display.DISPLAY_LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


class TestErrorHandling:
    def test_init_failure_still_sleeps_and_does_not_raise(self, sample_image):
        mock_epd = _mock_epd()
        mock_epd.init.side_effect = RuntimeError("SPI bus not available")
        with patch("waveshare_epd.epd7in3f.EPD", return_value=mock_epd), \
             patch("waveshare_epd.epd7in5_V2.EPD"):
            display.display_color_image(sample_image, model="epd7in3f")  # must not raise

        mock_epd.sleep.assert_called_once()
        mock_epd.display.assert_not_called()

    def test_display_failure_still_sleeps_and_does_not_raise(self, sample_image):
        mock_epd = _mock_epd()
        mock_epd.display.side_effect = RuntimeError("bus timeout")
        with patch("waveshare_epd.epd7in3f.EPD", return_value=mock_epd), \
             patch("waveshare_epd.epd7in5_V2.EPD"):
            display.display_color_image(sample_image, model="epd7in3f")  # must not raise

        mock_epd.sleep.assert_called_once()
