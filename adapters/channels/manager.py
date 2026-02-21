"""
adapters/channels/manager.py
Central coordinator for all channel adapters.

Responsibilities:
  - Load and start enabled channel adapters from config
  - Receive normalized messages from all channels
  - Serialize task submissions through a sequential queue
  - Monitor task completion via TaskBoard polling
  - Deliver results back to the originating channel/chat
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from threading import Thread
from typing import Optional

from .base import ChannelAdapter, ChannelMessage
from .session import SessionStore

logger = logging.getLogger(__name__)

# Message length limits per platform
PLATFORM_LIMITS = {
    "telegram": 4096,
    "discord": 2000,
    "feishu": 10000,
    "slack": 4000,
}

TASK_TIMEOUT = 600  # 10 minutes
POLL_INTERVAL = 2   # seconds between TaskBoard polls
STATUS_INTERVAL = 30  # seconds before sending "still processing" message
HEALTH_CHECK_INTERVAL = 60  # seconds between health checks


@dataclass
class PendingChannelTask:
    """Tracks a task submitted from a channel, for result delivery."""
    task_id: str
    channel: str
    chat_id: str
    session_id: str
    adapter: ChannelAdapter
    submitted_at: float
    status_sent: bool = False


class ChannelManager:
    """
    Manages all channel adapters and routes messages to/from the Cleo system.

    Usage:
        manager = ChannelManager(config)
        await manager.start()   # starts all enabled adapters
        ...
        await manager.stop()    # graceful shutdown
    """

    def __init__(self, config: dict):
        self.config = config
        self.channels_config = config.get("channels", {})
        self.adapters: list[ChannelAdapter] = []
        self._queue: asyncio.Queue[ChannelMessage] = asyncio.Queue()
        self._sessions = SessionStore()
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self):
        """Load and start all enabled channel adapters."""
        self._running = True
        self._loop = asyncio.get_event_loop()

        # Load adapters
        self._load_adapters()

        if not self.adapters:
            logger.info("No channel adapters enabled")
            return

        # Start all adapters
        for adapter in self.adapters:
            try:
                adapter.set_callback(self._on_message)
                await adapter.start()
                logger.info("Channel adapter started: %s", adapter.channel_name)
            except Exception as e:
                logger.error("Failed to start %s adapter: %s",
                             adapter.channel_name, e)

        # Start the task processor
        self._processor_task = asyncio.create_task(self._task_processor())
        # Start the health monitor
        self._health_task = asyncio.create_task(self._health_monitor())
        logger.info("ChannelManager started with %d adapter(s)", len(self.adapters))

    async def stop(self):
        """Stop all adapters gracefully."""
        self._running = False
        for task in (self._processor_task, self._health_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for adapter in self.adapters:
            try:
                await adapter.stop()
                logger.info("Channel adapter stopped: %s", adapter.channel_name)
            except Exception as e:
                logger.error("Error stopping %s: %s", adapter.channel_name, e)

    async def reload(self):
        """Hot-reload: stop all adapters, re-read config, restart enabled ones."""
        logger.info("Reloading channel manager...")

        # Stop existing adapters
        for adapter in self.adapters:
            try:
                await adapter.stop()
            except Exception as e:
                logger.error("Error stopping %s during reload: %s",
                             adapter.channel_name, e)
        self.adapters.clear()

        # Re-read config from disk (use absolute path for reliability)
        import yaml
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(_project_root, "config", "agents.yaml")
        try:
            with open(config_path, "r") as f:
                fresh_config = yaml.safe_load(f) or {}
            self.channels_config = fresh_config.get("channels", {})
            self.config["channels"] = self.channels_config
        except Exception as e:
            logger.error("Failed to reload channels config from %s: %s",
                         config_path, e)
            return

        # Re-load .env into os.environ (absolute path)
        env_path = os.path.join(_project_root, ".env")
        try:
            from core.env_loader import load_dotenv
            load_dotenv(env_path)
        except Exception:
            pass

        # Load and start adapters
        self._load_adapters()
        for adapter in self.adapters:
            try:
                adapter.set_callback(self._on_message)
                await adapter.start()
                logger.info("Channel adapter restarted: %s", adapter.channel_name)
            except Exception as e:
                logger.error("Failed to restart %s adapter: %s",
                             adapter.channel_name, e)

        # Ensure processor task is running
        if not self._processor_task or self._processor_task.done():
            self._processor_task = asyncio.create_task(self._task_processor())
        # Ensure health monitor is running
        if not self._health_task or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_monitor())

        logger.info("Channel manager reloaded: %d adapter(s) running",
                     len(self.adapters))

    def get_status(self) -> list[dict]:
        """Return status of all adapters (for /v1/channels endpoint)."""
        # Canonical list of known channels
        known_channels = ["telegram", "discord", "feishu", "slack"]
        statuses = []

        for name in known_channels:
            cfg = self.channels_config.get(name, {})
            adapter = self._get_adapter(name)

            status: dict = {
                "channel": name,
                "enabled": cfg.get("enabled", False),
                "running": adapter._running if adapter else False,
            }

            # Add config details for dashboard
            token_env_keys = self._get_token_env_keys(name, cfg)
            status["token_configured"] = all(
                bool(os.environ.get(cfg.get(k, ""), ""))
                for k in token_env_keys
            ) if token_env_keys else False
            status["mention_required"] = cfg.get("mention_required", True)
            status["config"] = {
                k: v for k, v in cfg.items()
                if k not in ("enabled",) and not k.endswith("_token")
            }

            if not adapter and cfg.get("enabled", False):
                status["reason"] = "SDK not installed"
            elif not cfg.get("enabled", False):
                status["reason"] = "disabled"

            statuses.append(status)

        # Include any extra channels from config not in the known list
        for name in self.channels_config:
            if name not in known_channels:
                cfg = self.channels_config[name]
                adapter = self._get_adapter(name)
                statuses.append({
                    "channel": name,
                    "enabled": cfg.get("enabled", False),
                    "running": adapter._running if adapter else False,
                    "token_configured": False,
                    "mention_required": cfg.get("mention_required", True),
                    "config": {},
                    "reason": "disabled or SDK not installed",
                })

        return statuses

    @staticmethod
    def _get_token_env_keys(channel_name: str, cfg: dict) -> list[str]:
        """Return the config keys that reference env vars for tokens."""
        if channel_name in ("telegram", "discord"):
            return ["bot_token_env"]
        elif channel_name == "feishu":
            return ["app_id_env", "app_secret_env"]
        elif channel_name == "slack":
            return ["bot_token_env", "app_token_env"]
        return []

    # ── Session context for tool access ──

    _active_session_path = ".channel_session.json"

    def _save_active_session(self, msg: ChannelMessage):
        """Persist the active channel session so tools (e.g. send_file) can
        route messages back to the correct channel/chat."""
        try:
            data = {
                "session_id": msg.session_id,
                "channel": msg.channel,
                "chat_id": msg.chat_id,
                "user_id": msg.user_id,
                "user_name": msg.user_name,
                "ts": time.time(),
            }
            with open(self._active_session_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Failed to save active session: %s", e)

    @staticmethod
    def get_active_session() -> Optional[dict]:
        """Read the active channel session info (used by tool handlers)."""
        path = ChannelManager._active_session_path
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return None

    async def send_file(self, session_id: str, file_path: str,
                        caption: str = "", reply_to: str = "") -> str:
        """Send a file to a channel chat. Returns sent message ID.

        Args:
            session_id: Channel session ID in format "channel:chat_id"
            file_path: Path to the file to send
            caption: Optional caption/message with the file
            reply_to: Optional message ID to reply to
        """
        if ":" not in session_id:
            logger.error("Invalid session_id for send_file: %s", session_id)
            return ""
        channel, chat_id = session_id.split(":", 1)
        adapter = self._get_adapter(channel)
        if not adapter:
            logger.error("No adapter for channel '%s' in send_file", channel)
            return ""
        return await adapter.send_file(chat_id, file_path, caption, reply_to)

    # ── Internal ──

    def _load_adapters(self):
        """Load enabled channel adapters. Skip gracefully if SDK not installed."""
        for channel_name, channel_cfg in self.channels_config.items():
            if not channel_cfg.get("enabled", False):
                continue

            adapter = self._create_adapter(channel_name, channel_cfg)
            if adapter:
                self.adapters.append(adapter)

    def _create_adapter(self, name: str, cfg: dict) -> Optional[ChannelAdapter]:
        """Create a channel adapter by name. Returns None if SDK unavailable."""
        if name == "telegram":
            try:
                from .telegram import TelegramAdapter
                return TelegramAdapter(cfg)
            except ImportError:
                logger.warning(
                    "Telegram adapter skipped: python-telegram-bot not installed. "
                    "Install with: pip install python-telegram-bot")
                return None

        elif name == "discord":
            try:
                from .discord_adapter import DiscordAdapter
                return DiscordAdapter(cfg)
            except ImportError:
                logger.warning(
                    "Discord adapter skipped: discord.py not installed. "
                    "Install with: pip install discord.py")
                return None

        elif name == "feishu":
            try:
                from .feishu import FeishuAdapter
                return FeishuAdapter(cfg)
            except ImportError:
                logger.warning(
                    "Feishu adapter skipped: lark-oapi not installed. "
                    "Install with: pip install lark-oapi")
                return None

        elif name == "slack":
            try:
                from .slack import SlackAdapter
                return SlackAdapter(cfg)
            except ImportError:
                logger.warning(
                    "Slack adapter skipped: slack-sdk not installed. "
                    "Install with: pip install 'slack-sdk[socket-mode]'")
                return None

        else:
            logger.warning("Unknown channel adapter: %s", name)
            return None

    async def _on_message(self, msg: ChannelMessage):
        """Callback from channel adapters — enqueue message for processing."""
        logger.info("[%s] message from %s (%s): %s",
                    msg.channel, msg.user_name, msg.chat_id, msg.text[:80])
        await self._queue.put(msg)

    async def _task_processor(self):
        """
        Sequential task processor — consumes from queue, one at a time.
        For each message:
          1. Send typing indicator
          2. Submit task via Orchestrator
          3. Poll TaskBoard until complete or timeout
          4. Send result back to channel
        """
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Find the adapter for this channel
            adapter = self._get_adapter(msg.channel)
            if not adapter:
                logger.error("No adapter found for channel: %s", msg.channel)
                continue

            # Track session
            session = self._sessions.get_or_create(
                msg.channel, msg.chat_id, msg.user_id, msg.user_name)

            # Show queue position if there are waiting messages
            queue_size = self._queue.qsize()
            if queue_size > 0:
                await adapter.send_message(
                    msg.chat_id,
                    f"⏳ 任务已排队 (前方还有 {queue_size} 个任务)...")

            try:
                await self._process_message(msg, adapter, session)
            except Exception as e:
                logger.exception("Error processing channel message: %s", e)
                try:
                    await adapter.send_message(
                        msg.chat_id, f"❌ 处理失败: {e}")
                except Exception:
                    pass

    async def _process_message(self, msg: ChannelMessage,
                                adapter: ChannelAdapter,
                                session):
        """Process a single channel message end-to-end."""
        # Send typing indicator
        await adapter.send_typing(msg.chat_id)

        # Save user message to session conversation history
        self._sessions.add_message(
            msg.session_id, "user", msg.text, msg.user_name)

        # Load conversation history for context injection
        session_history = self._sessions.format_history_for_prompt(
            msg.session_id, max_turns=10)
        if session_history:
            logger.info("[session:%s] loaded conversation history for context",
                        msg.session_id)

        # Save channel session info for tool access (e.g., send_file)
        self._save_active_session(msg)

        # Submit task via Orchestrator (with session history)
        task_id = await asyncio.get_event_loop().run_in_executor(
            None, self._submit_task, msg.text, session_history)

        if not task_id:
            await adapter.send_message(msg.chat_id, "❌ 任务提交失败")
            return

        self._sessions.update_task(msg.session_id, task_id)
        await adapter.send_message(
            msg.chat_id, f"🚀 任务已提交，正在处理...")

        # Poll for completion
        result = await self._wait_for_result(
            task_id, msg, adapter)

        if result:
            # Save assistant response to session conversation history
            self._sessions.add_message(
                msg.session_id, "assistant", result[:2000])

            # Chunk and send result
            chunks = self._chunk_message(
                result, PLATFORM_LIMITS.get(msg.channel, 4096))
            for chunk in chunks:
                await adapter.send_message(msg.chat_id, chunk)
                if len(chunks) > 1:
                    await asyncio.sleep(0.5)  # rate limit
        else:
            await adapter.send_message(
                msg.chat_id, "⏰ 任务超时，请稍后重试或简化请求")

    def _submit_task(self, description: str,
                     session_history: str = "") -> Optional[str]:
        """Submit a task via Orchestrator (runs in thread pool).

        Args:
            description: The user's message / task description.
            session_history: Formatted conversation history to inject
                             into the task description for context continuity.
        """
        try:
            from core.orchestrator import Orchestrator
            from core.task_board import TaskBoard

            board = TaskBoard()

            # Archive old tasks for context persistence
            try:
                from core.task_history import save_round
                old_data = board._read()
                if old_data:
                    save_round(old_data)
            except Exception:
                pass

            # Soft clear: archive completed tasks, keep context alive
            # (ContextBus TTL mechanism handles natural expiry)
            board.clear(force=True)
            # NOTE: We no longer destroy .context_bus.json or .mailboxes
            # to preserve cross-round context for session continuity.

            # Inject conversation history into task description
            if session_history:
                full_description = (
                    f"{session_history}\n"
                    f"---\n\n"
                    f"## 当前消息 (Current Message)\n"
                    f"{description}"
                )
            else:
                full_description = description

            # Submit and launch
            orch = Orchestrator()
            task_id = orch.submit(full_description)

            # Run agents in background thread
            def _run():
                try:
                    orch._launch_all()
                    orch._wait()
                except Exception as e:
                    logger.error("Channel task execution error: %s", e)

            t = Thread(target=_run, daemon=True)
            t.start()

            return task_id
        except Exception as e:
            logger.error("Failed to submit channel task: %s", e)
            return None

    async def _wait_for_result(self, task_id: str,
                                msg: ChannelMessage,
                                adapter: ChannelAdapter) -> Optional[str]:
        """Poll TaskBoard until the task completes or times out."""
        from core.task_board import TaskBoard

        start = time.time()
        status_sent = False

        while time.time() - start < TASK_TIMEOUT:
            await asyncio.sleep(POLL_INTERVAL)

            board = TaskBoard()
            data = board._read()

            # Check if all tasks are done (including subtasks)
            if not data:
                continue

            active_states = {"pending", "claimed", "review", "critique",
                             "blocked", "paused", "synthesizing"}
            has_active = any(
                t.get("status") in active_states for t in data.values())

            if not has_active:
                # All done — prefer root task result (Leo's synthesis)
                root = data.get(task_id)
                if root and root.get("result"):
                    return self._clean_result(root["result"])
                # Maybe closeout hasn't written yet, wait briefly
                await asyncio.sleep(2)
                data = board._read()
                root = data.get(task_id)
                if root and root.get("result"):
                    return self._clean_result(root["result"])
                # Fallback to collected executor results
                result = board.collect_results(task_id)
                return self._clean_result(result) if result else "(无结果)"

            # Send typing indicator after 30s (less noisy)
            elapsed = time.time() - start
            if not status_sent and elapsed > STATUS_INTERVAL:
                await adapter.send_typing(msg.chat_id)
                status_sent = True

        return None  # timeout

    @staticmethod
    def _clean_result(text: str) -> str:
        """Strip internal metadata from result before sending to user.

        Removes: agent/task HTML comments, thinking tags, raw JSON task
        delegations, separator lines, and excessive blank lines.
        """
        import re

        # Remove <!-- agent:xxx task:xxx --> markers
        text = re.sub(r'<!--\s*agent:.*?-->', '', text)
        # Remove <think>...</think> reasoning traces
        text = re.sub(r'<think>[\s\S]*?</think>', '', text)
        # Remove raw JSON task arrays (planner delegation output)
        # Matches: [ {"task": "...", "role": "..."} ]  or similar
        text = re.sub(
            r'```json\s*\n?\s*\[[\s\S]*?"(?:task|role)"[\s\S]*?\]\s*\n?```',
            '', text)
        text = re.sub(
            r'^\s*\[\s*\{[^}]*"(?:task|role)"[^}]*\}\s*\]\s*$',
            '', text, flags=re.MULTILINE)
        # Remove separator lines between merged results
        text = re.sub(r'\n---\n', '\n\n', text)
        # Collapse excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    async def _health_monitor(self):
        """Periodically check adapter health and auto-reconnect dead ones."""
        while self._running:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

            for adapter in self.adapters:
                if not self._running:
                    break
                try:
                    alive = await adapter.health_check()
                    if not alive:
                        logger.warning(
                            "Health check failed for %s — triggering reconnect",
                            adapter.channel_name)
                        asyncio.create_task(adapter.reconnect())
                except Exception as e:
                    logger.error("Health check error for %s: %s",
                                 adapter.channel_name, e)

    def _get_adapter(self, channel_name: str) -> Optional[ChannelAdapter]:
        """Find adapter by channel name."""
        for adapter in self.adapters:
            if adapter.channel_name == channel_name:
                return adapter
        return None

    @staticmethod
    def _chunk_message(text: str, max_len: int) -> list[str]:
        """Split a message into chunks respecting platform limits.
        Tries to split at paragraph boundaries, then line boundaries."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break

            # Try to split at a paragraph boundary
            split_at = remaining.rfind("\n\n", 0, max_len)
            if split_at <= 0:
                # Try line boundary
                split_at = remaining.rfind("\n", 0, max_len)
            if split_at <= 0:
                # Try space
                split_at = remaining.rfind(" ", 0, max_len)
            if split_at <= 0:
                # Hard split
                split_at = max_len

            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()

        return chunks


def start_channel_manager(config: dict) -> ChannelManager:
    """
    Start channel manager in a dedicated asyncio thread.
    Called from gateway.start_gateway().

    Always creates a ChannelManager instance (even if no channels are
    currently enabled) so that channels enabled later via the Dashboard
    can be hot-reloaded without restarting the gateway.
    """
    manager = ChannelManager(config)

    def _run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager._loop = loop
        try:
            loop.run_until_complete(manager.start())
            loop.run_forever()
        except Exception as e:
            logger.error("Channel manager event loop error: %s", e)
        finally:
            loop.close()

    thread = Thread(target=_run_loop, daemon=True, name="channel-manager")
    thread.start()
    logger.info("Channel manager thread started")
    return manager
