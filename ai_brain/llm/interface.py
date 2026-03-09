"""
LLM abstraction layer supporting Anthropic, OpenAI, and local Ollama models.
Swap providers via config.yaml or LLM_PROVIDER env var — no code changes needed.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from ai_brain.config import get_config


class LLMInterface(ABC):
    """Base interface all LLM backends must implement."""

    @abstractmethod
    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        """Send a conversation and return the assistant reply as a string."""

    def simple(self, prompt: str, system: str | None = None) -> str:
        """Convenience wrapper for single-turn prompts."""
        return self.chat([{"role": "user", "content": prompt}], system=system)


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicLLM(LLMInterface):
    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: poetry add anthropic")

        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._model = model
        self._kwargs = kwargs

    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        import anthropic

        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": kwargs.get("max_tokens", self._kwargs.get("max_tokens", 2048)),
            "messages": messages,
        }
        if system:
            params["system"] = system

        response = self._client.messages.create(**params)
        return response.content[0].text


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

class OpenAILLM(LLMInterface):
    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed. Run: poetry add openai")

        self._client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self._model = model
        self._kwargs = kwargs

    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=full_messages,
            max_tokens=kwargs.get("max_tokens", self._kwargs.get("max_tokens", 2048)),
            temperature=kwargs.get("temperature", self._kwargs.get("temperature", 0.7)),
        )
        return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Ollama (local) backend — ideal for Raspberry Pi
# ---------------------------------------------------------------------------

class OllamaLLM(LLMInterface):
    def __init__(self, model: str, base_url: str = "http://localhost:11434", **kwargs):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._kwargs = kwargs

    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        import requests

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", self._kwargs.get("temperature", 0.7)),
                "num_predict": kwargs.get("max_tokens", self._kwargs.get("max_tokens", 2048)),
            },
        }
        if system:
            payload["system"] = system

        resp = requests.post(f"{self._base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()["message"]["content"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_llm_instance: LLMInterface | None = None


def get_llm(force_new: bool = False) -> LLMInterface:
    """Return singleton LLM instance built from current config."""
    global _llm_instance
    if _llm_instance and not force_new:
        return _llm_instance

    cfg = get_config().get("llm", {})
    provider = cfg.get("provider", "anthropic").lower()
    model = cfg.get("model", "claude-haiku-4-5-20251001")

    if provider == "anthropic":
        _llm_instance = AnthropicLLM(
            model=model,
            api_key=cfg.get("anthropic_api_key"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.7),
        )
    elif provider == "openai":
        _llm_instance = OpenAILLM(
            model=model,
            api_key=cfg.get("openai_api_key"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.7),
        )
    elif provider == "ollama":
        _llm_instance = OllamaLLM(
            model=model,
            base_url=cfg.get("base_url", "http://localhost:11434"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.7),
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    return _llm_instance
