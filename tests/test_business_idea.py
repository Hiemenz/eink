"""
Unit tests for modules/business_idea.py — markdown idea parsing, shown-state
rotation, and image rendering. All file I/O goes through tmp_path so nothing
touches the real sibling business-ideas repo or this project's data/ dir.
"""

import os
import sys
import json

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import business_idea as bi


class TestClean:
    def test_strips_bold_markers(self):
        assert bi._clean("**Bold Title**") == "Bold Title"

    def test_strips_italic_markers(self):
        assert bi._clean("*Italic Title*") == "Italic Title"

    def test_strips_leading_dashes_and_hashes(self):
        assert bi._clean("### Some Title") == "Some Title"
        assert bi._clean("- Some Title") == "Some Title"

    def test_strips_surrounding_brackets(self):
        assert bi._clean("[Bracketed]") == "Bracketed"

    def test_strips_surrounding_whitespace(self):
        assert bi._clean("   spaced text   ") == "spaced text"


class TestNumberedSections:
    def test_splits_multiple_numbered_headers(self):
        text = (
            "## 1. First Idea\n"
            "Body of first idea.\n"
            "### 2. Second Idea\n"
            "Body of second idea.\n"
        )
        sections = bi._numbered_sections(text)
        assert [t for t, _ in sections] == ["First Idea", "Second Idea"]
        assert "Body of first idea." in sections[0][1]
        assert "Body of second idea." in sections[1][1]

    def test_no_numbered_headers_returns_empty(self):
        assert bi._numbered_sections("# Just a plain heading\nSome text.") == []

    def test_ignores_text_before_first_header(self):
        text = "Preamble line\n## 1. Idea\nBody\n"
        sections = bi._numbered_sections(text)
        assert len(sections) == 1
        assert "Preamble line" not in sections[0][1]


class TestWholeFileSection:
    def test_uses_first_hash_heading_as_title(self):
        text = "Intro\n# The Title\nBody line one.\nBody line two.\n"
        sections = bi._whole_file_section(text)
        assert len(sections) == 1
        title, body = sections[0]
        assert title == "The Title"
        assert "Body line one." in body

    def test_no_heading_returns_empty(self):
        assert bi._whole_file_section("Just plain text, no heading.") == []


class TestSectionsFromFile:
    def test_prefers_numbered_sections_when_present(self):
        text = "## 1. Numbered\nBody\n# Whole File Title\nOther body\n"
        sections = bi._sections_from_file(text)
        assert sections[0][0] == "Numbered"

    def test_falls_back_to_whole_file(self):
        text = "# Only A Whole-File Title\nBody text.\n"
        sections = bi._sections_from_file(text)
        assert sections[0][0] == "Only A Whole-File Title"


class TestExtractDetail:
    def test_picks_first_substantial_paragraph(self):
        body = "Short.\n\nThis is a much longer paragraph that clears the forty character bar.\n"
        detail = bi._extract_detail(body)
        assert detail.startswith("This is a much longer paragraph")

    def test_falls_back_to_first_paragraph_when_none_long_enough(self):
        body = "Tiny.\n\nAlso small.\n"
        detail = bi._extract_detail(body)
        assert detail == "Tiny."

    def test_empty_body_returns_empty_string(self):
        assert bi._extract_detail("") == ""

    def test_skips_headers_and_metadata_lines(self):
        body = "# Heading\n**Date:** 2024-01-01\nThis is the real detail paragraph content here now.\n"
        detail = bi._extract_detail(body)
        assert detail.startswith("This is the real detail")

    def test_truncates_long_detail_with_ellipsis(self):
        sentence = "This sentence is repeated to build a very long paragraph. "
        body = sentence * 20
        detail = bi._extract_detail(body)
        assert len(detail) <= bi.MAX_DETAIL_CHARS + 1
        assert detail.endswith("…")


