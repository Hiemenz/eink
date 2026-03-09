"""
Discord Bridge — connects the AI brain to a Discord channel.

Features
--------
1. FEEDBACK LISTENER  — reads messages from a configured Discord channel
   and converts them into objectives/tasks for the brain.

2. STATUS REPORTER    — periodically posts a brain status update to Discord,
   showing active objectives, recent events, and current queue state.

3. COMMAND HANDLER    — responds to simple !commands:
     !status           — brain status
     !objectives       — list active objectives
     !add <text>       — add a new objective
     !done <id>        — mark objective complete
     !tasks            — show task queue

Setup
-----
1. Create a Discord bot at https://discord.com/developers/applications
2. Enable "Message Content Intent" in Bot settings
3. Invite bot to your server with `bot` and `applications.commands` scopes
4. Set env vars:
     DISCORD_TOKEN=<your-bot-token>
     DISCORD_CHANNEL_ID=<channel-id-as-integer>

The bridge runs in a background thread alongside the brain daemon.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_brain.brain import Brain

logger = logging.getLogger("discord_bridge")


class DiscordBridge:
    """
    Wraps a discord.py Client and provides a simple interface the Brain uses
    to send messages and receive commands.
    """

    def __init__(self, token: str | None = None, channel_id: int | None = None):
        self._token = token or os.environ.get("DISCORD_TOKEN", "")
        self._channel_id = channel_id or int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
        self._brain: Brain | None = None
        self._client = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._outbox: list[str] = []   # messages queued before client is ready
        self._lock = threading.Lock()

    def attach_brain(self, brain: "Brain") -> None:
        self._brain = brain

    def enabled(self) -> bool:
        return bool(self._token and self._channel_id)

    # ------------------------------------------------------------------
    # Outbound — send to Discord
    # ------------------------------------------------------------------

    def send(self, message: str) -> None:
        """Queue a message to send to the Discord channel (thread-safe)."""
        if not self.enabled():
            return
        with self._lock:
            self._outbox.append(message[:2000])   # Discord 2000 char limit

    def _drain_outbox(self, channel) -> None:
        """Called from async context — flush queued messages."""
        import asyncio
        with self._lock:
            messages = list(self._outbox)
            self._outbox.clear()
        for msg in messages:
            asyncio.ensure_future(channel.send(msg))

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch discord client in a background thread."""
        if not self.enabled():
            logger.warning("Discord bridge disabled — set DISCORD_TOKEN and DISCORD_CHANNEL_ID")
            return
        self._thread = threading.Thread(target=self._run_client, daemon=True, name="DiscordBridge")
        self._thread.start()

    def stop(self) -> None:
        if self._client:
            import asyncio
            asyncio.run_coroutine_threadsafe(self._client.close(), self._client.loop)

    # ------------------------------------------------------------------
    # Discord client
    # ------------------------------------------------------------------

    def _run_client(self) -> None:
        try:
            import discord
        except ImportError:
            logger.error("discord.py not installed. Run: poetry add discord.py")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            logger.info(f"Discord bot connected as {client.user}")
            self._ready.set()
            channel = client.get_channel(self._channel_id)
            if channel:
                await channel.send("🤖 **AI Brain is online.** Type `!help` for commands.")

        @client.event
        async def on_message(message):
            if message.author == client.user:
                return
            if message.channel.id != self._channel_id:
                return

            content = message.content.strip()
            reply = self._handle_command(content)
            if reply:
                await message.channel.send(reply)

        import asyncio

        async def periodic_drain():
            """Flush outbox every 5 seconds."""
            await client.wait_until_ready()
            channel = client.get_channel(self._channel_id)
            while not client.is_closed():
                if channel:
                    self._drain_outbox(channel)
                await asyncio.sleep(5)

        @client.event
        async def setup_hook():
            client.loop.create_task(periodic_drain())

        client.run(self._token, log_handler=None)

    # ------------------------------------------------------------------
    # Command handler
    # ------------------------------------------------------------------

    def _handle_command(self, text: str) -> str | None:
        if not text.startswith("!"):
            # Not a command — treat as feedback/objective
            if self._brain:
                obj_id = self._brain.memory.add_objective(text, source="discord")
                return f"✅ Added objective #{obj_id}: {text}"
            return None

        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "!help":
            return (
                "**AI Brain Commands**\n"
                "`!status` — brain status\n"
                "`!objectives` — list active objectives\n"
                "`!add <text>` — add objective\n"
                "`!done <id>` — complete objective\n"
                "`!tasks` — task queue\n"
                "`!thoughts` — recent reasoning\n"
                "Or just type naturally to add an objective."
            )

        if cmd == "!status" and self._brain:
            return self._brain.status_report()

        if cmd == "!objectives" and self._brain:
            objs = self._brain.memory.get_objectives("active")
            if not objs:
                return "No active objectives."
            lines = [f"[{o['id']}] {o['objective']}" for o in objs]
            return "**Active Objectives:**\n" + "\n".join(lines)

        if cmd == "!add" and arg and self._brain:
            obj_id = self._brain.memory.add_objective(arg, source="discord")
            return f"✅ Objective #{obj_id} added: {arg}"

        if cmd == "!done" and arg and self._brain:
            try:
                self._brain.memory.complete_objective(int(arg))
                return f"✅ Objective #{arg} marked complete."
            except ValueError:
                return "Usage: !done <id>"

        if cmd == "!tasks" and self._brain:
            tasks = self._brain.memory.get_tasks()
            if not tasks:
                return "Task queue is empty."
            lines = [f"[{t['status']}] {t['task_id']}: {t['description'][:60]}" for t in tasks[-10:]]
            return "**Recent Tasks:**\n" + "\n".join(lines)

        if cmd == "!thoughts" and self._brain:
            thoughts = self._brain.memory.recall_thoughts(5)
            if not thoughts:
                return "No thoughts recorded yet."
            lines = [f"• {t['reasoning'][:120]}" for t in thoughts]
            return "**Recent Thoughts:**\n" + "\n".join(lines)

        return None
