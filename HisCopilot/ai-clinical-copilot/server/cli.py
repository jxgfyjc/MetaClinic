"""Operations CLI:  python -m server.cli <command>

  migrate                      apply pending schema migrations (backs up first when the DB has data)
  import-legacy [--dry-run]    one-way import of data/ai_clinical.db (read-only source; idempotent)
  create-user USERNAME ROLE    interactive-free account creation: --name N --password P [--title T] [--dept D]
  reset-password USERNAME      set a new password (forces change at next login)
  backup                       consistent online backup of the live database
  restore BACKUP_FILE          replace the live DB with a backup (STOP THE API FIRST)
  verify-audit                 verify the tamper-evident audit chain
  drain-jobs                   run queued jobs in this process (maintenance / debugging)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from server.config import get_settings
from server.db import migrate as mig
from server.db.base import init_db, session_scope


def _boot():
    s = get_settings()
    eng = init_db(s.db_path)
    return s, eng


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    p = sub.add_parser("import-legacy"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("create-user"); p.add_argument("username"); p.add_argument("role", choices=["ADMIN", "DOCTOR", "NURSE", "PHARMACIST"])
    p.add_argument("--name", required=True); p.add_argument("--password", required=True); p.add_argument("--title", default=""); p.add_argument("--dept", default="")
    p = sub.add_parser("reset-password"); p.add_argument("username"); p.add_argument("--password", required=True)
    sub.add_parser("backup")
    p = sub.add_parser("restore"); p.add_argument("file")
    sub.add_parser("verify-audit")
    sub.add_parser("drain-jobs")
    a = ap.parse_args(argv)

    if a.cmd == "restore":
        s = get_settings()
        mig.restore_backup(Path(a.file), s.db_path)
        print(f"restored {a.file} → {s.db_path}")
        return 0
    s, eng = _boot()
    if a.cmd == "migrate":
        print("applied:", mig.migrate(eng, s.db_path, s.backups_dir), "| version:", mig.current_version(eng))
    elif a.cmd == "import-legacy":
        from server.migrate_legacy import run_import
        print(json.dumps(run_import(dry_run=a.dry_run), ensure_ascii=False, indent=2))
    elif a.cmd == "backup":
        mig.migrate(eng, s.db_path, s.backups_dir, backup=False)
        print(mig.backup_database(s.db_path, s.backups_dir, "manual"))
    elif a.cmd == "create-user":
        from server.db.models import User
        from server.security.auth import hash_password, password_problem
        prob = password_problem(a.password)
        if prob:
            print("weak password:", prob); return 2
        with session_scope() as db:
            if db.scalar(select(User.id).where(User.username == a.username)):
                print("username exists"); return 2
            db.add(User(username=a.username, display_name=a.name, role=a.role, title=a.title, dept=a.dept,
                        password_hash=hash_password(a.password), must_change_password=True))
        print(f"created {a.role} {a.username} (must change password at first login)")
    elif a.cmd == "reset-password":
        from server.db.models import User
        from server.security.auth import hash_password, password_problem, revoke_all_sessions
        prob = password_problem(a.password)
        if prob:
            print("weak password:", prob); return 2
        with session_scope() as db:
            u = db.scalar(select(User).where(User.username == a.username))
            if not u:
                print("no such user"); return 2
            u.password_hash, u.must_change_password, u.failed_logins, u.locked_until, u.active = hash_password(a.password), True, 0, None, True
            db.flush(); revoke_all_sessions(db, u.id)
        print("password reset; must change at next login")
    elif a.cmd == "verify-audit":
        from server.security import audit
        with session_scope() as db:
            r = audit.verify_chain(db)
        print(json.dumps(r, ensure_ascii=False)); return 0 if r["ok"] else 1
    elif a.cmd == "drain-jobs":
        import server.jobs.handlers  # noqa: F401
        from server.jobs import engine
        print("ran", engine.drain(ignore_delay=True), "job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
