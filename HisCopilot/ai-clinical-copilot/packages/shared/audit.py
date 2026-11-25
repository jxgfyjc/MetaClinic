"""Audit log — append-only (instruct #38)."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from .config import REPO_ROOT

_AUDIT_LOG = REPO_ROOT / "data" / "audit.ndjson"
_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
_LOCK = asyncio.Lock()


async def audit_log(
    *,
    actor: str,
    action: str,
    target: str = "",
    meta: dict[str, Any] | None = None,
) -> str:
    """Append one structured audit record. Returns the record id."""
    rid = uuid.uuid4().hex
    rec = {
        "id": rid,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actor": actor,
        "action": action,
        "target": target,
        "meta": meta or {},
    }
    line = json.dumps(rec, ensure_ascii=False)
    async with _LOCK:
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return rid


def audit_log_sync(*, actor: str, action: str, target: str = "", meta: dict[str, Any] | None = None) -> str:
    rid = uuid.uuid4().hex
    rec = {
        "id": rid,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actor": actor,
        "action": action,
        "target": target,
        "meta": meta or {},
    }
    with _AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rid


async def audit_for(target: str, limit: int = 200) -> list[dict[str, Any]]:
    """Return audit entries for a specific target (newest-first)."""
    if not _AUDIT_LOG.exists():
        return []
    try:
        lines = _AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if rec.get("target") == target:
            out.append(rec)
        if len(out) >= limit:
            break
    return out