class TestDiscoverIdeas:
    def _write_md(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_discovers_ideas_across_configured_dirs(self, tmp_path):
        source = str(tmp_path)
        self._write_md(
            os.path.join(source, "main-income-ideas", "2024-01-01-notes.md"),
            "## 1. Idea One\nDetail body for idea one that is long enough to matter here.\n",
        )
        ideas = bi._discover_ideas(source, ["main-income-ideas"])
        assert len(ideas) == 1
        assert ideas[0]["title"] == "Idea One"
        assert ideas[0]["category"] == "Main Income Idea"
        assert ideas[0]["date"] == "2024-01-01"

    def test_skips_nonexistent_dirs(self, tmp_path):
        ideas = bi._discover_ideas(str(tmp_path), ["does-not-exist"])
        assert ideas == []

    def test_skips_sections_with_empty_title_or_body(self, tmp_path):
        source = str(tmp_path)
        self._write_md(os.path.join(source, "proof-of-concepts", "empty.md"), "## 1. \n\n")
        ideas = bi._discover_ideas(source, ["proof-of-concepts"])
        assert ideas == []

    def test_no_date_prefix_leaves_date_none(self, tmp_path):
        source = str(tmp_path)
        self._write_md(
            os.path.join(source, "discovered-problems", "notes.md"),
            "## 1. Undated Idea\nSome reasonably long body content goes here for parsing.\n",
        )
        ideas = bi._discover_ideas(source, ["discovered-problems"])
        assert ideas[0]["date"] is None

    def test_ids_are_stable_for_same_content(self, tmp_path):
        source = str(tmp_path)
        self._write_md(
            os.path.join(source, "main-income-ideas", "a.md"),
            "## 1. Stable Idea\nBody content that is long enough to be substantial here.\n",
        )
        first = bi._discover_ideas(source, ["main-income-ideas"])
        second = bi._discover_ideas(source, ["main-income-ideas"])
        assert first[0]["id"] == second[0]["id"]

    def test_unreadable_file_is_skipped_not_raised(self, tmp_path, monkeypatch):
        source = str(tmp_path)
        bad_path = os.path.join(source, "main-income-ideas", "bad.md")
        self._write_md(bad_path, "## 1. X\nY\n")

        real_open = open

        def failing_open(path, *a, **kw):
            if path == bad_path:
                raise OSError("permission denied")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(bi, "open", failing_open, raising=False)
        ideas = bi._discover_ideas(source, ["main-income-ideas"])
        assert ideas == []


class TestShownCache:
    def test_load_missing_cache_returns_empty_set(self, tmp_path):
        cache = str(tmp_path / "missing.json")
        assert bi._load_shown(cache) == set()

    def test_save_then_load_roundtrip(self, tmp_path):
        cache = str(tmp_path / "shown.json")
        bi._save_shown(cache, {"id1", "id2"})
        assert bi._load_shown(cache) == {"id1", "id2"}

    def test_corrupt_cache_returns_empty_set(self, tmp_path):
        cache = tmp_path / "corrupt.json"
        cache.write_text("{not json")
        assert bi._load_shown(str(cache)) == set()

    def test_save_creates_parent_dirs(self, tmp_path):
        cache = str(tmp_path / "nested" / "shown.json")
        bi._save_shown(cache, {"id1"})
        assert os.path.exists(cache)


class TestPickIdea:
    def _idea(self, id_, mtime, order=0):
        return {"id": id_, "title": f"Idea {id_}", "body": "body", "mtime": mtime, "order": order}

    def test_empty_ideas_returns_none(self, tmp_path):
        assert bi._pick_idea([], str(tmp_path / "shown.json")) is None

    def test_picks_newest_unseen_by_mtime(self, tmp_path):
        cache = str(tmp_path / "shown.json")
        ideas = [self._idea("a", mtime=100), self._idea("b", mtime=200)]
        chosen = bi._pick_idea(ideas, cache)
        assert chosen["id"] == "b"

    def test_marks_chosen_idea_as_shown(self, tmp_path):
        cache = str(tmp_path / "shown.json")
        ideas = [self._idea("a", mtime=100)]
        bi._pick_idea(ideas, cache)
        assert "a" in bi._load_shown(cache)

    def test_resets_rotation_when_all_shown(self, tmp_path):
        cache = str(tmp_path / "shown.json")
        ideas = [self._idea("a", mtime=100), self._idea("b", mtime=200)]
        bi._save_shown(cache, {"a", "b"})
        chosen = bi._pick_idea(ideas, cache)
        # Rotation reset — the newest idea is picked again.
        assert chosen["id"] == "b"

    def test_does_not_repeat_within_a_rotation(self, tmp_path):
        cache = str(tmp_path / "shown.json")
        ideas = [self._idea("a", mtime=100), self._idea("b", mtime=200)]
        first = bi._pick_idea(ideas, cache)
        second = bi._pick_idea(ideas, cache)
        assert first["id"] != second["id"]


class TestRender:
    def test_render_creates_output_file(self, tmp_path):
        idea = {"category": "Main Income Idea", "date": "2024-01-01"}
        output_path = str(tmp_path / "out.bmp")
        result = bi._render(idea, "A Headline", "Some detail text.", output_path, {})
        assert result == output_path
        assert os.path.exists(output_path)
        img = Image.open(output_path)
        assert img.size == (bi.WIDTH, bi.HEIGHT)

    def test_render_without_detail_still_creates_file(self, tmp_path):
        idea = {"category": None, "date": None}
        output_path = str(tmp_path / "out.bmp")
        bi._render(idea, "Headline Only", "", output_path, {})
        assert os.path.exists(output_path)

    def test_render_creates_parent_dirs(self, tmp_path):
        output_path = str(tmp_path / "nested" / "dir" / "out.bmp")
        bi._render({"category": "x", "date": "y"}, "H", "D", output_path, {})
        assert os.path.exists(output_path)


class TestRenderEmpty:
    def test_creates_output_file(self, tmp_path):
        output_path = str(tmp_path / "empty.bmp")
        result = bi._render_empty(output_path, {}, "/some/source")
        assert result == output_path
        assert os.path.exists(output_path)


class TestGenerate:
    def test_end_to_end_renders_discovered_idea(self, tmp_path):
        source = tmp_path / "business-ideas"
        idea_dir = source / "main-income-ideas"
        idea_dir.mkdir(parents=True)
        (idea_dir / "2024-01-01-ideas.md").write_text(
            "## 1. Great New Idea\n"
            "This is the detailed body explaining the idea in enough words to pass the length bar.\n"
        )
        output_path = str(tmp_path / "out.bmp")
        config = {
            "business_idea": {
                "output_path": output_path,
                "source_dir": str(source),
                "idea_dirs": ["main-income-ideas"],
                "shown_cache": str(tmp_path / "shown.json"),
            }
        }
        result = bi.generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_no_ideas_found_renders_empty_state(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        config = {
            "business_idea": {
                "output_path": output_path,
                "source_dir": str(tmp_path / "empty-source"),
                "idea_dirs": ["main-income-ideas"],
                "shown_cache": str(tmp_path / "shown.json"),
            }
        }
        result = bi.generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_uses_default_config_keys_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = bi.generate({"business_idea": {"source_dir": str(tmp_path / "nope")}})
        assert result == "images/business_idea.bmp"
        assert os.path.exists(result)
