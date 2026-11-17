"""Rate limiting for the public detection endpoint.

``POST /detect`` is unauthenticated and spends CPU on every call, so it is limited
per client. Attempts are recorded in the same database as the reports
(``DATABASE_URL``), so the count is shared by every function instance instead of
resetting on each one.

Configure it with ``DETECT_RATE_LIMIT`` (default ``30/minute``)::

    DETECT_RATE_LIMIT=60/minute
    DETECT_RATE_LIMIT=5/second
    DETECT_RATE_LIMIT=off

Each allowed call stores one short-lived row; expired rows are deleted as the
limiter runs, so the table stays proportional to traffic in the current window.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, Optional, Tuple

from fastapi import Request
from sqlalchemy import (
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    delete,
    func,
    insert,
    select,
)

from airoad.db import build_engine, resolve_database_url

RATE_LIMIT_ENV = "DETECT_RATE_LIMIT"
DEFAULT_RATE_LIMIT = "30/minute"

#: Forwarded headers are only trusted when a proxy is known to set them.
TRUST_PROXY_ENV = "TRUST_PROXY_HEADERS"

#: Longest stored client key (matches ``detect_attempts.client_key``). Callers are
#: truncated to this so a bogus forwarded header cannot overflow the column.
MAX_CLIENT_KEY_LENGTH = 128

_UNITS: Dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "second": 1.0,
    "m": 60.0,
    "min": 60.0,
    "minute": 60.0,
    "h": 3600.0,
    "hour": 3600.0,
}

metadata = MetaData()

#: One row per admitted call. Epoch seconds keep the comparisons portable across
#: SQLite and Postgres (and avoid timezone handling entirely).
attempts_table = Table(
    "detect_attempts",
    metadata,
    Column("client_key", String(MAX_CLIENT_KEY_LENGTH), nullable=False),
    Column("expires_at", Float, nullable=False),
    Index("ix_detect_attempts_key_expiry", "client_key", "expires_at"),
    Index("ix_detect_attempts_expiry", "expires_at"),
)


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


class SqlRateLimiter(RateLimiter):
    """Allow ``limit`` calls per ``window_seconds`` per key, counted in the database.

    Two requests that arrive in the same instant can both be admitted, so the
    window can be exceeded by a small margin under concurrency. It is a guardrail
    against runaway usage, not an exact quota.
    """

    def __init__(
        self,
        engine,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._engine = engine
        self._limit = limit
        self._window = float(window_seconds)
        self._clock = clock
        metadata.create_all(engine)

    def check(self, key: str) -> RateLimitDecision:
        key = key[:MAX_CLIENT_KEY_LENGTH]
        now = self._clock()
        with self._engine.begin() as connection:
            # Expired attempts are cleared before counting, which also bounds the
            # table without a background job.
            connection.execute(
                delete(attempts_table).where(attempts_table.c.expires_at <= now)
            )
            used = int(
                connection.execute(
                    select(func.count())
                    .select_from(attempts_table)
                    .where(attempts_table.c.client_key == key)
                ).scalar_one()
            )

            if used >= self._limit:
                soonest = connection.execute(
                    select(func.min(attempts_table.c.expires_at)).where(
                        attempts_table.c.client_key == key
                    )
                ).scalar_one()
                return RateLimitDecision(
                    allowed=False,
                    limit=self._limit,
                    remaining=0,
                    retry_after=max(0.0, float(soonest) - now) if soonest else 0.0,
                    window_seconds=self._window,
                )

            connection.execute(
                insert(attempts_table).values(
                    client_key=key, expires_at=now + self._window
                )
            )

        return RateLimitDecision(
            allowed=True,
            limit=self._limit,
            remaining=max(0, self._limit - used - 1),
            retry_after=0.0,
            window_seconds=self._window,
        )


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


def create_limiter(
    spec: Optional[str] = None, database_url: Optional[str] = None
) -> RateLimiter:
    """Build a limiter from ``DETECT_RATE_LIMIT`` and ``DATABASE_URL``."""
    raw = spec if spec is not None else os.environ.get(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT)
    limit, window = parse_rate_limit(raw)
    if limit == 0:
        return NoopLimiter()
    return SqlRateLimiter(
        build_engine(resolve_database_url(database_url)), limit, window
    )


@lru_cache(maxsize=1)
def get_limiter() -> RateLimiter:
    """Process-wide limiter, created once per instance."""
    return create_limiter()


def client_key(request: Request) -> str:
    """Caller identity, capped at :data:`MAX_CLIENT_KEY_LENGTH` for storage."""

    return _raw_client_key(request)[:MAX_CLIENT_KEY_LENGTH]


def _raw_client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Forwarded headers are only honoured when a proxy is known to set them,
    otherwise a client could send a fresh ``X-Forwarded-For`` per request and
    never hit the limit.
    """
    trusted = bool(os.environ.get("VERCEL")) or os.environ.get(
        TRUST_PROXY_ENV, ""
    ).strip().lower() in {"1", "true", "yes"}
    if trusted:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first

    client = request.client
    return client.host if client is not None and client.host else "unknown"
