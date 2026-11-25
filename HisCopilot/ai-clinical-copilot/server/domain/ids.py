"""Prefixed, time-sortable identifiers and small hashing helpers.

A prefix per entity (`enc_`, `pat_`, ...) makes cross-entity mix-ups (passing a
prescription id where an encounter id is expected) fail fast as 404 instead of
silently matching nothing — and makes logs/support much easier to read.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

_B32 = "0123456789abcdefghjkmnpqrstvwxyz"   # Crockford-ish, lowercase


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_B32[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """`<prefix>_<10 chars ms-timestamp><10 random chars>` — sortable by creation time."""
    ms = int(time.time() * 1000)
    rnd = int.from_bytes(secrets.token_bytes(7), "big")
    return f"{prefix}_{_b32(ms, 10)}{_b32(rnd, 10)}"


def new_token(nbytes: int = 32) -> str:
    """URL-safe opaque secret (session / access tokens)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).decode().rstrip("=")


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    """Deterministic JSON for hashing (sorted keys, no whitespace, UTF-8)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def utcnow() -> datetime:
    """Naive-UTC timestamp (SQLite stores naive; we treat all stored datetimes as UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None
