import os
import time

import pytest

import main
from modules import weather


def _write_hash(path, value, age_seconds=0):
    hash_path = str(path) + ".last_hash"
    with open(hash_path, "w") as f:
        f.write(value)
    when = time.time() - age_seconds
    os.utime(hash_path, (when, when))


def test_same_hash_is_unchanged(tmp_path):
    img = tmp_path / "a.bmp"
    _write_hash(img, "abc")
    assert main._is_unchanged(str(img), "abc") is True


def test_different_hash_is_changed(tmp_path):
    img = tmp_path / "a.bmp"
    _write_hash(img, "abc")
    assert main._is_unchanged(str(img), "xyz") is False


def test_missing_hash_file_is_changed(tmp_path):
    assert main._is_unchanged(str(tmp_path / "a.bmp"), "abc") is False


def test_recent_push_with_max_age_is_unchanged(tmp_path):
    img = tmp_path / "a.bmp"
    _write_hash(img, "abc", age_seconds=600)
    assert main._is_unchanged(str(img), "abc", max_age=3600) is True


def test_stale_push_with_max_age_forces_refresh(tmp_path):
    img = tmp_path / "a.bmp"
    _write_hash(img, "abc", age_seconds=7200)
    assert main._is_unchanged(str(img), "abc", max_age=3600) is False


def test_max_age_zero_never_forces_refresh(tmp_path):
    img = tmp_path / "a.bmp"
    _write_hash(img, "abc", age_seconds=10**6)
    assert main._is_unchanged(str(img), "abc", max_age=0) is True


def _view(ts=1000.0, quiet=True):
    return {"ts": ts, "quiet": quiet, "national": False}


@pytest.mark.parametrize("shown, new, expected", [
    (_view(), _view(ts=1720.0), None),
    (_view(), _view(ts=4535.0), "hourly refresh"),
    (_view(), _view(ts=1720.0, quiet=False), "active weather"),
    (_view(quiet=False), _view(ts=1720.0, quiet=False), "active weather"),
    (_view(quiet=False), _view(ts=1720.0), "weather cleared"),
    (_view(), {**_view(ts=3880.0), "national": True}, "US view's turn"),
    ({**_view(), "national": True}, _view(ts=1720.0), "back to the local view"),
])
def test_view_change_reason(shown, new, expected):
    assert weather.view_change_reason(shown, new, max_age=3600) == expected


def test_quiet_run_is_skipped_and_active_run_is_pushed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_VIEW_FILE", str(tmp_path / "view.json"))
    path = str(tmp_path / "a.bmp")
    main._save_shown_view(path, _view())
    assert main._skip_reason(weather, {"_view_state": _view(ts=1720.0)}, path, "new")
    assert main._skip_reason(weather, {"_view_state": _view(ts=1720.0, quiet=False)}, path, "new") is None


def test_push_without_a_view_forgets_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_VIEW_FILE", str(tmp_path / "view.json"))
    path = str(tmp_path / "a.bmp")
    main._save_shown_view(path, _view())
    main._save_shown_view(str(tmp_path / "other.bmp"), None)
    assert main._skip_reason(weather, {"_view_state": _view()}, path, "new") is None
