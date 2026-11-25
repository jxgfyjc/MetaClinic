"""Shared pytest fixtures: isolated temp DB (built via the real migrations), settings override, factories."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def settings_env(tmp_path, monkeypatch):
    """Point the whole backend at a throw-away data dir; AI in fake mode; no demo seeding."""
    monkeypatch.setenv("XY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XY_AI_MODE", "fake")
    monkeypatch.setenv("XY_JOBS_ENABLED", "false")
    monkeypatch.setenv("XY_SEED_DEMO_USERS", "false")
    monkeypatch.setenv("APP_ENV", "test")
    from server import config
    config.reset_settings_cache()
    yield config.get_settings()
    config.reset_settings_cache()


@pytest.fixture()
def db_engine(settings_env):
    from server.db import migrate
    from server.db.base import init_db
    s = settings_env
    eng = init_db(s.db_path)
    migrate.migrate(eng, s.db_path, s.backups_dir, backup=False)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(db_engine):
    from server.db.base import new_session
    s = new_session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def make_user(db):
    from server.security.auth import hash_password
    from server.db.models import User

    def _make(username="dr-t1", role="DOCTOR", password="Passw0rd!x", name=None, **kw):
        u = User(username=username, display_name=name or username, role=role,
                 password_hash=hash_password(password), **kw)
        db.add(u)
        db.commit()
        return u
    return _make


# ---- HTTP fixtures (kept in a separate module for readability) ----
from tests.conftest_http import app_client, roles, ok, err  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    from server.security import ratelimit
    ratelimit.reset()
    yield
    ratelimit.reset()
