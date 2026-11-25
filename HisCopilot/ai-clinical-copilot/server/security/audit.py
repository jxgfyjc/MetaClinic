"""Hash-chained, append-only audit trail.

Each event commits `hash = sha256(prev_hash | canonical_json(fields))`. `verify_chain` replays the chain and
reports the first row whose stored hash (or link to its predecessor) no longer matches — i.e. any in-place
edit or deletion of historical rows is detectable. Writes happen in the *caller's* transaction so an action
and its audit record commit (or roll back) together; failures to audit therefore fail the action.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import AuditEvent
from server.domain.ids import canonical_json, sha256_hex, utcnow

_chain_lock = threading.Lock()          # serialise chain tip reads within the process (SQLite = 1 writer anyway)

_FIELDS = ("ts", "actor_kind", "actor_id", "actor_name", "role", "action", "entity_type", "entity_id",
           "encounter_id", "patient_id", "summary", "detail_json", "ip", "request_id", "outcome")


@dataclass
class Actor:
    kind: str = "system"                # staff | patient | system | ai
    id: str = ""
    name: str = ""
    role: str = ""
    ip: str = ""
    request_id: str = ""

    @staticmethod
    def system(name: str = "system") -> "Actor":
        return Actor(kind="system", id="system", name=name)

    @staticmethod
    def staff(user, ip: str = "", request_id: str = "") -> "Actor":
        return Actor(kind="staff", id=user.id, name=user.display_name, role=user.role, ip=ip, request_id=request_id)

    @staticmethod
    def patient(patient_id: str, ip: str = "", request_id: str = "", label: str = "患者") -> "Actor":
        return Actor(kind="patient", id=patient_id or "anonymous", name=label, role="PATIENT", ip=ip,
                     request_id=request_id)


def _event_fields(ev: AuditEvent) -> dict[str, Any]:
    d = {k: getattr(ev, k) for k in _FIELDS}
    d["ts"] = ev.ts.replace(microsecond=0).isoformat()
    return d


def record(db: Session, actor: Actor, action: str, *, entity_type: str = "", entity_id: str = "",
           encounter_id: str = "", patient_id: str = "", summary: str = "", detail: dict | None = None,
           outcome: str = "ok") -> AuditEvent:
    ev = AuditEvent(ts=utcnow(), actor_kind=actor.kind, actor_id=actor.id or "", actor_name=actor.name or "",
                    role=actor.role or "", action=action, entity_type=entity_type, entity_id=entity_id or "",
                    encounter_id=encounter_id or "", patient_id=patient_id or "", summary=summary[:300],
                    detail_json=detail or {}, ip=actor.ip or "", request_id=actor.request_id or "", outcome=outcome)
    with _chain_lock:
        # The chain covers NATIVE rows only. Imported legacy rows (legacy=True, empty hashes) may be inserted at any
        # time (incremental import) and must never become a chain tip, or the next native event would link to "".
        tip = db.scalar(select(AuditEvent.hash).where(AuditEvent.legacy.is_(False)).order_by(AuditEvent.id.desc()).limit(1)) or ""
        ev.prev_hash = tip
        ev.hash = sha256_hex(tip + "|" + canonical_json(_event_fields(ev)))
        db.add(ev)
        db.flush()
    return ev


def verify_chain(db: Session, *, limit: int | None = None) -> dict:
    """Replay the chain from the first non-legacy row. Returns {ok, checked, first_bad_id, reason}."""
    # populate_existing: always compare against what is PERSISTED, never against rows cached in this
    # session's identity map (a stale cache would hide an out-of-band UPDATE/DELETE).
    q = (select(AuditEvent).where(AuditEvent.legacy.is_(False)).order_by(AuditEvent.id)
         .execution_options(populate_existing=True))
    if limit:
        q = q.limit(limit)
    prev = ""
    checked = 0
    first = True
    for ev in db.scalars(q):
        if first:
            prev = ev.prev_hash            # chain begins at the first native row (legacy rows precede it)
            first = False
        expect = sha256_hex(prev + "|" + canonical_json(_event_fields(ev)))
        if ev.prev_hash != prev:
            return {"ok": False, "checked": checked, "first_bad_id": ev.id,
                    "reason": "broken link (a previous row was removed or altered)"}
        if ev.hash != expect:
            return {"ok": False, "checked": checked, "first_bad_id": ev.id, "reason": "row content was modified"}
        prev = ev.hash
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None, "reason": ""}
