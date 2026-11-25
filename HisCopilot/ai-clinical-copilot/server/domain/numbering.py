"""Human-readable sequential numbers (E20261029-003, RX…, M…, P…) — allocated inside the caller's transaction.

`when` lets the legacy importer number a migrated row by its ORIGINAL date (E20261013-004 for an Oct-13 visit)
instead of the import date."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from server.domain.ids import utcnow


def _next(db: Session, column, prefix: str, width: int) -> str:
    """Allocate the next number for `prefix` atomically.

    `INSERT OR IGNORE` (lazy seed from the highest existing number, so legacy/imported rows are respected) followed by ONE
    `UPDATE … SET value = MAX(value, :seed) + 1` and a read-back, all in the caller's transaction. The UPDATE takes SQLite's
    write lock, so concurrent requests queue up (busy_timeout) instead of both picking the same number; `MAX(value, seed)`
    also self-heals if rows were ever inserted with numbers beyond the counter. A rollback returns the number (no gaps).
    """
    last = db.scalar(select(func.max(column)).where(column.like(f"{prefix}%")))
    seed = int(last[len(prefix):]) if last and last[len(prefix):].isdigit() else 0
    db.execute(text("INSERT OR IGNORE INTO number_counters(key, value) VALUES (:k, :s)"), {"k": prefix, "s": seed})
    db.execute(text("UPDATE number_counters SET value = MAX(value, :s) + 1 WHERE key = :k"), {"k": prefix, "s": seed})
    n = db.execute(text("SELECT value FROM number_counters WHERE key = :k"), {"k": prefix}).scalar_one()
    return f"{prefix}{n:0{width}d}"


def next_mrn(db: Session, when: datetime | None = None) -> str:
    from server.db.models import Patient
    return _next(db, Patient.mrn, f"P{(when or utcnow()):%y%m%d}", 4)


def next_encounter_no(db: Session, when: datetime | None = None) -> str:
    from server.db.models import Encounter
    return _next(db, Encounter.encounter_no, f"E{(when or utcnow()):%Y%m%d}-", 3)


def next_rx_no(db: Session, when: datetime | None = None) -> str:
    from server.db.models import Prescription
    return _next(db, Prescription.rx_no, f"RX{(when or utcnow()):%Y%m%d}-", 4)


def next_record_no(db: Session, when: datetime | None = None) -> str:
    from server.db.models import MedicalRecord
    return _next(db, MedicalRecord.record_no, f"M{(when or utcnow()):%Y%m%d}-", 4)
