"""Tests for token-bucket RateLimiter and exponential backoff with jitter."""

from __future__ import annotations

import pytest

from bakkis_sorter.ratelimit import RateLimitError, RateLimiter, call_with_backoff


def test_rate_limiter_burst_is_instant_then_paces() -> None:
    now = [100.0]
    slept: list[float] = []

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(secs: float) -> None:
        slept.append(secs)
        now[0] += secs

    limiter = RateLimiter(
        rate_per_sec=4.0,
        burst=5,
        jitter_range=(0.0, 0.0),
        sleep_fn=fake_sleep,
        clock_fn=fake_clock,
    )

    # First 5 calls (burst=5) must execute with 0.0s wait so lookahead prefetching is never delayed
    for _ in range(5):
        assert limiter.acquire() == 0.0
    assert slept == []

    # 6th call exceeds burst and paces at 1 / 4.0 = 0.25s
    waited = limiter.acquire()
    assert pytest.approx(waited, rel=1e-3) == 0.25
    assert len(slept) == 1

    # Priority user actions bypass normal token wait when not paused by a 429
    assert limiter.acquire(priority=True) == 0.0


def test_call_with_backoff_retries_429_and_honors_retry_after() -> None:
    now = [0.0]
    slept: list[float] = []

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(secs: float) -> None:
        slept.append(secs)
        now[0] += secs

    limiter = RateLimiter(rate_per_sec=10.0, burst=10, sleep_fn=fake_sleep, clock_fn=fake_clock)
    attempts = [0]

    def flaky_endpoint() -> str:
        attempts[0] += 1
        if attempts[0] == 1:
            raise RateLimitError("HTTP 429 Too Many Requests", status_code=429, retry_after=1.5)
        if attempts[0] == 2:
            raise RuntimeError("Server HTTP 503 Unavailable")
        return "ok"

    result = call_with_backoff(
        flaky_endpoint,
        limiter=limiter,
        max_retries=3,
        base_delay=0.5,
        sleep_fn=fake_sleep,
    )
    assert result == "ok"
    assert attempts[0] == 3
    assert slept[0] == pytest.approx(1.5, rel=1e-2)
