"""Configuration loader with YAML support and env var overrides."""

import os
import yaml
from pathlib import Path
from typing import Any

_config: dict = {}


def load_config(path: str | None = None) -> dict:
    """Load config from YAML file. Falls back to defaults if file missing."""
    global _config

    if path is None:
        path = os.environ.get(
            "BRAIN_CONFIG",
            str(Path(__file__).parent / "config.yaml"),
        )

    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            _config = yaml.safe_load(f) or {}
    else:
        print(f"[Config] Warning: config file not found at {path}, using defaults.")
        _config = _defaults()

    _apply_env_overrides(_config)
    return _config


def get_config() -> dict:
    """Return currently loaded config, loading defaults if not yet initialised."""
    if not _config:
        load_config()
    return _config


def _defaults() -> dict:
    return {
        "brain": {"reflection_interval": 60, "max_thoughts_per_cycle": 3, "verbose": True},
        "agents": {"max_parallel": 5, "timeout": 300, "retry_attempts": 2},
        "memory": {"database": "brain.db", "max_events_recalled": 20, "max_knowledge_recalled": 10},
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "temperature": 0.7, "max_tokens": 2048},
        "scheduler": {"check_interval": 10},
        "tools": {"web_search": {"enabled": True, "max_results": 5}, "code_runner": {"enabled": True, "timeout": 30}},
        "skills_dir": "skills",
    }


def _apply_env_overrides(cfg: dict) -> None:
    """Allow LLM_PROVIDER, LLM_MODEL, BRAIN_DB env vars to override config."""
    provider = os.environ.get("LLM_PROVIDER")
    if provider:
        cfg.setdefault("llm", {})["provider"] = provider

    model = os.environ.get("LLM_MODEL")
    if model:
        cfg.setdefault("llm", {})["model"] = model

    db = os.environ.get("BRAIN_DB")
    if db:
        cfg.setdefault("memory", {})["database"] = db

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        cfg.setdefault("llm", {})["anthropic_api_key"] = api_key

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        cfg.setdefault("llm", {})["openai_api_key"] = openai_key
