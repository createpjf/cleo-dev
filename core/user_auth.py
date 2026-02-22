"""
core/user_auth.py — User authentication and pairing for channel interactions.

Security layers:
  1. Allowlist (existing): Only specific user IDs can interact
  2. Pairing code (new): Unknown users must verify with a one-time code
  3. Rate limiting (new): Integrated with core/rate_limiter.py

Pairing flow:
  1. Unknown user sends first message → bot replies with "Send pairing code"
  2. Admin generates a code via gateway dashboard or CLI
  3. User sends the code → bot verifies and adds user to trusted list
  4. Subsequent messages are processed normally

Trusted users stored in: memory/trusted_users.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from typing import Optional

logger = logging.getLogger(__name__)

TRUSTED_USERS_FILE = "memory/trusted_users.json"
PAIRING_CODES_FILE = "memory/pairing_codes.json"
CODE_EXPIRY_SECONDS = 300  # 5 minutes
MAX_PAIRING_ATTEMPTS = 5   # lockout after 5 wrong codes


def _load_json(path: str) -> dict:
    """Load a JSON file, returning empty dict on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path: str, data: dict):
    """Save a JSON file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class UserAuth:
    """
    Manages user authentication for channel interactions.

    Supports three modes (configured per-channel):
      - "open": All users allowed (default)
      - "allowlist": Only users in allowed_users list
      - "pairing": Unknown users must verify with a one-time code
    """

    def __init__(self, mode: str = "open"):
        """
        Args:
            mode: "open" | "allowlist" | "pairing"
        """
        self.mode = mode

    def is_authorized(self, channel: str, user_id: str,
                      allowed_users: list[str] | None = None) -> bool:
        """Check if a user is authorized to interact.

        Args:
            channel: Channel name ("telegram", "discord", etc.)
            user_id: Platform user ID
            allowed_users: Optional explicit allowlist from config

        Returns:
            True if user is authorized
        """
        if self.mode == "open":
            return True

        if self.mode == "allowlist":
            if not allowed_users:
                return True  # No allowlist = allow all
            return str(user_id) in [str(u) for u in allowed_users]

        if self.mode == "pairing":
            # Check explicit allowlist first
            if allowed_users and str(user_id) in [str(u) for u in allowed_users]:
                return True
            # Check trusted users
            return self._is_trusted(channel, user_id)

        return True  # Unknown mode = open

    def _is_trusted(self, channel: str, user_id: str) -> bool:
        """Check if user is in the trusted users file."""
        data = _load_json(TRUSTED_USERS_FILE)
        key = f"{channel}:{user_id}"
        return key in data.get("users", {})

    def trust_user(self, channel: str, user_id: str,
                   user_name: str = "", reason: str = ""):
        """Add a user to the trusted list.

        Args:
            channel: Channel name
            user_id: Platform user ID
            user_name: Optional display name
            reason: Why they were trusted (e.g. "pairing code verified")
        """
        data = _load_json(TRUSTED_USERS_FILE)
        if "users" not in data:
            data["users"] = {}

        key = f"{channel}:{user_id}"
        data["users"][key] = {
            "channel": channel,
            "user_id": str(user_id),
            "user_name": user_name,
            "trusted_at": time.time(),
            "reason": reason,
        }
        _save_json(TRUSTED_USERS_FILE, data)
        logger.info("[auth] Trusted user: %s (%s) — %s",
                    key, user_name, reason)

    def revoke_user(self, channel: str, user_id: str) -> bool:
        """Remove a user from the trusted list."""
        data = _load_json(TRUSTED_USERS_FILE)
        key = f"{channel}:{user_id}"
        if key in data.get("users", {}):
            del data["users"][key]
            _save_json(TRUSTED_USERS_FILE, data)
            logger.info("[auth] Revoked user: %s", key)
            return True
        return False

    def list_trusted(self) -> list[dict]:
        """List all trusted users."""
        data = _load_json(TRUSTED_USERS_FILE)
        return list(data.get("users", {}).values())

    # ── Pairing Codes ────────────────────────────────────────────────────

    def generate_pairing_code(self, label: str = "") -> str:
        """Generate a one-time pairing code.

        Args:
            label: Optional label for the code (e.g. "for @username")

        Returns:
            6-digit pairing code string
        """
        code = f"{secrets.randbelow(1000000):06d}"

        # Hash the code for storage (don't store plaintext)
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        data = _load_json(PAIRING_CODES_FILE)
        if "codes" not in data:
            data["codes"] = {}

        # Clean expired codes
        now = time.time()
        data["codes"] = {
            k: v for k, v in data["codes"].items()
            if now - v.get("created_at", 0) < CODE_EXPIRY_SECONDS
        }

        data["codes"][code_hash] = {
            "created_at": now,
            "label": label,
            "used": False,
        }
        _save_json(PAIRING_CODES_FILE, data)

        logger.info("[auth] Generated pairing code (label=%s, expires in %ds)",
                    label, CODE_EXPIRY_SECONDS)
        return code

    def verify_pairing_code(self, channel: str, user_id: str,
                            code: str, user_name: str = "") -> dict:
        """Verify a pairing code and trust the user if valid.

        Args:
            channel: Channel name
            user_id: Platform user ID
            code: The code submitted by the user
            user_name: Optional display name

        Returns:
            {"ok": bool, "message": str}
        """
        # Check lockout
        key = f"{channel}:{user_id}"
        data = _load_json(PAIRING_CODES_FILE)
        attempts = data.get("attempts", {})
        attempt_info = attempts.get(key, {"count": 0, "last": 0})

        if attempt_info["count"] >= MAX_PAIRING_ATTEMPTS:
            lockout_remaining = 600 - (time.time() - attempt_info["last"])
            if lockout_remaining > 0:
                return {"ok": False,
                        "message": f"Too many failed attempts. Try again in {int(lockout_remaining)}s."}
            # Reset after lockout period
            attempt_info = {"count": 0, "last": 0}

        # Hash the submitted code
        code_hash = hashlib.sha256(code.strip().encode()).hexdigest()

        codes = data.get("codes", {})
        now = time.time()

        if code_hash in codes:
            entry = codes[code_hash]
            # Check expiry
            if now - entry.get("created_at", 0) > CODE_EXPIRY_SECONDS:
                return {"ok": False, "message": "Code expired. Request a new one."}
            if entry.get("used"):
                return {"ok": False, "message": "Code already used."}

            # Valid! Trust the user
            entry["used"] = True
            entry["used_by"] = key
            entry["used_at"] = now
            data["codes"] = codes

            # Reset attempts
            if key in attempts:
                del attempts[key]
            data["attempts"] = attempts
            _save_json(PAIRING_CODES_FILE, data)

            self.trust_user(channel, user_id, user_name, "pairing code verified")
            return {"ok": True,
                    "message": f"Verified! Welcome, {user_name or user_id}."}

        # Wrong code — increment attempts
        attempt_info["count"] += 1
        attempt_info["last"] = now
        if "attempts" not in data:
            data["attempts"] = {}
        data["attempts"][key] = attempt_info
        _save_json(PAIRING_CODES_FILE, data)

        remaining = MAX_PAIRING_ATTEMPTS - attempt_info["count"]
        return {"ok": False,
                "message": f"Invalid code. {remaining} attempts remaining."}


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: UserAuth | None = None


def get_user_auth(mode: str = "open") -> UserAuth:
    """Get or create the singleton UserAuth instance."""
    global _instance
    if _instance is None:
        _instance = UserAuth(mode=mode)
    return _instance
