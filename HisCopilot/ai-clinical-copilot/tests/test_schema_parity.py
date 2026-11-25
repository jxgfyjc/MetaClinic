"""The migrations ARE the production schema; the ORM models are what the code reads/writes. They must never drift apart:
a column added to a model without a migration only fails at runtime ("no such column") on a real database."""
from __future__ import annotations

from sqlalchemy import inspect

import server.db.models  # noqa: F401
from server.db.base import Base


def test_every_orm_table_and_column_exists_after_migrations(db_engine):
    insp = inspect(db_engine)
    have = set(insp.get_table_names())
    problems = []
    for t in Base.metadata.sorted_tables:
        if t.name == "schema_migrations":
            continue
        if t.name not in have:
            problems.append(f"missing table {t.name}")
            continue
        cols = {c["name"] for c in insp.get_columns(t.name)}
        problems += [f"{t.name}.{c.name} missing in DB" for c in t.columns if c.name not in cols]
    assert not problems, "migrations and ORM models drifted:\n  " + "\n  ".join(problems)


def test_no_unmodelled_columns_in_database(db_engine):
    insp = inspect(db_engine)
    problems = []
    for t in Base.metadata.sorted_tables:
        if t.name == "schema_migrations":
            continue
        cols = {c["name"] for c in insp.get_columns(t.name)}
        problems += [f"{t.name}.{c} exists in DB but not in the model" for c in cols - {c.name for c in t.columns}]
    assert not problems, "\n  ".join(problems)
