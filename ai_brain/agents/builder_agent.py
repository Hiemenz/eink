"""
BuilderAgent — writes or modifies code and creates scripts.
"""

from __future__ import annotations

from ai_brain.agents.base_agent import Agent, AgentResult
from ai_brain.tools.file_manager import read_file, write_file
from ai_brain.tools.code_runner import run_python, run_bash
from ai_brain.tools.git_tools import git_commit


class BuilderAgent(Agent):
    name = "BuilderAgent"
    description = "Writes and modifies code files, runs tests, commits results."

    def __init__(self, **kwargs):
        super().__init__(tools=[read_file, write_file, run_python, run_bash, git_commit], **kwargs)

    def run(self, task: dict) -> AgentResult:
        goal = task.get("goal", "")
        target_file = task.get("file")
        existing_code = ""

        if target_file:
            existing_code = read_file(target_file)
            prompt = (
                f"Goal: {goal}\n\n"
                f"Existing file ({target_file}):\n```\n{existing_code}\n```\n\n"
                "Provide the complete updated file content. Output ONLY the code, no explanation."
            )
        else:
            prompt = (
                f"Goal: {goal}\n\n"
                "Write Python code to accomplish this goal. "
                "Output ONLY the code, no explanation, no markdown fences."
            )

        self.log("generating_code", goal)
        code = self.think(
            prompt,
            system="You are an expert Python developer on a Raspberry Pi. Write clean, minimal, working code.",
        )

        # Strip markdown fences if LLM adds them
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # If we have a target file, write it
        output_file = target_file or task.get("output_file")
        if output_file:
            write_result = write_file(output_file, code)
            self.log("wrote_file", write_result)

        # Optionally run it
        if task.get("run", False):
            run_result = run_python(code)
            if run_result["returncode"] != 0:
                return AgentResult(
                    success=False,
                    agent_name=self.name,
                    error=f"Code execution failed: {run_result['stderr']}",
                    output=code,
                )
            self.log("ran_code", run_result["stdout"][:200])

        return AgentResult(
            success=True,
            agent_name=self.name,
            output=code,
            metadata={"file": output_file},
        )
