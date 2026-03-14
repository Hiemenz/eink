"""File management tools for agents."""

from __future__ import annotations

import os
from pathlib import Path


def read_file(path: str) -> str:
    """Read and return contents of a file."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading {path}: {e}"


def write_file(path: str, content: str, overwrite: bool = True) -> str:
    """Write content to a file. Returns success/error message."""
    try:
        p = Path(path)
        if p.exists() and not overwrite:
            return f"File already exists: {path}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


def list_dir(path: str = ".", recursive: bool = False) -> list[str]:
    """List files in a directory."""
    try:
        p = Path(path)
        if recursive:
            return [str(f) for f in p.rglob("*") if f.is_file()]
        return [str(f) for f in p.iterdir()]
    except Exception as e:
        return [f"Error listing {path}: {e}"]


def delete_file(path: str) -> str:
    """Delete a file (not directories)."""
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        if p.is_dir():
            return f"Path is a directory, not deleting: {path}"
        p.unlink()
        return f"Deleted: {path}"
    except Exception as e:
        return f"Error deleting {path}: {e}"


def append_file(path: str, content: str) -> str:
    """Append content to a file."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error appending to {path}: {e}"
