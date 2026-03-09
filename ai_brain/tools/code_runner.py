"""
Safe code execution tool.
Runs Python snippets in a subprocess with timeout — no eval/exec.
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
from pathlib import Path

from ai_brain.config import get_config


def _timeout() -> int:
    return get_config().get("tools", {}).get("code_runner", {}).get("timeout", 30)


def run_python(code: str, timeout: int | None = None) -> dict:
    """
    Execute Python code in an isolated subprocess.
    Returns {"stdout": str, "stderr": str, "returncode": int}.
    """
    t = timeout or _timeout()
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(textwrap.dedent(code))
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=t,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {t}s", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def run_bash(command: str, timeout: int | None = None) -> dict:
    """
    Execute a bash command in a subprocess.
    Returns {"stdout": str, "stderr": str, "returncode": int}.
    """
    t = timeout or _timeout()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=t,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {t}s", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}
