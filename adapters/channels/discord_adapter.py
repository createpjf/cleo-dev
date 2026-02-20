"""
adapters/channels/discord_adapter.py
Discord channel adapter using discord.py.

File named discord_adapter.py to avoid conflicts with the discord package.

Install: pip install discord.py>=2.3

Features:
  - DM: always responds
  - Server channels: responds only when @mentioned (configurable)
  - Channel allowlist for servers
  - 2000 char message limit
  - Typing indicator support
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


class DiscordAdapter(ChannelAdapter):
    """Discord bot adapter using discord.py."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._client = None
        self._ready_event = asyncio.Event()

    @property
    def channel_name(self) -> str:
        return "discord"

    async def start(self):
        """Initialize and start the Discord bot."""
        import discord

        token_env = self.config.get("bot_token_env", "DISCORD_BOT_TOKEN")
        token = os.environ.get(token_env, "")
        if not token:
            raise ValueError(
                f"Discord bot token not found in env var: {token_env}")

        intents = discord.Intents.default()
        intents.message_content = True

        self._client = discord.Client(intents=intents)
        adapter = self  # capture for nested class

        @self._client.event
        async def on_ready():
            logger.info("Discord bot connected: %s (ID: %s)",
                        adapter._client.user.name,
                        adapter._client.user.id)
            adapter._ready_event.set()

        @self._client.event
        async def on_message(message):
            await adapter._handle_message(message)

        # Start client in background task
        asyncio.create_task(self._run_client(token))

        # Wait for ready with timeout
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Discord bot did not become ready within 30s")

        self._running = True

    async def _run_client(self, token: str):
        """Run the Discord client (blocking)."""
        try:
            await self._client.start(token)
        except Exception as e:
            logger.error("Discord client error: %s", e)

    async def stop(self):
        """Stop the Discord bot gracefully."""
        self._running = False
        if self._client and not self._client.is_closed():
            try:
                await self._client.close()
            except Exception as e:
                logger.warning("Discord shutdown error: %s", e)

    async def send_message(self, chat_id: str, text: str,
                           reply_to: str = "", **kwargs) -> str:
        """Send a message to a Discord channel."""
        if not self._client:
            return ""

        try:
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))

            # Discord has 2000 char limit — handled by ChannelManager chunking
            msg = await channel.send(text[:2000])
            return str(msg.id)
        except Exception as e:
            logger.error("Discord send failed to %s: %s", chat_id, e)
            return ""

    async def send_typing(self, chat_id: str):
        """Send typing indicator."""
        if not self._client:
            return
        try:
            channel = self._client.get_channel(int(chat_id))
            if channel:
                await channel.trigger_typing()
        except Exception:
            pass

    # ── Internal ──

    async def _handle_message(self, message):
        """Process an incoming Discord message."""
        import discord

        # Ignore own messages
        if message.author == self._client.user:
            return

        # Ignore bot messages
        if message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_group = not is_dm

        # For server messages, check if bot is mentioned
        if is_group and self.config.get("mention_required", True):
            if not self._client.user.mentioned_in(message):
                return

        # Channel allowlist check (server channels only)
        if is_group:
            allowed = self.config.get("allowed_channels", [])
            if allowed and str(message.channel.id) not in \
                    [str(c) for c in allowed]:
                return

        # Extract text, removing @mention
        text = message.content
        if is_group and self._client.user:
            text = text.replace(
                f"<@{self._client.user.id}>", "").strip()
            text = text.replace(
                f"<@!{self._client.user.id}>", "").strip()

        if not text.strip():
            return

        # Build normalized message
        channel_msg = ChannelMessage(
            channel="discord",
            chat_id=str(message.channel.id),
            user_id=str(message.author.id),
            user_name=message.author.display_name or message.author.name,
            text=text.strip(),
            message_id=str(message.id),
            is_group=is_group,
            raw=message,
        )

        if self._callback:
            await self._callback(channel_msg)
