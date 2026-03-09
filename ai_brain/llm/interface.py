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

    # Set by subclasses so token tracking knows which provider/model is in use
    provider: str = "unknown"
    model: str = "unknown"

    # Optional: agent name injected by get_llm_for_agent so usage can be tagged
    _agent_name: str = "unknown"

    @abstractmethod
    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        """Send a conversation and return the assistant reply as a string."""

    def simple(self, prompt: str, system: str | None = None) -> str:
        """Convenience wrapper for single-turn prompts."""
        return self.chat([{"role": "user", "content": prompt}], system=system)

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Write token usage to memory store (best-effort, never raises)."""
        try:
            from ai_brain.memory import get_memory
            get_memory().log_token_usage(
                agent=self._agent_name,
                provider=self.provider,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicLLM(LLMInterface):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: poetry add anthropic")

        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self._kwargs = kwargs

    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", self._kwargs.get("max_tokens", 2048)),
            "messages": messages,
        }
        if system:
            params["system"] = system

        response = self._client.messages.create(**params)
        self._record_usage(response.usage.input_tokens, response.usage.output_tokens)
        return response.content[0].text


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

class OpenAILLM(LLMInterface):
    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed. Run: poetry add openai")

        self._client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.model = model
        self._kwargs = kwargs

    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            max_tokens=kwargs.get("max_tokens", self._kwargs.get("max_tokens", 2048)),
            temperature=kwargs.get("temperature", self._kwargs.get("temperature", 0.7)),
        )
        usage = response.usage
        self._record_usage(usage.prompt_tokens, usage.completion_tokens)
        return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Ollama (local) backend — ideal for Raspberry Pi
# ---------------------------------------------------------------------------

class OllamaLLM(LLMInterface):
    provider = "ollama"

    def __init__(self, model: str, base_url: str = "http://localhost:11434", **kwargs):
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._kwargs = kwargs

    def chat(self, messages: list[dict], system: str | None = None, **kwargs) -> str:
        import requests

        payload: dict[str, Any] = {
            "model": self.model,
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
        data = resp.json()
        # Ollama reports eval_count (output) and prompt_eval_count (input)
        self._record_usage(
            data.get("prompt_eval_count", 0),
            data.get("eval_count", 0),
        )
        return data["message"]["content"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_llm_cache: dict[str, LLMInterface] = {}   # keyed by "default" or agent name


def _build_llm(cfg: dict) -> LLMInterface:
    """Construct an LLMInterface from a config dict."""
    provider = cfg.get("provider", "anthropic").lower()
    model    = cfg.get("model", "claude-haiku-4-5-20251001")

    if provider == "anthropic":
        return AnthropicLLM(
            model=model,
            api_key=cfg.get("anthropic_api_key"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.7),
        )
    if provider == "openai":
        return OpenAILLM(
            model=model,
            api_key=cfg.get("openai_api_key"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.7),
        )
    if provider == "ollama":
        return OllamaLLM(
            model=model,
            base_url=cfg.get("base_url", "http://localhost:11434"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.7),
        )
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def get_llm(force_new: bool = False) -> LLMInterface:
    """Return the default singleton LLM instance."""
    if "default" not in _llm_cache or force_new:
        cfg = get_config().get("llm", {})
        _llm_cache["default"] = _build_llm(cfg)
    return _llm_cache["default"]


def get_llm_for_agent(agent_name: str) -> LLMInterface:
    """
    Return the LLM configured for a specific agent.

    Resolution order:
      1. llm.agents.<agent_name> in config  (per-agent override)
      2. llm default config                 (global fallback)

    If an Ollama agent config has a fallback_provider/fallback_model set and
    the Ollama server is unreachable, this automatically falls back to the
    cloud provider so the brain keeps running even when the local model is down.
    """
    if agent_name in _llm_cache:
        return _llm_cache[agent_name]

    root_cfg   = get_config().get("llm", {})
    agent_cfg  = root_cfg.get("agents", {}).get(agent_name)

    if not agent_cfg:
        instance = get_llm()
        instance._agent_name = agent_name
        _llm_cache[agent_name] = instance
        return instance

    # Merge agent overrides on top of root defaults
    merged = {**root_cfg, **agent_cfg}
    merged.pop("agents", None)

    def _tagged(inst: LLMInterface) -> LLMInterface:
        inst._agent_name = agent_name
        return inst

    if merged.get("provider", "").lower() == "ollama":
        try:
            instance = _build_llm(merged)
            # Quick connectivity check
            instance.simple("ping", system="Reply with one word: pong")
            _llm_cache[agent_name] = _tagged(instance)
            return _llm_cache[agent_name]
        except Exception as e:
            fallback_provider = merged.get("fallback_provider")
            fallback_model    = merged.get("fallback_model")
            if fallback_provider:
                import logging
                logging.getLogger("llm").warning(
                    f"Ollama unavailable for {agent_name} ({e}); "
                    f"falling back to {fallback_provider}/{fallback_model}"
                )
                fallback_cfg = {**merged, "provider": fallback_provider}
                if fallback_model:
                    fallback_cfg["model"] = fallback_model
                _llm_cache[agent_name] = _tagged(_build_llm(fallback_cfg))
                return _llm_cache[agent_name]
            raise

    _llm_cache[agent_name] = _tagged(_build_llm(merged))
    return _llm_cache[agent_name]


def clear_llm_cache() -> None:
    """Force all LLM instances to be rebuilt on next use (e.g. after config reload)."""
    _llm_cache.clear()
