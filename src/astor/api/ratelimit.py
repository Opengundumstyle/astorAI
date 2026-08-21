"""In-process sliding-window rate limiting.

A signed App Proxy request is authenticated but not metered; one storefront visitor in
a loop is an unbounded Anthropic bill. At single-instance scale an in-memory window is
enough — move to a shared store only if the web service is scaled out.

The clock is injected so the window is testable without sleeping.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Record a hit for `key` and report whether it fits inside the window."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
