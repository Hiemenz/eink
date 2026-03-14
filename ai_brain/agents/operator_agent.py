"""
OperatorAgent — runs shell commands, manages files, executes jobs.
"""

from __future__ import annotations

from ai_brain.agents.base_agent import Agent, AgentResult
from ai_brain.tools.file_manager import read_file, write_file, list_dir, delete_file
from ai_brain.tools.code_runner import run_bash, run_python


class OperatorAgent(Agent):
    name = "OperatorAgent"
    description = "Executes shell commands and manages the file system."

    # Commands that could be destructive — require explicit task flag
    _DANGEROUS = {"rm -rf", "mkfs", "dd if=", "shutdown", "reboot"}

    def __init__(self, **kwargs):
        super().__init__(tools=[run_bash, run_python, read_file, write_file, list_dir, delete_file], **kwargs)

    def run(self, task: dict) -> AgentResult:
        command = task.get("command")
        goal = task.get("goal", "")

        # If no explicit command, ask LLM to generate one
        if not command and goal:
            command = self.think(
                f"Goal: {goal}\n\nProvide a single safe bash command to accomplish this. Output ONLY the command.",
                system="You are a Linux sysadmin. Output only the shell command, no explanation.",
            ).strip()

        if not command:
            return AgentResult(success=False, agent_name=self.name, error="No command specified")

        # Safety check
        if not task.get("allow_dangerous", False):
            for danger in self._DANGEROUS:
                if danger in command:
                    return AgentResult(
                        success=False,
                        agent_name=self.name,
                        error=f"Blocked dangerous command: {command}",
                    )

        self.log("execute", command)
        result = run_bash(command)
        success = result["returncode"] == 0
        output = result["stdout"] if success else result["stderr"]

        self.log("result", output[:200])
        return AgentResult(
            success=success,
            agent_name=self.name,
            output=output,
            error=result["stderr"] if not success else "",
            metadata={"command": command, "returncode": result["returncode"]},
        )
