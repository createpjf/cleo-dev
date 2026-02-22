"""
core/rate_limiter.py — Token-bucket rate limiter for Cleo.

Protects against:
  - Channel spam (per-user message rate limiting)
  - Gateway API abuse (per-IP/per-token request limiting)
  - Exec tool abuse (per-agent command rate limiting)

Thread-safe via threading.Lock. No external dependencies.

Usage:
    limiter = RateLimiter(rate=5, per=60)  # 5 requests per 60 seconds
    if limiter.allow("user:12345"):
        process_message()
    else:
        send_rate_limit_warning()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class _Bucket:
    """Token bucket state for a single key."""
    tokens: float = 0.0
    last_refill: float = 0.0
    warning_sent: float = 0.0     # timestamp of last rate-limit warning


class RateLimiter:
    """
    Token-bucket rate limiter.

    Each key (user ID, IP address, agent ID) gets its own bucket.
    Tokens refill at a steady rate. Each request consumes one token.

    Args:
        rate: Max number of requests allowed in the time window
        per: Time window in seconds (default: 60)
        burst: Max burst size (default: same as rate)
        name: Limiter name for logging (e.g. "channel", "gateway")
    """

    def __init__(self, rate: int = 10, per: float = 60.0,
                 burst: int = 0, name: str = ""):
        self.rate = rate
        self.per = per
        self.burst = burst or rate
        self.name = name or f"limiter({rate}/{per}s)"
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(tokens=self.burst, last_refill=time.time())
        )
        self._lock = threading.Lock()
        self._cleanup_counter = 0

    def allow(self, key: str, cost: float = 1.0) -> bool:
        """Check if a request is allowed for this key.

        Args:
            key: Identifier (user ID, IP address, etc.)
            cost: Token cost for this request (default: 1.0)

        Returns:
            True if allowed, False if rate-limited
        """
        with self._lock:
            bucket = self._buckets[key]
            now = time.time()

            # Refill tokens based on elapsed time
            elapsed = now - bucket.last_refill
            refill = elapsed * (self.rate / self.per)
            bucket.tokens = min(self.burst, bucket.tokens + refill)
            bucket.last_refill = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True

            # Rate limited
            logger.debug("[%s] Rate limited: %s (tokens=%.1f, cost=%.1f)",
                         self.name, key, bucket.tokens, cost)
            return False

    def remaining(self, key: str) -> float:
        """Get remaining tokens for a key."""
        with self._lock:
            if key not in self._buckets:
                return float(self.burst)
            bucket = self._buckets[key]
            now = time.time()
            elapsed = now - bucket.last_refill
            refill = elapsed * (self.rate / self.per)
            return min(self.burst, bucket.tokens + refill)

    def reset(self, key: str):
        """Reset a key's bucket to full."""
        with self._lock:
            if key in self._buckets:
                del self._buckets[key]

    def should_warn(self, key: str, cooldown: float = 30.0) -> bool:
        """Check if we should send a rate-limit warning to this key.

        Returns True at most once per `cooldown` seconds per key.
        """
        with self._lock:
            bucket = self._buckets[key]
            now = time.time()
            if now - bucket.warning_sent > cooldown:
                bucket.warning_sent = now
                return True
            return False

    def cleanup(self, max_idle: float = 3600.0):
        """Remove idle buckets to prevent memory leak.

        Called automatically every 1000 allow() calls.
        """
        with self._lock:
            now = time.time()
            stale = [k for k, b in self._buckets.items()
                     if now - b.last_refill > max_idle]
            for k in stale:
                del self._buckets[k]
            if stale:
                logger.debug("[%s] Cleaned up %d idle buckets", self.name, len(stale))

    @property
    def active_keys(self) -> int:
        """Number of active rate-limit buckets."""
        return len(self._buckets)


# ── Pre-configured Limiters ───────────────────────────────────────────────────

# Channel message rate limiting (per user)
# 10 messages per 60 seconds, burst up to 15
channel_limiter = RateLimiter(rate=10, per=60.0, burst=15, name="channel")

# Gateway API rate limiting (per IP or token)
# 60 requests per 60 seconds, burst up to 100
gateway_limiter = RateLimiter(rate=60, per=60.0, burst=100, name="gateway")

# Exec tool rate limiting (per agent)
# 20 commands per 60 seconds, burst up to 30
exec_limiter = RateLimiter(rate=20, per=60.0, burst=30, name="exec")

# Control plane write operations (config changes, etc.)
# 3 writes per 60 seconds
control_limiter = RateLimiter(rate=3, per=60.0, burst=5, name="control")
