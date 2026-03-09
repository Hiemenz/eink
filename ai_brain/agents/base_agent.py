"""
Base Agent — all specialised agents inherit from this.

An agent has:
  name       — unique identifier
  goal       — what this agent is trying to accomplish
  tools      — list of callable tool functions
  context    — arbitrary key/value context dict
  llm        — LLM backend
  memory     — MemoryStore reference

Agents return AgentResult objects so the orchestrator can handle
success/failure uniformly.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ai_brain.llm import LLMInterface, get_llm, get_llm_for_agent
from ai_brain.memory import MemoryStore, get_memory


@dataclass
class AgentResult:
    success: bool
    agent_name: str
    output: Any = None
    error: str = ""
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        if self.success:
            return f"[{self.agent_name}] OK: {str(self.output)[:200]}"
        return f"[{self.agent_name}] FAIL: {self.error}"


class Agent:
    """Base agent class."""

    name: str = "BaseAgent"
    description: str = "Generic agent"

    def __init__(
        self,
        goal: str = "",
        tools: list[Callable] | None = None,
        context: dict | None = None,
        llm: LLMInterface | None = None,
        memory: MemoryStore | None = None,
    ):
        self.goal = goal
        self.tools: list[Callable] = tools or []
        self.context: dict = context or {}
        self.llm: LLMInterface = llm or get_llm_for_agent(self.__class__.name)
        self.memory: MemoryStore = memory or get_memory()
        self._tool_map: dict[str, Callable] = {t.__name__: t for t in self.tools}

    # ------------------------------------------------------------------
    # Override in subclasses
    # ------------------------------------------------------------------

    def run(self, task: dict) -> AgentResult:
        """Execute the task. Subclasses must override this."""
        raise NotImplementedError(f"{self.name}.run() not implemented")

    # ------------------------------------------------------------------
    # Helpers available to all agents
    # ------------------------------------------------------------------

    def think(self, prompt: str, system: str | None = None) -> str:
        """Ask the LLM a question and return its reply."""
        reply = self.llm.simple(prompt, system=system)
        self.memory.log_thought(f"[{self.name}] {prompt[:100]}", context={"reply": reply[:200]})
        return reply

    def use_tool(self, name: str, **kwargs) -> Any:
        """Call a registered tool by name."""
        fn = self._tool_map.get(name)
        if not fn:
            return f"Tool '{name}' not found. Available: {list(self._tool_map)}"
        return fn(**kwargs)

    def log(self, action: str, result: Any = "") -> None:
        self.memory.log_event(self.name, action, result)

    def _safe_run(self, task: dict) -> AgentResult:
        """Wrap run() with error handling and logging."""
        self.log("start", {"task": task.get("goal", "")})
        try:
            result = self.run(task)
            self.log("complete", str(result.output)[:300])
            return result
        except Exception as e:
            err = traceback.format_exc()
            self.log("error", err[:300])
            return AgentResult(success=False, agent_name=self.name, error=str(e))
