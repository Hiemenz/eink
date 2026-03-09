"""
Brain — the central autonomous reasoning loop.

The Brain runs continuously in a while-True loop:
  1. observe_state()     — collect metrics, queue length, objectives
  2. review_memory()     — summarise recent events and knowledge
  3. decide()            — ask LLM what to do next
  4. act()               — spawn agents / run skills / update plan
  5. report()            — post status to Discord
  6. sleep(interval)     — rest until next cycle

The Brain is lightweight: it delegates all real work to Agents and Skills.
Its only job is reasoning and coordination.
"""

from __future__ import annotations

import json
import logging
import re
import signal
import sys
import time
import traceback
from datetime import datetime
from typing import Any

from ai_brain.config import get_config
from ai_brain.llm import get_llm, LLMInterface
from ai_brain.memory import get_memory, MemoryStore
from ai_brain.orchestrator import TaskOrchestrator, Task
from ai_brain.scheduler import JobScheduler
from ai_brain.skills import discover_skills

logger = logging.getLogger("brain")


BRAIN_SYSTEM_PROMPT = """You are an autonomous AI brain running continuously on a Raspberry Pi.
Your role is to:
- Review current objectives set by the user
- Reason about what actions to take next
- Decide which agents to spawn for upcoming tasks
- Ensure you're making progress toward active objectives

You have access to these agents:
- ResearchAgent: search the web and summarise information
- BuilderAgent: write or modify Python code
- OperatorAgent: run shell commands, manage files
- PlannerAgent: break a complex goal into subtasks

Respond ONLY with a JSON object:
{
  "thought": "your current reasoning (1-2 sentences)",
  "action": "none | spawn_agent | run_skill | plan_goal",
  "agent": "ResearchAgent | BuilderAgent | OperatorAgent | PlannerAgent",
  "task_goal": "specific task description for the agent",
  "skill": "skill_name if action=run_skill",
  "priority": 5
}

Keep responses concise. Prioritise user objectives above all else.
"""


