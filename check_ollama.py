#!/usr/bin/env python3
"""
check_ollama.py — verify the remote Ollama server is reachable and
list available models before starting the brain daemon.

Usage:
    poetry run python check_ollama.py
    poetry run python check_ollama.py --url http://192.168.1.50:11434
    poetry run python check_ollama.py --url http://192.168.1.50:11434 --model mistral
"""

from __future__ import annotations

import argparse
import sys

import requests


def check(base_url: str, model: str | None = None) -> bool:
    base_url = base_url.rstrip("/")
    print(f"Checking Ollama at {base_url} ...")

    # 1. Health check
    try:
        r = requests.get(f"{base_url}/", timeout=5)
        if r.status_code == 200:
            print(f"  ✓ Server reachable ({base_url})")
        else:
            print(f"  ✗ Unexpected status: {r.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print(f"  ✗ Cannot connect to {base_url}")
        print()
        print("  On the remote machine, start Ollama with:")
        print("    OLLAMA_HOST=0.0.0.0 ollama serve")
        print()
        print("  Then open the firewall port (Linux):")
        print("    sudo ufw allow from <pi-ip> to any port 11434")
        return False
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False

    # 2. List available models
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        if models:
            print(f"  ✓ Models available: {', '.join(models)}")
        else:
            print("  ⚠ No models installed yet.")
            print("    Run on the remote machine:  ollama pull mistral")
    except Exception as e:
        print(f"  ⚠ Could not list models: {e}")

    # 3. Optional quick inference test
    if model:
        print(f"  Testing inference with '{model}' ...")
        try:
            r = requests.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with just the word PONG"}],
                    "stream": False,
                    "options": {"num_predict": 10},
                },
                timeout=60,
            )
            r.raise_for_status()
            reply = r.json().get("message", {}).get("content", "").strip()
            tok_in  = r.json().get("prompt_eval_count", "?")
            tok_out = r.json().get("eval_count", "?")
            print(f"  ✓ Response: {reply!r}  ({tok_in} in / {tok_out} out tokens)")
        except Exception as e:
            print(f"  ✗ Inference failed: {e}")
            print(f"    Make sure '{model}' is pulled:  ollama pull {model}")
            return False

    print()
    print("All checks passed. Update ai_brain/config/config.yaml:")
    print(f"  llm:")
    print(f"    base_url: \"{base_url}\"")
    if model:
        print(f"    model: \"{model}\"")
    print()
    print("Or set the env var (no config edit needed):")
    print(f"  export OLLAMA_BASE_URL={base_url}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Check remote Ollama connectivity")
    parser.add_argument("--url", default=None, help="Ollama base URL (default: read from config)")
    parser.add_argument("--model", default=None, help="Model to test inference with")
    args = parser.parse_args()

    url = args.url
    model = args.model

    if not url:
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from ai_brain.config import load_config
            cfg = load_config()
            url = cfg.get("llm", {}).get("base_url", "http://localhost:11434")
            if not model:
                model = cfg.get("llm", {}).get("model")
        except Exception:
            url = "http://localhost:11434"

    ok = check(url, model)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
