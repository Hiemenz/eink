"""
ResearchAgent — searches the web, summarises findings, stores knowledge.
"""

from __future__ import annotations

from ai_brain.agents.base_agent import Agent, AgentResult
from ai_brain.tools.web_search import web_search, fetch_page


class ResearchAgent(Agent):
    name = "ResearchAgent"
    description = "Searches the web and summarises information into memory."

    def __init__(self, **kwargs):
        super().__init__(tools=[web_search, fetch_page], **kwargs)

    def run(self, task: dict) -> AgentResult:
        query = task.get("goal", task.get("query", ""))
        max_results = task.get("max_results", 5)

        self.log("searching", query)

        # 1. Search
        results = web_search(query, max_results=max_results)
        if not results:
            return AgentResult(success=False, agent_name=self.name, error="No search results")

        # 2. Format snippets for LLM
        snippets = "\n\n".join(
            f"[{i+1}] {r['title']}\n{r['url']}\n{r['snippet']}"
            for i, r in enumerate(results)
        )

        # 3. Ask LLM to summarise
        summary = self.think(
            f"Research task: {query}\n\nSearch results:\n{snippets}\n\n"
            "Write a concise summary of the key findings (3-5 bullet points).",
            system="You are a research assistant. Summarise accurately and concisely.",
        )

        # 4. Store in memory
        self.memory.save_knowledge(
            topic=query,
            summary=summary,
            source=results[0]["url"] if results else "web",
        )
        self.log("summarised", summary[:200])

        return AgentResult(
            success=True,
            agent_name=self.name,
            output=summary,
            metadata={"sources": [r["url"] for r in results], "query": query},
        )