class Brain:
    def __init__(self, config_path: str | None = None):
        from ai_brain.config import load_config
        self.config = load_config(config_path)
        from ai_brain.llm import get_llm_for_agent
        self.llm: LLMInterface = get_llm_for_agent("brain")
        self.memory: MemoryStore = get_memory()
        self.orchestrator = TaskOrchestrator(memory=self.memory)
        self.scheduler = JobScheduler()
        self.skills: dict = {}
        self.discord = None        # set by main.py if Discord is configured
        self._running = False
        self._cycle = 0
        self._verbose: bool = self.config.get("brain", {}).get("verbose", True)
        self._interval: int = self.config.get("brain", {}).get("reflection_interval", 60)
        self._max_thoughts: int = self.config.get("brain", {}).get("max_thoughts_per_cycle", 3)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise subsystems and enter the main loop."""
        self._log("Brain starting up...")
        self._load_skills()
        self._register_scheduled_skills()
        self.scheduler.start()

        if self.discord:
            self.discord.attach_brain(self)
            self.discord.start()
            self.discord.send("🧠 Brain loop started. I'll begin working on your objectives.")

        self.memory.log_event("brain", "startup", {"skills": list(self.skills.keys())})

        # Graceful shutdown on SIGINT / SIGTERM
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self._running = True
        self._loop()

    def stop(self) -> None:
        self._running = False
        self.scheduler.stop()
        if self.discord:
            self.discord.stop()
        self.memory.log_event("brain", "shutdown", "graceful")
        self._log("Brain stopped.")

    def _handle_signal(self, sig, frame) -> None:
        self._log(f"Signal {sig} received — shutting down.")
        self.stop()
        sys.exit(0)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            self._cycle += 1
            try:
                self._tick()
            except Exception:
                logger.exception("Brain tick error")
                self.memory.log_event("brain", "tick_error", traceback.format_exc()[:400])
            time.sleep(self._interval)

    def _tick(self) -> None:
        """One full brain cycle."""
        self._log(f"\n{'='*60}\nCycle #{self._cycle} — {datetime.utcnow().isoformat()}\n{'='*60}")

        # 1. Run pending orchestrator tasks first
        results = self.orchestrator.run_all_pending()
        for r in results:
            self._log(str(r))
            if self.discord and not r.success:
                self.discord.send(f"⚠️ Task failed: {r.error[:200]}")

        # 2. Reason about what to do next
        for _ in range(self._max_thoughts):
            decision = self._decide()
            if not decision:
                break
            acted = self._act(decision)
            if not acted:
                break   # nothing to do this cycle

        # 3. Periodic status to Discord (every 5 cycles)
        if self._cycle % 5 == 0 and self.discord:
            self.discord.send(self.status_report())

    # ------------------------------------------------------------------
    # Reasoning
    # ------------------------------------------------------------------

    def _decide(self) -> dict | None:
        """Ask the LLM what to do next based on current state."""
        context = self.memory.build_context_summary()
        queue_status = self.orchestrator.get_status()
        skills_list = ", ".join(self.skills.keys()) or "none"

        prompt = (
            f"{context}\n\n"
            f"Task queue: {queue_status}\n"
            f"Available skills: {skills_list}\n"
            f"Current cycle: #{self._cycle}\n\n"
            "What should I do next?"
        )

        raw = self.llm.simple(prompt, system=BRAIN_SYSTEM_PROMPT)
        self._log(f"Brain decision: {raw[:300]}")

        # Extract JSON
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            self.memory.log_thought(f"Could not parse decision: {raw[:200]}")
            return None

        try:
            decision = json.loads(json_match.group())
            self.memory.log_thought(decision.get("thought", ""), context=decision)
            return decision
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------
    # Acting
    # ------------------------------------------------------------------

    def _act(self, decision: dict) -> bool:
        """Execute the brain's decision. Returns True if action was taken."""
        action = decision.get("action", "none")
        thought = decision.get("thought", "")

        if thought:
            self._log(f"Thought: {thought}")

        if action == "none":
            self._log("Brain decided: no action needed.")
            return False

        if action == "spawn_agent":
            agent = decision.get("agent", "ResearchAgent")
            goal = decision.get("task_goal", "")
            priority = decision.get("priority", 5)
            if goal:
                task = self.orchestrator.submit(goal, agent_name=agent, priority=priority)
                self._log(f"Spawned {agent} for: {goal}")
                if self.discord:
                    self.discord.send(f"🤖 Spawning **{agent}**: {goal}")
                return True

        if action == "run_skill":
            skill_name = decision.get("skill", "")
            return self._run_skill(skill_name)

        if action == "plan_goal":
            goal = decision.get("task_goal", "")
            if goal:
                plan_task = self.orchestrator.submit(goal, agent_name="PlannerAgent", priority=1)
                self._log(f"Planning goal: {goal}")
                return True

        return False

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def _load_skills(self) -> None:
        from pathlib import Path
        skills_dir = str(Path(__file__).parent / "skills")
        self.skills = discover_skills(skills_dir)
        self._log(f"Loaded {len(self.skills)} skills: {list(self.skills.keys())}")

    def _register_scheduled_skills(self) -> None:
        """Register skills with SCHEDULE_INTERVAL in the job scheduler."""
        for name, mod in self.skills.items():
            interval = getattr(mod, "SCHEDULE_INTERVAL", 0)
            if interval > 0:
                self.scheduler.every(
                    seconds=interval,
                    fn=self._run_skill,
                    args=(name,),
                    name=f"skill:{name}",
                    delay=30,  # wait 30s before first run
                )
                self._log(f"Scheduled skill '{name}' every {interval}s")

    def _run_skill(self, skill_name: str) -> bool:
        mod = self.skills.get(skill_name)
        if not mod:
            self._log(f"Skill not found: {skill_name}")
            return False
        try:
            self._log(f"Running skill: {skill_name}")
            result = mod.run(self.memory, self.llm)
            self.memory.log_event("brain", f"skill:{skill_name}", result[:200] if result else "")
            if self.discord and result:
                self.discord.send(f"🔧 **{skill_name}**: {result[:400]}")
            return True
        except Exception as e:
            logger.exception(f"Skill {skill_name} failed")
            self.memory.log_event("brain", f"skill:{skill_name}:error", str(e))
            return False

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def status_report(self) -> str:
        objectives = self.memory.get_objectives("active")
        queue = self.orchestrator.get_status()
        thoughts = self.memory.recall_thoughts(2)
        skills_list = ", ".join(self.skills.keys()) or "none"

        lines = [
            f"**🧠 Brain Status — Cycle #{self._cycle}**",
            f"Active objectives: {len(objectives)}",
        ]
        for o in objectives[:5]:
            lines.append(f"  • [{o['id']}] {o['objective'][:60]}")

        lines.append(f"Task queue: {queue}")
        lines.append(f"Skills loaded: {skills_list}")

        if thoughts:
            lines.append("Recent thought: " + thoughts[0]["reasoning"][:120])

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(msg, flush=True)
        logger.info(msg)
