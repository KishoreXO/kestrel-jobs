from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class DomainRateLimiter:
    """Enforces a minimum interval between requests to the same domain (process-wide)."""

    def __init__(self) -> None:
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, domain: str, min_interval: float) -> None:
        async with self._locks[domain]:
            now = time.monotonic()
            delay = self._last[domain] + min_interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[domain] = time.monotonic()

    def reset(self) -> None:
        self._last.clear()


limiter = DomainRateLimiter()
