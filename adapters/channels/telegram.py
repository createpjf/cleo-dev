"""
adapters/channels/telegram.py
Telegram channel adapter using python-telegram-bot (async, long-polling).

Install: pip install python-telegram-bot>=21.0

Features:
  - Long-polling (no public URL needed)
  - Group mention filtering (configurable)
  - MarkdownV2 with fallback to plain text
  - /start, /status, /cancel commands
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

from .base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


def _escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    # Characters that need escaping in MarkdownV2
    special = r'_*[]()~`>#+-=|{}.!'
    result = []
    for char in text:
        if char in special:
            result.append('\\')
        result.append(char)
    return ''.join(result)


class TelegramAdapter(ChannelAdapter):
    """Telegram bot adapter using python-telegram-bot."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._app = None  # telegram.ext.Application
        self._bot_username: str = ""

    @property
    def channel_name(self) -> str:
        return "telegram"

    async def start(self):
        """Initialize and start the Telegram bot with long-polling."""
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            MessageHandler,
            filters,
        )

        token_env = self.config.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        token = os.environ.get(token_env, "")
        if not token:
            raise ValueError(
                f"Telegram bot token not found in env var: {token_env}")

        self._app = (
            ApplicationBuilder()
            .token(token)
            .build()
        )

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self._app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._on_text_message,
            )
        )

        # Get bot info
        await self._app.initialize()
        bot_info = await self._app.bot.get_me()
        self._bot_username = bot_info.username or ""
        logger.info("Telegram bot connected: @%s", self._bot_username)

        # Start polling in background
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        self._running = True

    async def stop(self):
        """Stop the Telegram bot gracefully."""
        self._running = False
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.warning("Telegram shutdown error: %s", e)

    async def send_message(self, chat_id: str, text: str,
                           reply_to: str = "", **kwargs) -> str:
        """Send a message to a Telegram chat."""
        if not self._app:
            return ""

        try:
            # Try MarkdownV2 first
            msg = await self._app.bot.send_message(
                chat_id=int(chat_id),
                text=_escape_markdown_v2(text),
                parse_mode="MarkdownV2",
                reply_to_message_id=int(reply_to) if reply_to else None,
            )
            return str(msg.message_id)
        except Exception:
            # Fallback to plain text
            try:
                msg = await self._app.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                )
                return str(msg.message_id)
            except Exception as e:
                logger.error("Telegram send failed to %s: %s", chat_id, e)
                return ""

    async def send_typing(self, chat_id: str):
        """Send typing indicator."""
        if self._app:
            try:
                await self._app.bot.send_chat_action(
                    chat_id=int(chat_id), action="typing")
            except Exception:
                pass

    # ── Handlers ──

    async def _on_text_message(self, update, context):
        """Handle incoming text messages."""
        if not update.effective_message or not update.effective_chat:
            return

        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        text = msg.text or ""
        is_group = chat.type in ("group", "supergroup")

        # Group mention filtering
        if is_group and self.config.get("mention_required", True):
            if not self._is_mentioned(update):
                return  # Ignore messages without @mention in groups
            # Remove the @mention from the text
            text = self._strip_mention(text)

        if not text.strip():
            return

        # User allowlist check
        allowed = self.config.get("allowed_users", [])
        if allowed and str(user.id) not in [str(u) for u in allowed]:
            logger.debug("Telegram user %s not in allowed list", user.id)
            return

        # Build normalized message
        channel_msg = ChannelMessage(
            channel="telegram",
            chat_id=str(chat.id),
            user_id=str(user.id) if user else "unknown",
            user_name=self._get_display_name(user),
            text=text.strip(),
            message_id=str(msg.message_id),
            reply_to_message_id=(
                str(msg.reply_to_message.message_id)
                if msg.reply_to_message else ""
            ),
            is_group=is_group,
            raw=update,
        )

        if self._callback:
            await self._callback(channel_msg)

    async def _cmd_start(self, update, context):
        """Handle /start command."""
        await update.effective_message.reply_text(
            "🤖 Swarm Agent ready! Send me a task and I'll process it.\n\n"
            "Commands:\n"
            "/status — Check task status\n"
            "/cancel — Cancel current task"
        )

    async def _cmd_status(self, update, context):
        """Handle /status command."""
        try:
            from core.task_board import TaskBoard
            board = TaskBoard()
            data = board._read()
            if not data:
                await update.effective_message.reply_text("No active tasks.")
                return

            lines = []
            for tid, t in data.items():
                status = t.get("status", "unknown")
                desc = t.get("description", "")[:50]
                emoji = {"pending": "⏳", "claimed": "🔄",
                         "completed": "✅", "failed": "❌"}.get(status, "❓")
                lines.append(f"{emoji} {desc}... [{status}]")

            await update.effective_message.reply_text(
                "📋 Task Board:\n" + "\n".join(lines[-10:]))
        except Exception as e:
            await update.effective_message.reply_text(f"Error: {e}")

    async def _cmd_cancel(self, update, context):
        """Handle /cancel command."""
        await update.effective_message.reply_text(
            "⚠️ Task cancellation is not yet supported. "
            "Current task will complete or timeout.")

    # ── Helpers ──

    def _is_mentioned(self, update) -> bool:
        """Check if the bot is @mentioned in a group message."""
        msg = update.effective_message
        if not msg:
            return False

        # Check message entities for bot mention
        if msg.entities:
            for entity in msg.entities:
                if entity.type == "mention":
                    mention_text = msg.text[entity.offset:
                                            entity.offset + entity.length]
                    if mention_text.lower() == f"@{self._bot_username.lower()}":
                        return True
                elif entity.type == "text_mention":
                    # For users without usernames
                    bot_id = self._app.bot.id if self._app else None
                    if entity.user and entity.user.id == bot_id:
                        return True

        return False

    def _strip_mention(self, text: str) -> str:
        """Remove @bot_username from the message text."""
        if self._bot_username:
            pattern = re.compile(
                rf'@{re.escape(self._bot_username)}\s*', re.IGNORECASE)
            text = pattern.sub('', text)
        return text.strip()

    @staticmethod
    def _get_display_name(user) -> str:
        """Get a user's display name."""
        if not user:
            return "Unknown"
        if user.full_name:
            return user.full_name
        if user.username:
            return user.username
        return str(user.id)
