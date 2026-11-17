"""Rate limiter tests."""

import pytest
from starlette.requests import Request

from airoad.ratelimit import (
    DEFAULT_RATE_LIMIT,
    RATE_LIMIT_ENV,
    NoopLimiter,
    RateLimitMisconfigured,
    SlidingWindowLimiter,
    client_key,
    create_limiter,
    parse_rate_limit,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_request(headers=None, client=("1.2.3.4", 1234)) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/detect",
            "headers": raw_headers,
            "client": client,
        }
    )


# --- parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("30/minute", (30, 60.0)),
        ("5/second", (5, 1.0)),
        ("2/hour", (2, 3600.0)),
        ("10", (10, 60.0)),  # bare number means per minute
        (" 7 / minute ", (7, 60.0)),
        ("3/s", (3, 1.0)),
        ("off", (0, 0.0)),
        ("none", (0, 0.0)),
        ("0", (0, 0.0)),
        ("", (0, 0.0)),
    ],
)
def test_parse_rate_limit(spec, expected):
    assert parse_rate_limit(spec) == expected


@pytest.mark.parametrize("spec", ["abc", "30/fortnight", "-1/minute", "minute", "1.5/minute"])
def test_parse_rate_limit_rejects_nonsense(spec):
    with pytest.raises(RateLimitMisconfigured):
        parse_rate_limit(spec)


def test_default_is_thirty_per_minute():
    assert parse_rate_limit(DEFAULT_RATE_LIMIT) == (30, 60.0)


def test_create_limiter_reads_the_environment(monkeypatch):
    monkeypatch.setenv(RATE_LIMIT_ENV, "4/minute")
    limiter = create_limiter()
    assert isinstance(limiter, SlidingWindowLimiter)
    assert limiter.limit == 4
    assert limiter.window_seconds == 60.0


def test_create_limiter_returns_noop_when_off(monkeypatch):
    monkeypatch.setenv(RATE_LIMIT_ENV, "off")
    assert isinstance(create_limiter(), NoopLimiter)


def test_create_limiter_uses_the_default_when_unset(monkeypatch):
    monkeypatch.delenv(RATE_LIMIT_ENV, raising=False)
    limiter = create_limiter()
    assert isinstance(limiter, SlidingWindowLimiter)
    assert limiter.limit == 30


# --- sliding window ---------------------------------------------------------


def test_allows_up_to_the_limit_then_blocks():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60, clock=FakeClock())

    decisions = [limiter.check("ip") for _ in range(3)]
    assert all(decision.allowed for decision in decisions)
    assert [decision.remaining for decision in decisions] == [2, 1, 0]

    blocked = limiter.check("ip")
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.limit == 3
    assert blocked.window_seconds == 60


def test_reports_how_long_to_wait():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, clock=clock)
    assert limiter.check("ip").allowed

    clock.advance(15)
    blocked = limiter.check("ip")
    assert blocked.allowed is False
    assert blocked.retry_after == pytest.approx(45.0)


def test_window_slides_so_the_client_recovers():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10, clock=clock)
    assert limiter.check("ip").allowed
    clock.advance(6)
    assert limiter.check("ip").allowed
    assert limiter.check("ip").allowed is False

    # The first call falls out of the window, freeing one slot.
    clock.advance(5)
    assert limiter.check("ip").allowed


def test_clients_are_limited_independently():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, clock=FakeClock())
    assert limiter.check("a").allowed
    assert limiter.check("a").allowed is False
    assert limiter.check("b").allowed


def test_reset_clears_state():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, clock=FakeClock())
    assert limiter.check("ip").allowed
    limiter.reset()
    assert limiter.check("ip").allowed


def test_noop_limiter_never_blocks():
    limiter = NoopLimiter()
    for _ in range(1000):
        assert limiter.check("ip").allowed


@pytest.mark.parametrize("kwargs", [{"limit": 0, "window_seconds": 60}, {"limit": 5, "window_seconds": 0}])
def test_invalid_construction_is_rejected(kwargs):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(**kwargs)


# --- client identity --------------------------------------------------------


def test_client_key_uses_the_peer_address_by_default(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    assert client_key(make_request()) == "1.2.3.4"


def test_client_key_ignores_forwarded_headers_without_a_proxy(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    request = make_request({"X-Forwarded-For": "9.9.9.9"})
    assert client_key(request) == "1.2.3.4"


@pytest.mark.parametrize(
    "env",
    [{"VERCEL": "1"}, {"TRUST_PROXY_HEADERS": "true"}, {"TRUST_PROXY_HEADERS": "1"}],
)
def test_client_key_honours_forwarded_headers_behind_a_proxy(monkeypatch, env):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    request = make_request({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert client_key(request) == "9.9.9.9"


def test_client_key_falls_back_to_x_real_ip(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    request = make_request({"X-Real-IP": "8.8.8.8"})
    assert client_key(request) == "8.8.8.8"


def test_client_key_handles_a_missing_peer_address(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    assert client_key(make_request(client=None)) == "unknown"
