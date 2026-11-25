"""Versioned SQL migrations with automatic pre-migration backup and checksum verification.

Layout:  server/db/migrations/NNNN_name.sql   (statements separated by a line `-- ;;`)
                              NNNN_name.py    (optional; defines `upgrade(conn)`)

Rules
  * Applied migrations are immutable — their checksum is stored and re-verified on every start.
  * Before applying ANY pending migration to a database that already holds data, the file is copied with
    SQLite's online-backup API to `<data>/backups/`. Rollback = restore that file (see `restore_backup`).
  * The legacy database (ai_clinical.db) is never opened for writing by this module.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_SPLIT = re.compile(r"^-- ;;\s*$", re.MULTILINE)
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(sql|py)$")

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    checksum VARCHAR(64) NOT NULL DEFAULT '',
    applied_at DATETIME NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class MigrationError(RuntimeError):
    pass


def discover() -> list[Migration]:
    out: list[Migration] = []
    for p in sorted(MIGRATIONS_DIR.iterdir()):
        m = _NAME.match(p.name)
        if m:
            out.append(Migration(int(m.group(1)), m.group(2), p))
    versions = [m.version for m in out]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration versions: {versions}")
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 1, got {versions}")
    return out


def _applied(engine: Engine) -> dict[int, str]:
    with engine.begin() as c:
        c.exec_driver_sql(BOOTSTRAP_SQL)
        rows = c.exec_driver_sql("SELECT version, checksum FROM schema_migrations").all()
    return {int(v): cs for v, cs in rows}


def _has_user_data(db_file: Path) -> bool:
    if not db_file.exists() or db_file.stat().st_size == 0:
        return False
    con = sqlite3.connect(str(db_file))
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_migrations'")]
        return bool(names)
    finally:
        con.close()


def backup_database(db_file: Path, backups_dir: Path, label: str) -> Path:
    """Consistent online backup (safe while other connections are open)."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = backups_dir / f"{db_file.stem}-{label}-{ts}.db"
    src = sqlite3.connect(str(db_file))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def restore_backup(backup_file: Path, db_file: Path) -> None:
    """Rollback helper: replace the live DB with a backup (caller must stop the API first)."""
    if not backup_file.exists():
        raise MigrationError(f"backup not found: {backup_file}")
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_file) + suffix)
        if side.exists():
            side.unlink()
    shutil.copyfile(backup_file, db_file)


def _run_sql(conn, sql: str) -> None:
    for stmt in _SPLIT.split(sql):
        stmt = stmt.strip()
        if stmt:
            conn.exec_driver_sql(stmt)


def migrate(engine: Engine, db_file: Path, backups_dir: Path, *, backup: bool = True) -> list[int]:
    """Apply all pending migrations. Returns the versions applied (empty if up to date)."""
    migs = discover()
    applied = _applied(engine)

    # 1. immutability check on already-applied migrations
    for m in migs:
        if m.version in applied and applied[m.version] and applied[m.version] != m.checksum:
            raise MigrationError(
                f"migration {m.version:04d}_{m.name} was modified after being applied "
                f"(checksum mismatch). Never edit applied migrations — add a new one.")
    unknown = set(applied) - {m.version for m in migs}
    if unknown:
        raise MigrationError(f"database has migrations unknown to this build: {sorted(unknown)} "
                             f"(database is newer than the code — refusing to start)")

    pending = [m for m in migs if m.version not in applied]
    if not pending:
        return []

    if backup and _has_user_data(db_file):
        engine.dispose()           # flush pooled connections before copying
        dest = backup_database(db_file, backups_dir, f"pre-v{pending[0].version:04d}")
        print(f"[migrate] backup written: {dest}", flush=True)

    done: list[int] = []
    for m in pending:
        print(f"[migrate] applying {m.version:04d}_{m.name} ...", flush=True)
        try:
            with engine.begin() as conn:
                if m.path.suffix == ".sql":
                    _run_sql(conn, m.path.read_text(encoding="utf-8"))
                else:
                    ns: dict = {}
                    exec(compile(m.path.read_text(encoding="utf-8"), str(m.path), "exec"), ns)  # noqa: S102
                    ns["upgrade"](conn)
                conn.execute(text(
                    "INSERT INTO schema_migrations(version, name, checksum, applied_at, notes) "
                    "VALUES (:v, :n, :c, CURRENT_TIMESTAMP, '')"),
                    {"v": m.version, "n": m.name, "c": m.checksum})
        except Exception as e:                           # transaction rolled back by engine.begin()
            raise MigrationError(f"migration {m.version:04d}_{m.name} failed: {e}") from e
        done.append(m.version)
    return done


def current_version(engine: Engine) -> int:
    applied = _applied(engine)
    return max(applied) if applied else 0
