"""
AI Brain — main entry point.

Usage:
    poetry run python -m ai_brain.main [--config path/to/config.yaml]

Environment variables (override config):
    ANTHROPIC_API_KEY   — Anthropic API key
    OPENAI_API_KEY      — OpenAI API key
    LLM_PROVIDER        — anthropic | openai | ollama
    LLM_MODEL           — model name
    BRAIN_DB            — path to DuckDB file
    DISCORD_TOKEN       — Discord bot token
    DISCORD_CHANNEL_ID  — Discord channel ID (integer)
    WEATHER_LAT         — latitude for weather skill
    WEATHER_LON         — longitude for weather skill
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Ensure package root is on sys.path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_brain.config import load_config
from ai_brain.brain import Brain
from ai_brain.discord_bridge import DiscordBridge


def setup_logging(verbose: bool = True) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("brain.log", encoding="utf-8"),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Brain — autonomous agent daemon")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--objective", default=None, help="Add an initial objective and start")
    args = parser.parse_args()

    config = load_config(args.config)
    verbose = args.verbose or config.get("brain", {}).get("verbose", True)
    setup_logging(verbose)

    brain = Brain(config_path=args.config)

    # Wire up Discord if configured
    discord_bridge = DiscordBridge()
    if discord_bridge.enabled():
        brain.discord = discord_bridge
        print("[main] Discord bridge enabled.")
    else:
        print("[main] Discord bridge disabled (set DISCORD_TOKEN + DISCORD_CHANNEL_ID to enable).")

    # Pre-load an objective from CLI
    if args.objective:
        brain.memory.add_objective(args.objective, source="cli")
        print(f"[main] Objective added: {args.objective}")

    print("[main] Starting brain loop. Press Ctrl+C to stop.")
    brain.start()


if __name__ == "__main__":
    main()
