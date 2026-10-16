import os
import time

import main


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
