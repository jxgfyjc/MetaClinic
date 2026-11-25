"""Tiny in-process sliding-window rate limiter (single API process; swap for Redis if ever scaled out)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from server.errors import TooMany

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def rate_limit(key: str, limit: int, per_s: int) -> None:
    now = time.time()
    with _lock:
        q = _hits[key]
        while q and q[0] < now - per_s:
            q.popleft()
        if len(q) >= limit:
            raise TooMany("操作过于频繁，请稍后再试", code="rate_limited", headers={"Retry-After": str(per_s)})
        q.append(now)


def reset() -> None:                         # tests
    with _lock:
        _hits.clear()
