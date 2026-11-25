"""SQLAlchemy engine/session factory for the v3 backend (sync, SQLite/WAL).

Why sync: SQLite has a single writer; sync sessions in FastAPI's threadpool (and
in job-worker threads) avoid async/greenlet pitfalls and keep transactions
explicit. Every connection gets WAL + foreign_keys + busy_timeout.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _on_connect(dbapi_conn: sqlite3.Connection, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def make_engine(path: str | Path) -> Engine:
    url = f"sqlite:///{path}"
    eng = create_engine(
        url, future=True, echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=10, max_overflow=20, pool_pre_ping=True,
    )
    event.listen(eng, "connect", _on_connect)
    return eng


def init_db(path: str | Path) -> Engine:
    """(Re)bind the process-wide engine. Called once at startup (and by tests)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _engine = make_engine(path)
    _SessionFactory = sessionmaker(_engine, expire_on_commit=False, autoflush=True, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database not initialised; call init_db() first")
    return _engine


def new_session() -> Session:
    if _SessionFactory is None:
        raise RuntimeError("database not initialised; call init_db() first")
    return _SessionFactory()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Services commit explicitly; anything left open is rolled back."""
    s = new_session()
    try:
        yield s
    finally:
        try:
            if s.in_transaction():
                s.rollback()
        finally:
            s.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone unit of work (jobs, CLI tools). Commits on success, rolls back on error."""
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def acquire_write_lock(db: Session) -> None:
    """Take SQLite's single write lock for the rest of this transaction (waits up to busy_timeout).

    Call it BEFORE any check-then-write sequence ("is there already a live plan?", "does this row still have version N?"):
    concurrent requests then queue up and each one's check sees the previous one's COMMITTED result. Without it, N parallel
    requests all pass the check on the same stale state and all write (duplicates, lost updates). The lock is released on commit
    or rollback, so hold it only for the short DB part of a request — never across a model call."""
    db.execute(text("INSERT OR IGNORE INTO number_counters(key, value) VALUES ('__write_lock__', 0)"))
    db.execute(text("UPDATE number_counters SET value = value WHERE key = '__write_lock__'"))
