"""
core/conversation_history.py — Session conversation history for cross-task memory.

Persists user↔agent exchanges per channel session so agents can maintain
conversational continuity across separate task submissions.

When a user says "继续" or "now do the other part", the agent can reference
what was discussed before — instead of starting from a blank slate each time.

Storage: memory/conversations/{session_id_safe}.jsonl
Each line: {"role": "user"|"assistant", "content": "...", "ts": 1234567890.0}
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import List, Dict

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = "memory/conversations"
MAX_HISTORY_TURNS = 20          # max user/assistant pairs to keep per session
MAX_LOAD_TOKENS = 4000          # token budget when loading into task context
CHARS_PER_TOKEN = 3             # conservative (English ~4, CJK ~1.5)
SESSION_EXPIRE_HOURS = 24       # start fresh if idle longer than this


def _safe_filename(session_id: str) -> str:
    """Convert session_id (e.g. 'telegram:12345') to safe filename."""
    return re.sub(r'[^\w\-]', '_', session_id) + ".jsonl"


def _session_path(session_id: str) -> str:
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    return os.path.join(CONVERSATIONS_DIR, _safe_filename(session_id))


def save_turn(session_id: str, role: str, content: str):
    """Append a conversation turn to the session history.

    Args:
        session_id: Channel session ID (e.g. "telegram:12345")
        role: "user" or "assistant"
        content: Message content
    """
    path = _session_path(session_id)
    entry = {"role": role, "content": content, "ts": time.time()}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
    except Exception as e:
        logger.warning("Failed to save conversation turn: %s", e)


def load_history(session_id: str,
                 max_tokens: int = MAX_LOAD_TOKENS,
                 expire_hours: float = SESSION_EXPIRE_HOURS) -> List[Dict]:
    """Load recent conversation history within token budget.

    Returns list of {"role": str, "content": str, "ts": float} dicts,
    ordered chronologically (oldest first).

    Returns empty list if:
    - No history exists
    - Session has been idle longer than expire_hours
    """
    path = _session_path(session_id)
    if not os.path.exists(path):
        return []

    try:
        turns: List[Dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if not turns:
            return []

        # Check session expiry — if idle too long, start fresh
        last_ts = turns[-1].get("ts", 0)
        idle_hours = (time.time() - last_ts) / 3600
        if idle_hours > expire_hours:
            logger.info(
                "Session %s expired (idle %.1fh > %.1fh), starting fresh",
                session_id, idle_hours, expire_hours)
            return []

        # Load from most recent, respecting token budget
        selected: List[Dict] = []
        token_count = 0
        for turn in reversed(turns):
            content = turn.get("content", "")
            turn_tokens = len(content) // CHARS_PER_TOKEN
            if token_count + turn_tokens > max_tokens and selected:
                break  # over budget, stop (but always include at least one)
            selected.insert(0, turn)
            token_count += turn_tokens

        return selected

    except Exception as e:
        logger.warning("Failed to load conversation history for %s: %s",
                       session_id, e)
        return []


def format_history_context(turns: List[Dict]) -> str:
    """Format conversation history into a context string for task description.

    This gets prepended to the user's message so the planner can see
    what was discussed previously in this channel session.
    """
    if not turns:
        return ""

    parts = []
    for turn in turns:
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        # Truncate very long messages to keep context reasonable
        if len(content) > 1500:
            content = content[:1400] + "\n...(truncated)"
        label = "🧑 User" if role == "user" else "🤖 Assistant"
        parts.append(f"{label}: {content}")

    return (
        "## Conversation History (this session)\n"
        "Below is the recent conversation with this user. "
        "Use this context to understand references like '继续', "
        "'the previous one', 'do the same for...', etc.\n\n"
        + "\n\n---\n\n".join(parts)
    )


def clear_session(session_id: str):
    """Clear conversation history for a session (e.g. user says '重新开始')."""
    path = _session_path(session_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("Cleared conversation history for %s", session_id)
    except Exception as e:
        logger.warning("Failed to clear conversation history: %s", e)


def get_session_stats(session_id: str) -> Dict:
    """Return stats about a session's conversation history."""
    path = _session_path(session_id)
    if not os.path.exists(path):
        return {"turns": 0, "exists": False}

    try:
        count = 0
        first_ts = 0.0
        last_ts = 0.0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        count += 1
                        ts = entry.get("ts", 0)
                        if first_ts == 0:
                            first_ts = ts
                        last_ts = ts
                    except json.JSONDecodeError:
                        continue
        return {
            "turns": count,
            "exists": True,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "idle_hours": (time.time() - last_ts) / 3600 if last_ts else 0,
        }
    except Exception:
        return {"turns": 0, "exists": False}


def _rotate_if_needed(path: str):
    """Keep only the last MAX_HISTORY_TURNS * 2 entries."""
    max_lines = MAX_HISTORY_TURNS * 2
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            keep = lines[-max_lines:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)
            logger.debug("Rotated conversation history: kept %d of %d turns",
                         len(keep), len(lines))
    except Exception:
        pass
