"""Token-bucket API rate limiter and exponential backoff with jitter for Inflyte & Trackstack.

Designed so interactive playback and 8-album lookahead prefetching feel instantaneous:
- Each ``RateLimiter`` starts with a full ``burst`` bucket (default 10 tokens) so the first
  batch of prefetched tracks and user actions execute with **zero delay**.
- Sustained background requests beyond the burst are paced at ``rate_per_sec`` (with small
  random jitter) to avoid triggering Cloudflare or AWS WAF rate limits.
- Transient ``HTTP 429 Too Many Requests`` and ``5xx`` errors are retried with exponential
  backoff + full jitter, honoring any ``Retry-After`` duration.
"""

from __future__ import annotations

import random
import re
import threading
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)


class RateLimitError(RuntimeError):
    """Raised when an API returns HTTP 429 or a retryable 5xx error."""

    def __init__(self, message: str, status_code: int = 429, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.retry_after = retry_after


def extract_http_status(exc: BaseException) -> Optional[int]:
    if isinstance(exc, RateLimitError):
        return exc.status_code
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    m = _STATUS_RE.search(str(exc))
    if m:
        return int(m.group(1))
    return None


class RateLimiter:
    """Thread-safe token-bucket rate limiter with immediate burst capacity and circuit-breaker pause."""

    def __init__(
        self,
        *,
        rate_per_sec: float = 4.0,
        burst: int = 10,
        jitter_range: tuple[float, float] = (0.02, 0.08),
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_per_sec = max(0.1, float(rate_per_sec))
        self.burst = max(1, int(burst))
        self.jitter_range = jitter_range
        self._sleep = sleep_fn
        self._clock = clock_fn
        self._lock = threading.Lock()
        self._tokens = float(self.burst)
        self._last_refill = self._clock()
        self._paused_until = 0.0
        self._rng = random.Random()

    def pause(self, seconds: float) -> None:
        """Pause background calls for ``seconds`` (e.g., when a 429 Retry-After is received)."""
        with self._lock:
            now = self._clock()
            self._paused_until = max(self._paused_until, now + max(0.0, float(seconds)))

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._clock() < self._paused_until

    def acquire(self, *, priority: bool = False) -> float:
        """Acquire a token, returning the total seconds waited.

        - If ``priority=True`` (user-initiated sort/skip or current track playback), bypasses
          background queue throttling and only waits if an active ``429`` server cooldown is in effect.
        - If tokens are available in the burst bucket, returns ``0.0`` immediately!
        """
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                elapsed = max(0.0, now - self._last_refill)
                self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate_per_sec)
                self._last_refill = now

                if now < self._paused_until:
                    wait_pause = self._paused_until - now
                else:
                    wait_pause = 0.0

                if wait_pause <= 0.0:
                    if priority:
                        self._tokens = max(0.0, self._tokens - 1.0)
                        return waited
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return waited
                    needed = (1.0 - self._tokens) / self.rate_per_sec
                    j_low, j_high = self.jitter_range
                    jitter = self._rng.uniform(j_low, j_high) if j_high > 0 else 0.0
                    sleep_for = needed + jitter
                else:
                    sleep_for = wait_pause

            if sleep_for > 0:
                self._sleep(sleep_for)
                waited += sleep_for


def call_with_backoff(
    fn: Callable[[], T],
    *,
    limiter: Optional[RateLimiter] = None,
    priority: bool = False,
    max_retries: int = 3,
    base_delay: float = 0.75,
    max_delay: float = 12.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Execute ``fn()`` under ``limiter`` with exponential backoff + jitter on 429 / 502 / 503 / 504."""
    rng = random.Random()
    attempt = 0
    while True:
        if limiter is not None:
            limiter.acquire(priority=priority)
        try:
            return fn()
        except Exception as exc:
            status = extract_http_status(exc)
            retryable = status in (429, 502, 503, 504)
            if not retryable or attempt >= max_retries:
                raise
            retry_after = getattr(exc, "retry_after", None)
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                delay = min(float(retry_after), max_delay)
            else:
                cap = min(max_delay, base_delay * (2**attempt))
                delay = rng.uniform(base_delay * 0.5, max(base_delay, cap))
            if limiter is not None and status == 429:
                limiter.pause(delay)
            sleep_fn(delay)
            attempt += 1
