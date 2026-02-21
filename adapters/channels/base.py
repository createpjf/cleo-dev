"""
adapters/channels/base.py
Channel adapter abstract base class and normalized message format.

Each platform adapter (Telegram, Discord, Feishu) implements ChannelAdapter.
Inbound messages are normalized to ChannelMessage for uniform processing.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional


@dataclass
class ChannelMessage:
    """Normalized inbound message from any channel."""

    channel: str            # "telegram" | "discord" | "feishu"
    chat_id: str            # channel-specific chat/group ID
    user_id: str            # channel-specific user ID
    user_name: str          # display name
    text: str               # message text content
    message_id: str = ""    # channel-specific message ID (for replies)
    reply_to_message_id: str = ""   # if replying to a previous message
    is_group: bool = False  # True if from a group chat
    attachments: list[dict] = field(default_factory=list)
    raw: Any = None         # original SDK message object (not serialized)
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""    # auto-generated: "{channel}:{chat_id}"

    def __post_init__(self):
        if not self.session_id:
            self.session_id = f"{self.channel}:{self.chat_id}"


# Type alias for the message callback
MessageCallback = Callable[[ChannelMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    """
    Abstract channel adapter. Each platform implements this.

    Lifecycle:
        adapter = TelegramAdapter(config)
        adapter.set_callback(on_message)
        await adapter.start()    # non-blocking, starts listening
        ...
        await adapter.stop()     # graceful shutdown
    """

    def __init__(self, config: dict):
        self.config = config
        self._callback: Optional[MessageCallback] = None
        self._running = False

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Return channel identifier: 'telegram', 'discord', 'feishu'."""
        ...

    def set_callback(self, callback: MessageCallback):
        """Register the inbound message handler (called by ChannelManager)."""
        self._callback = callback

    @abstractmethod
    async def start(self):
        """Start listening for messages. Non-blocking."""
        ...

    @abstractmethod
    async def stop(self):
        """Graceful shutdown."""
        ...

    @abstractmethod
    async def send_message(self, chat_id: str, text: str,
                           reply_to: str = "", **kwargs) -> str:
        """Send a message to a channel chat. Returns sent message ID."""
        ...

    async def send_file(self, chat_id: str, file_path: str,
                        caption: str = "", reply_to: str = "",
                        **kwargs) -> str:
        """Send a file to a channel chat. Returns sent message ID.

        Default implementation: fallback to text with filename.
        Override in subclass for native file upload (e.g., Telegram send_document).
        """
        filename = os.path.basename(file_path)
        text = f"📎 {filename}"
        if caption:
            text += f"\n{caption}"
        return await self.send_message(chat_id, text, reply_to=reply_to)

    async def send_typing(self, chat_id: str):
        """Optional: send typing indicator while processing."""
        pass

    async def health_check(self) -> bool:
        """Check if the adapter is alive and connected. Override for real checks."""
        return self._running

    async def reconnect(self):
        """Reconnect the adapter. Override with platform-specific logic."""
        await self.stop()
        await self.start()

    def is_enabled(self) -> bool:
        """Check if this channel is enabled in config."""
        return self.config.get("enabled", False)
