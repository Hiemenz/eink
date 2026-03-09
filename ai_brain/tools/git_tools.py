"""Git tools for BuilderAgent and OperatorAgent."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: str = ".") -> dict:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}


def git_status(repo_path: str = ".") -> str:
    r = _run(["git", "status", "--short"], cwd=repo_path)
    return r["stdout"] or "(clean)"


def git_diff(repo_path: str = ".", file: str | None = None) -> str:
    cmd = ["git", "diff"]
    if file:
        cmd.append(file)
    return _run(cmd, cwd=repo_path)["stdout"] or "(no diff)"


def git_log(repo_path: str = ".", n: int = 5) -> str:
    r = _run(["git", "log", f"-{n}", "--oneline"], cwd=repo_path)
    return r["stdout"] or "(no commits)"


def git_commit(message: str, files: list[str] | None = None, repo_path: str = ".") -> str:
    """Stage files (or all changes) and commit."""
    if files:
        for f in files:
            _run(["git", "add", f], cwd=repo_path)
    else:
        _run(["git", "add", "-A"], cwd=repo_path)
    r = _run(["git", "commit", "-m", message], cwd=repo_path)
    if r["returncode"] == 0:
        return f"Committed: {message}"
    return f"Commit failed: {r['stderr']}"
