"""
adapters/channels — Multi-platform channel adapters (OpenClaw-inspired).

Supported channels:
  - Telegram (python-telegram-bot)
  - Discord  (discord.py)
  - Feishu   (lark-oapi)

Usage:
  from adapters.channels import ChannelManager
  manager = ChannelManager(config)
  await manager.start()
"""

from __future__ import annotations

from .base import ChannelAdapter, ChannelMessage, MessageCallback
from .manager import ChannelManager
from .session import SessionStore, ChannelSession

__all__ = [
    "ChannelAdapter",
    "ChannelMessage",
    "MessageCallback",
    "ChannelManager",
    "SessionStore",
    "ChannelSession",
]
