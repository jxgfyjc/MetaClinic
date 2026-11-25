"""HTTP-level fixtures: a real FastAPI app on a temp DB (fake AI), TestClient with per-role logins."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_client(settings_env, monkeypatch):
    monkeypatch.setenv("XY_SEED_DEMO_USERS", "true")
    monkeypatch.setenv("XY_DEMO_PASSWORD", "Demo12345")
    monkeypatch.setenv("XY_COOKIE_SECURE", "false")
    from server import config
    config.reset_settings_cache()
    from server.app import create_app
    app = create_app()
    with TestClient(app, base_url="http://testserver") as c:
        yield c


class Roles:
    """Independent cookie jars per role so cross-role calls are tested truthfully."""

    def __init__(self, app_client):
        self.app = app_client.app
        self._c: dict[str, TestClient] = {}

    def as_(self, username: str) -> TestClient:
        if username not in self._c:
            c = TestClient(self.app, base_url="http://testserver")
            r = c.post("/api/v1/auth/login", json={"username": username, "password": "Demo12345"})
            assert r.status_code == 200, r.text
            self._c[username] = c
        return self._c[username]

    def anon(self) -> TestClient:
        return TestClient(self.app, base_url="http://testserver")


@pytest.fixture()
def roles(app_client):
    return Roles(app_client)


def ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:600]}"
    return r.json()


def err(r, status, code=None):
    assert r.status_code == status, f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:600]}"
    e = r.json()["error"]
    if code:
        assert e["code"] == code, e
    return e
