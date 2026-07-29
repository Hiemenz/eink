"""
Unit tests for modules/movie_slideshow.py: state persistence, frame
listing/discovery, fit/crop image layout math, and the playlist
advancement logic in generate().
"""

import sys
import os
import json

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.movie_slideshow import (
    _load_state,
    _save_state,
    _list_frames,
    _prepare_frames,
    _fit_image,
    _crop_image,
    generate,
)


class TestStatePersistence:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        state = _load_state(str(tmp_path))
        assert state == {"movie_index": 0, "frame_index": 0}

    def test_save_then_load_round_trip(self, tmp_path):
        _save_state(str(tmp_path), {"movie_index": 2, "frame_index": 10})
        loaded = _load_state(str(tmp_path))
        assert loaded == {"movie_index": 2, "frame_index": 10}

    def test_corrupt_state_file_returns_defaults(self, tmp_path):
        state_path = tmp_path / "_state.json"
        state_path.write_text("{not valid")
        state = _load_state(str(tmp_path))
        assert state == {"movie_index": 0, "frame_index": 0}


class TestListFrames:
    def test_lists_supported_extensions_only_sorted(self, tmp_path):
        (tmp_path / "b.png").write_bytes(b"x")
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "c.txt").write_bytes(b"x")
        frames = _list_frames(str(tmp_path))
        names = [os.path.basename(f) for f in frames]
        assert names == ["a.jpg", "b.png"]

    def test_missing_directory_returns_empty_list(self, tmp_path):
        assert _list_frames(str(tmp_path / "missing")) == []

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert _list_frames(str(tmp_path)) == []


class TestPrepareFrames:
    def test_returns_movie_dir_when_only_images_present(self, tmp_path):
        movie_dir = tmp_path / "movie1"
        movie_dir.mkdir()
        (movie_dir / "frame1.jpg").write_bytes(b"x")
        result = _prepare_frames(str(movie_dir), extract_fps=1)
        assert result == str(movie_dir)

    def test_returns_movie_dir_when_missing(self, tmp_path):
        result = _prepare_frames(str(tmp_path / "nonexistent"), extract_fps=1)
        assert result == str(tmp_path / "nonexistent")

    def test_returns_movie_dir_when_empty(self, tmp_path):
        movie_dir = tmp_path / "empty_movie"
        movie_dir.mkdir()
        result = _prepare_frames(str(movie_dir), extract_fps=1)
        assert result == str(movie_dir)


class TestFitAndCropImage:
    def test_fit_image_preserves_canvas_size(self):
        img = Image.new("RGB", (400, 100), "red")
        result = _fit_image(img, 800, 480)
        assert result.size == (800, 480)

    def test_fit_image_letterboxes_wide_image(self):
        # Very wide image should have black bars top/bottom, image centered.
        img = Image.new("RGB", (1000, 100), "red")
        result = _fit_image(img, 800, 480)
        # Corner pixel should be the black letterbox bar, not red.
        assert result.getpixel((0, 0)) == (0, 0, 0)

    def test_crop_image_preserves_canvas_size(self):
        img = Image.new("RGB", (400, 100), "blue")
        result = _crop_image(img, 800, 480)
        assert result.size == (800, 480)

    def test_crop_image_fills_entire_canvas(self):
        img = Image.new("RGB", (2000, 100), "blue")
        result = _crop_image(img, 800, 480)
        # Crop-fill should have no black bars — every corner is the source color.
        assert result.getpixel((0, 0)) == (0, 0, 255)
        assert result.getpixel((799, 479)) == (0, 0, 255)


class TestGeneratePlaylistAdvancement:
    def _make_movie(self, movies_root, name, n_frames):
        movie_dir = os.path.join(movies_root, name)
        os.makedirs(movie_dir, exist_ok=True)
        for i in range(n_frames):
            Image.new("RGB", (10, 10), "white").save(
                os.path.join(movie_dir, f"frame_{i:03d}.png")
            )
        return movie_dir

    def _config(self, movies_root, playlist=None, active_movie="", output_path=None):
        return {
            "movie_slideshow": {
                "movies_dir": movies_root,
                "active_movie": active_movie,
                "playlist": playlist or [],
                "output_path": output_path or os.path.join(movies_root, "out.bmp"),
                "show_frame_counter": False,
            },
            "width": 80,
            "height": 48,
        }

    def test_no_movies_configured_shows_placeholder(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        config = self._config(movies_root)
        output = generate(config)
        assert os.path.exists(output)

    def test_missing_frames_shows_placeholder(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        os.makedirs(os.path.join(movies_root, "empty_movie"), exist_ok=True)
        config = self._config(movies_root, active_movie="empty_movie")
        output = generate(config)
        assert os.path.exists(output)

    def test_advances_frame_index_within_movie(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        self._make_movie(movies_root, "movie1", 5)
        config = self._config(movies_root, active_movie="movie1")
        generate(config)
        state = _load_state(movies_root)
        assert state["frame_index"] == 1
        assert state["movie_index"] == 0

    def test_wraps_to_next_movie_in_playlist_at_end(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        self._make_movie(movies_root, "movie1", 1)
        self._make_movie(movies_root, "movie2", 3)
        _save_state(movies_root, {"movie_index": 0, "frame_index": 0})
        config = self._config(movies_root, playlist=["movie1", "movie2"])
        generate(config)
        state = _load_state(movies_root)
        assert state["movie_index"] == 1
        assert state["frame_index"] == 0

    def test_wraps_playlist_around_to_start(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        self._make_movie(movies_root, "movie1", 2)
        self._make_movie(movies_root, "movie2", 1)
        _save_state(movies_root, {"movie_index": 1, "frame_index": 0})
        config = self._config(movies_root, playlist=["movie1", "movie2"])
        generate(config)
        state = _load_state(movies_root)
        assert state["movie_index"] == 0
        assert state["frame_index"] == 0

    def test_active_movie_overrides_playlist(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        self._make_movie(movies_root, "solo", 2)
        self._make_movie(movies_root, "ignored", 2)
        config = self._config(movies_root, playlist=["ignored"], active_movie="solo")
        output = generate(config)
        assert os.path.exists(output)
        img = Image.open(output)
        assert img.size == (80, 48)

    def test_frame_step_skips_frames(self, tmp_path):
        movies_root = str(tmp_path / "movies")
        self._make_movie(movies_root, "movie1", 10)
        config = self._config(movies_root, active_movie="movie1")
        config["movie_slideshow"]["frame_step"] = 3
        generate(config)
        state = _load_state(movies_root)
        assert state["frame_index"] == 3
