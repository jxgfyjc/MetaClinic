"""Minimal in-memory cache + local object store abstractions.

MVP backends (assumptions A4): in-process cache + local filesystem.
Interfaces stay stable so Redis/MinIO can drop in later without business changes.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any


class CacheProvider:
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int = 3600) -> None: ...
    async def delete(self, key: str) -> None: ...


class MemoryCache(CacheProvider):
    def __init__(self) -> None:
        self._d: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            v = self._d.get(key)
            if v is None:
                return None
            val, exp = v
            if exp and exp < time.time():
                self._d.pop(key, None)
                return None
            return val

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        async with self._lock:
            self._d[key] = (value, time.time() + ttl if ttl else 0.0)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._d.pop(key, None)


_cache: MemoryCache | None = None


def get_cache() -> CacheProvider:
    global _cache
    if _cache is None:
        _cache = MemoryCache()
    return _cache


# ---------------------------------------------------------------------------
# Object store
# ---------------------------------------------------------------------------
class ObjectStore:
    async def put(self, key: str, data: bytes, mime: str = "application/octet-stream") -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def url(self, key: str) -> str: ...


class LocalObjectStore(ObjectStore):
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, mime: str = "application/octet-stream") -> str:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    async def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    async def url(self, key: str) -> str:
        return f"/objects/{key}"


_store: LocalObjectStore | None = None


def get_object_store() -> ObjectStore:
    from .config import get_settings
    global _store
    if _store is None:
        _store = LocalObjectStore(get_settings().object_store_dir)
    return _store


# ---------------------------------------------------------------------------
# Result cache key (instruct #84)
# ---------------------------------------------------------------------------
def ai_cache_key(*, encounter_id: str, model: str, prompt_version: str,
                 knowledge_version: str, clinical_input: str) -> str:
    h = hashlib.sha256(
        f"{encounter_id}|{model}|{prompt_version}|{knowledge_version}|{clinical_input}".encode()
    ).hexdigest()
    return f"ai:{model}:{h}"
