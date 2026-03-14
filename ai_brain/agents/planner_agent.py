"""
PlannerAgent — breaks high-level goals into an ordered list of subtasks.
"""

from __future__ import annotations

import json
import re

from ai_brain.agents.base_agent import Agent, AgentResult


class PlannerAgent(Agent):
    name = "PlannerAgent"
    description = "Decomposes complex goals into ordered subtasks for the orchestrator."

    _SYSTEM = (
        "You are a precise task planner. Given a high-level goal, output a JSON array of subtasks. "
        "Each subtask must have: goal (str), agent (str: ResearchAgent|BuilderAgent|OperatorAgent|PlannerAgent), "
        "priority (int 1-10, lower=higher priority). "
        "Output ONLY valid JSON, no prose."
    )

    def run(self, task: dict) -> AgentResult:
        goal = task.get("goal", "")
        context = task.get("context", "")
        memory_ctx = self.memory.build_context_summary()

        prompt = (
            f"High-level goal: {goal}\n\n"
            f"Additional context: {context}\n\n"
            f"Brain memory context:\n{memory_ctx}\n\n"
            "Break this into 3-7 concrete subtasks."
        )

        self.log("planning", goal)
        raw = self.think(prompt, system=self._SYSTEM)

        # Extract JSON even if LLM wraps it in markdown
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not json_match:
            return AgentResult(
                success=False,
                agent_name=self.name,
                error=f"LLM did not return valid JSON: {raw[:200]}",
            )

        try:
            subtasks = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            return AgentResult(success=False, agent_name=self.name, error=f"JSON parse error: {e}")

        self.log("plan_created", json.dumps(subtasks)[:300])
        return AgentResult(
            success=True,
            agent_name=self.name,
            output=subtasks,
            metadata={"goal": goal, "subtask_count": len(subtasks)},
        )
