"""Generate the frozen baseline DDL from the current ORM metadata.

Run ONCE when cutting migration 0001:   python -m server.db.gen_baseline > server/db/migrations/0001_baseline.sql
After that the file is immutable; schema changes go into 0002_*.sql etc.
"""
from __future__ import annotations

import sys

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

import server.db.models  # noqa: F401  (register all tables)
from server.db.base import Base


def render() -> str:
    d = sqlite.dialect()
    parts = ["-- 0001_baseline: initial schema of the 象元 AI 诊疗 v3 backend (generated, then frozen).",
             "-- Statements are separated by `-- ;;` lines."]
    for t in Base.metadata.sorted_tables:
        if t.name == "schema_migrations":
            continue                                # created by the runner's bootstrap
        parts.append("-- ;;")
        parts.append(str(CreateTable(t).compile(dialect=d)).strip())
        for ix in sorted(t.indexes, key=lambda i: i.name or ""):
            parts.append("-- ;;")
            parts.append(str(CreateIndex(ix).compile(dialect=d)).strip())
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
