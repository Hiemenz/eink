"""
Skills are self-contained modules that the brain auto-discovers and loads.

Each skill module must expose:
    SKILL_NAME: str          — unique identifier
    SKILL_DESCRIPTION: str   — one-line description shown to the brain
    run(memory, llm) -> str  — entry point called by the brain

Optionally:
    SCHEDULE_INTERVAL: int   — seconds between auto-runs (0 = manual only)
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("skills")


def discover_skills(skills_dir: str | None = None) -> dict[str, Any]:
    """
    Scan the skills/ directory and load all valid skill modules.
    Returns {skill_name: module} dict.
    """
    if skills_dir is None:
        skills_dir = str(Path(__file__).parent)

    loaded: dict[str, Any] = {}
    skills_path = Path(skills_dir)

    for py_file in sorted(skills_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"ai_brain.skills.{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "SKILL_NAME") and hasattr(mod, "run"):
                loaded[mod.SKILL_NAME] = mod
                logger.info(f"Loaded skill: {mod.SKILL_NAME}")
            else:
                logger.debug(f"Skipping {py_file.name}: missing SKILL_NAME or run()")
        except Exception as e:
            logger.warning(f"Failed to load skill {py_file.name}: {e}")

    return loaded
