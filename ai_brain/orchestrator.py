"""
Task Orchestrator — manages the task queue, assigns agents, tracks progress.

Tasks flow through these states:
  pending → in_progress → completed | failed

The orchestrator is intentionally single-threaded to keep Pi resource usage
low. Agent execution is sequential by default; set max_parallel > 1 in
config to enable concurrent ThreadPoolExecutor-based execution.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from typing import Any

from ai_brain.config import get_config
from ai_brain.memory import MemoryStore, get_memory
from ai_brain.agents.base_agent import Agent, AgentResult


@dataclass
class Task:
    goal: str
    agent_name: str = "ResearchAgent"       # which agent to run
    priority: int = 5                        # 1 = highest, 10 = lowest
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: str = "pending"                  # pending | in_progress | completed | failed
    context: dict = field(default_factory=dict)
    result: AgentResult | None = None
    retries: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "agent_name": self.agent_name,
            "priority": self.priority,
            "status": self.status,
        }


# Agent name → class mapping (lazy import to avoid circular deps)
def _agent_registry() -> dict[str, type[Agent]]:
    from ai_brain.agents import ResearchAgent, BuilderAgent, OperatorAgent, PlannerAgent
    return {
        "ResearchAgent": ResearchAgent,
        "BuilderAgent": BuilderAgent,
        "OperatorAgent": OperatorAgent,
        "PlannerAgent": PlannerAgent,
    }


class TaskOrchestrator:
    def __init__(self, memory: MemoryStore | None = None):
        self.memory = memory or get_memory()
        self._queue: list[Task] = []          # sorted by priority
        self._registry = _agent_registry()
        cfg = get_config()
        self._max_parallel: int = cfg.get("agents", {}).get("max_parallel", 1)
        self._max_retries: int = cfg.get("agents", {}).get("retry_attempts", 2)

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def submit(self, goal: str, agent_name: str = "ResearchAgent",
               priority: int = 5, context: dict | None = None) -> Task:
        """Add a task to the queue. Returns the Task object."""
        task = Task(goal=goal, agent_name=agent_name, priority=priority, context=context or {})
        self._queue.append(task)
        self._queue.sort(key=lambda t: t.priority)
        self.memory.save_task(task.task_id, task.goal, task.priority, "pending", agent_name)
        return task

    def submit_task(self, task: Task) -> None:
        """Add a pre-built Task to the queue."""
        self._queue.append(task)
        self._queue.sort(key=lambda t: t.priority)
        self.memory.save_task(task.task_id, task.goal, task.priority, "pending", task.agent_name)

    def get_pending(self) -> list[Task]:
        return [t for t in self._queue if t.status == "pending"]

    def get_status(self) -> dict:
        counts: dict[str, int] = {}
        for t in self._queue:
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def clear_completed(self) -> int:
        before = len(self._queue)
        self._queue = [t for t in self._queue if t.status not in ("completed", "failed")]
        return before - len(self._queue)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_next(self) -> AgentResult | None:
        """Execute the highest-priority pending task. Returns None if queue empty."""
        pending = self.get_pending()
        if not pending:
            return None
        task = pending[0]
        return self._execute(task)

    def run_all_pending(self) -> list[AgentResult]:
        """Run all pending tasks, respecting max_parallel."""
        pending = self.get_pending()
        if not pending:
            return []

        if self._max_parallel <= 1:
            return [self._execute(t) for t in pending]

        results: list[AgentResult] = []
        with ThreadPoolExecutor(max_workers=self._max_parallel) as pool:
            futures: dict[Future, Task] = {pool.submit(self._execute, t): t for t in pending}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    task = futures[future]
                    results.append(AgentResult(success=False, agent_name=task.agent_name, error=str(e)))
        return results

    def _execute(self, task: Task) -> AgentResult:
        task.status = "in_progress"
        self.memory.update_task(task.task_id, "in_progress")

        agent_cls = self._registry.get(task.agent_name)
        if not agent_cls:
            task.status = "failed"
            result = AgentResult(
                success=False,
                agent_name=task.agent_name,
                error=f"Unknown agent: {task.agent_name}",
            )
            self.memory.update_task(task.task_id, "failed", str(result.error))
            return result

        agent = agent_cls(goal=task.goal, context=task.context, memory=self.memory)
        result = agent._safe_run({"goal": task.goal, **task.context})
        task.result = result

        if result.success:
            task.status = "completed"
            self.memory.update_task(task.task_id, "completed", str(result.output)[:500])
        else:
            if task.retries < self._max_retries:
                task.retries += 1
                task.status = "pending"  # retry
                self.memory.update_task(task.task_id, "pending", f"retry {task.retries}")
            else:
                task.status = "failed"
                self.memory.update_task(task.task_id, "failed", result.error[:300])

        return result

    # ------------------------------------------------------------------
    # Skill-spawned subtasks
    # ------------------------------------------------------------------

    def submit_subtasks(self, subtasks: list[dict]) -> list[Task]:
        """Accept PlannerAgent output and enqueue subtasks."""
        created = []
        for s in subtasks:
            t = Task(
                goal=s.get("goal", ""),
                agent_name=s.get("agent", "ResearchAgent"),
                priority=s.get("priority", 5),
                context=s.get("context", {}),
            )
            self.submit_task(t)
            created.append(t)
        return created
