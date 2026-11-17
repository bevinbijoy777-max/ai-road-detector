"""Rate limiting for unauthenticated endpoints.

``POST /detect`` is public and spends CPU on every call, so it is limited per
client. The limiter is a sliding window kept in process memory.

On Vercel the counter is per function instance, so it is a guardrail that bounds
what a single caller can do to one instance rather than a globally exact quota.
That is enough to stop a runaway loop; a fleet-wide quota needs shared storage.

Configure it with ``DETECT_RATE_LIMIT`` (default ``30/minute``):

    DETECT_RATE_LIMIT=60/minute
    DETECT_RATE_LIMIT=5/second     # strict
    DETECT_RATE_LIMIT=off          # disable
"""

from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Deque, Dict, Optional, Tuple

from fastapi import Request

RATE_LIMIT_ENV = "DETECT_RATE_LIMIT"
DEFAULT_RATE_LIMIT = "30/minute"

#: Only trust forwarded headers when the app really sits behind a proxy.
TRUST_PROXY_ENV = "TRUST_PROXY_HEADERS"

_UNITS: Dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}

#: Keys are dropped once this many distinct clients have been seen and expired.
_MAX_TRACKED_KEYS = 10_000


class RateLimitMisconfigured(RuntimeError):
    """Raised when ``DETECT_RATE_LIMIT`` cannot be understood."""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float
    window_seconds: float


class RateLimiter(ABC):
    """Contract for a per-client limiter."""

    @abstractmethod
    def check(self, key: str) -> RateLimitDecision:
        """Record an attempt for ``key`` and report whether it is allowed."""


class NoopLimiter(RateLimiter):
    """Unlimited; used when rate limiting is switched off."""

    def check(self, key: str) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=True, limit=0, remaining=0, retry_after=0.0, window_seconds=0.0
        )


class SlidingWindowLimiter(RateLimiter):
    """Allow at most ``limit`` calls per ``window_seconds`` for each key."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window = float(window_seconds)
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    def check(self, key: str) -> RateLimitDecision:
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= self._limit:
                retry_after = max(0.0, events[0] + self._window - now)
                return RateLimitDecision(
                    allowed=False,
                    limit=self._limit,
                    remaining=0,
                    retry_after=retry_after,
                    window_seconds=self._window,
                )

            events.append(now)
            self._prune(now)
            return RateLimitDecision(
                allowed=True,
                limit=self._limit,
                remaining=max(0, self._limit - len(events)),
                retry_after=0.0,
                window_seconds=self._window,
            )

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def _prune(self, now: float) -> None:
        """Drop keys whose window has fully expired, to bound memory."""
        if len(self._events) < _MAX_TRACKED_KEYS:
            return
        cutoff = now - self._window
        for key in [k for k, v in self._events.items() if not v or v[-1] <= cutoff]:
            del self._events[key]


def parse_rate_limit(spec: str) -> Tuple[int, float]:
    """Parse ``"30/minute"`` into ``(30, 60.0)``.

    ``"off"``, ``"none"``, ``"0"`` and empty mean unlimited.
    """
    value = spec.strip().lower()
    if value in {"", "off", "none", "0"}:
        return 0, 0.0

    count_text, _, unit_text = value.partition("/")
    count_text = count_text.strip()
    if not count_text.isdigit():
        raise RateLimitMisconfigured(
            f"{RATE_LIMIT_ENV}={spec!r} is not a rate like '30/minute'"
        )
    count = int(count_text)
    if count == 0:
        return 0, 0.0

    unit = (unit_text or "minute").strip()
    if unit not in _UNITS:
        raise RateLimitMisconfigured(
            f"{RATE_LIMIT_ENV}={spec!r} has an unknown unit {unit!r}; "
            "use second, minute or hour"
        )
    return count, _UNITS[unit]


def create_limiter(spec: Optional[str] = None) -> RateLimiter:
    """Build a limiter from an explicit spec or ``DETECT_RATE_LIMIT``."""
    raw = spec if spec is not None else os.environ.get(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT)
    limit, window = parse_rate_limit(raw)
    if limit == 0:
        return NoopLimiter()
    return SlidingWindowLimiter(limit=limit, window_seconds=window)


@lru_cache(maxsize=1)
def get_limiter() -> RateLimiter:
    """Process-wide limiter, built once per instance."""
    return create_limiter()


def _trust_forwarded_headers() -> bool:
    return bool(os.environ.get("VERCEL")) or os.environ.get(
        TRUST_PROXY_ENV, ""
    ).strip().lower() in {"1", "true", "yes"}


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Forwarded headers are only honoured when a proxy is known to set them,
    otherwise a client could send a fresh ``X-Forwarded-For`` per request and
    never hit the limit.
    """
    if _trust_forwarded_headers():
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"
