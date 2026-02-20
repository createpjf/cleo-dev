"""
adapters/channels/feishu.py
Feishu (Lark) channel adapter using lark-oapi SDK.

Install: pip install lark-oapi>=1.3.0

Features:
  - WebSocket long connection (no public webhook needed)
  - Event subscription: im.message.receive_v1
  - Auto token refresh via SDK
  - JSON text message format handling
  - Bridge: sync WS handler → async callback via run_coroutine_threadsafe
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from .base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


class FeishuAdapter(ChannelAdapter):
    """Feishu (Lark) bot adapter using lark-oapi WebSocket."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._ws_client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def channel_name(self) -> str:
        return "feishu"

    async def start(self):
        """Initialize and start the Feishu bot with WebSocket."""
        import lark_oapi as lark
        from lark_oapi.adapter.websocket import WebSocketClient

        app_id_env = self.config.get("app_id_env", "FEISHU_APP_ID")
        app_secret_env = self.config.get("app_secret_env", "FEISHU_APP_SECRET")

        app_id = os.environ.get(app_id_env, "")
        app_secret = os.environ.get(app_secret_env, "")

        if not app_id or not app_secret:
            raise ValueError(
                f"Feishu credentials not found in env vars: "
                f"{app_id_env}, {app_secret_env}")

        self._loop = asyncio.get_event_loop()

        # Build event handler
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )

        # Create WebSocket client
        self._ws_client = (
            WebSocketClient.builder(app_id, app_secret)
            .event_handler(event_handler)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

        # Start WebSocket in background thread
        import threading
        ws_thread = threading.Thread(
            target=self._ws_client.start,
            daemon=True,
            name="feishu-ws",
        )
        ws_thread.start()
        self._running = True
        logger.info("Feishu WebSocket client started")

    async def stop(self):
        """Stop the Feishu bot."""
        self._running = False
        # lark-oapi WS client doesn't expose a clean stop method
        # The daemon thread will exit when the process exits
        logger.info("Feishu adapter stopped")

    async def send_message(self, chat_id: str, text: str,
                           reply_to: str = "", **kwargs) -> str:
        """Send a text message to a Feishu chat."""
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            app_id_env = self.config.get("app_id_env", "FEISHU_APP_ID")
            app_secret_env = self.config.get(
                "app_secret_env", "FEISHU_APP_SECRET")

            client = (
                lark.Client.builder()
                .app_id(os.environ.get(app_id_env, ""))
                .app_secret(os.environ.get(app_secret_env, ""))
                .log_level(lark.LogLevel.WARNING)
                .build()
            )

            # Feishu text message format
            content = json.dumps({"text": text}, ensure_ascii=False)

            body = (
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )

            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None, client.im.v1.message.create, request)

            if response.success():
                msg_id = response.data.message_id if response.data else ""
                return msg_id or ""
            else:
                logger.error("Feishu send failed: code=%s, msg=%s",
                             response.code, response.msg)
                return ""

        except Exception as e:
            logger.error("Feishu send error to %s: %s", chat_id, e)
            return ""

    async def send_typing(self, chat_id: str):
        """Feishu doesn't have a typing indicator API — no-op."""
        pass

    # ── Internal ──

    def _on_message_sync(self, data):
        """
        Synchronous event handler (called by lark WS thread).
        Bridges to async callback via run_coroutine_threadsafe.
        """
        try:
            event = data.event
            if not event:
                return

            msg = event.message
            if not msg:
                return

            # Only handle text messages
            if msg.message_type != "text":
                logger.debug("Feishu: ignoring non-text message type: %s",
                             msg.message_type)
                return

            # Parse text content (Feishu wraps in JSON)
            text = ""
            try:
                content = json.loads(msg.content)
                text = content.get("text", "")
            except (json.JSONDecodeError, TypeError):
                text = msg.content or ""

            if not text.strip():
                return

            # Extract sender info
            sender = event.sender
            sender_id = ""
            sender_type = ""
            if sender and sender.sender_id:
                sender_id = sender.sender_id.open_id or ""
                sender_type = sender.sender_type or ""

            # Skip bot messages
            if sender_type == "app":
                return

            chat_id = msg.chat_id or ""
            is_group = msg.chat_type == "group"

            # Build normalized message
            channel_msg = ChannelMessage(
                channel="feishu",
                chat_id=chat_id,
                user_id=sender_id,
                user_name=sender_id,  # Feishu doesn't expose name in events
                text=text.strip(),
                message_id=msg.message_id or "",
                is_group=is_group,
                raw=data,
            )

            # Bridge to async
            if self._callback and self._loop:
                future = asyncio.run_coroutine_threadsafe(
                    self._callback(channel_msg), self._loop)
                # Wait briefly for the future to complete
                try:
                    future.result(timeout=5.0)
                except Exception as e:
                    logger.error("Feishu callback error: %s", e)

        except Exception as e:
            logger.exception("Feishu message processing error: %s", e)
