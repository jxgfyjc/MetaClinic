"""Structural security tests: nothing in the API is reachable without the intended credential.

These enumerate the real route table, so a newly added endpoint that forgets its auth dependency fails here.
"""
from __future__ import annotations

import re

import pytest

PUBLIC = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
}


def _routes(app):
    """(METHOD, path_template) for every operation, via the OpenAPI schema (which flattens included routers —
    walking `app.routes` yields opaque _IncludedRouter objects on this FastAPI version)."""
    spec = app.openapi()
    out = []
    for path, ops in spec["paths"].items():
        for method in ops:
            if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append((method.upper(), path, None))
    return out


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_every_non_patient_route_rejects_anonymous_callers(app_client, roles):
    anon = roles.anon()
    checked = 0
    for method, path, route in _routes(app_client.app):
        if (method, path) in PUBLIC or path in ("/healthz",) or path.startswith("/api/v1/p/"):
            continue
        r = anon.request(method, _fill(path), json={} if method in ("POST", "PUT", "PATCH") else None)
        assert r.status_code in (401, 422), f"{method} {path} -> {r.status_code} (expected 401): {r.text[:120]}"
        if r.status_code == 422:      # validation ran BEFORE auth → body schema leaks existence; auth dependency must run first
            pytest.fail(f"{method} {path}: validation error before auth check")
        checked += 1
    assert checked > 90, f"only {checked} routes enumerated — route table suspiciously small"


def test_every_patient_route_requires_a_token_of_the_right_kind(app_client, roles):
    anon = roles.anon()
    for method, path, route in _routes(app_client.app):
        if not path.startswith("/api/v1/p/"):
            continue
        r = anon.request(method, _fill(path), json={} if method in ("POST", "PUT", "PATCH") else None)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}: {r.text[:120]}"


def test_staff_cookie_is_not_accepted_on_patient_routes_and_vice_versa(app_client, roles):
    doc = roles.as_("dr-001")
    for path in ("/api/v1/p/portal", "/api/v1/p/intake/draft", "/api/v1/p/intake/link"):
        assert doc.get(path).status_code == 401, path
    # a patient intake-link token is no staff credential
    link = doc.post("/api/v1/intake-links", json={}).json()["url"].split("#t=")[1]
    anon = roles.anon()
    assert anon.get("/api/v1/patients", headers={"X-XY-Token": link}).status_code == 401
    assert anon.get("/api/v1/patients", headers={"Authorization": f"Bearer {link}"}).status_code == 401


@pytest.mark.parametrize("role,forbidden", [
    ("nu-001", [("POST", "/api/v1/encounters/x/plan/generate"), ("POST", "/api/v1/prescriptions/x/sign"), ("POST", "/api/v1/records/x/finalize"),
                ("GET", "/api/v1/admin/audit"), ("GET", "/api/v1/admin/users"), ("POST", "/api/v1/pharmacy/prescriptions/x/dispense")]),
    ("ph-001", [("GET", "/api/v1/patients"), ("GET", "/api/v1/encounters"), ("GET", "/api/v1/records"), ("POST", "/api/v1/prescriptions/x/sign"),
                ("GET", "/api/v1/admin/audit"), ("POST", "/api/v1/encounters/x/analysis")]),
    ("admin", [("POST", "/api/v1/prescriptions/x/sign"), ("POST", "/api/v1/records/x/finalize"), ("POST", "/api/v1/diagnoses/x/review"),
               ("POST", "/api/v1/encounters/x/plan/generate"), ("POST", "/api/v1/pharmacy/prescriptions/x/dispense"), ("POST", "/api/v1/encounters/x/analysis")]),
    ("dr-001", [("GET", "/api/v1/admin/audit"), ("GET", "/api/v1/admin/users"), ("POST", "/api/v1/pharmacy/prescriptions/x/dispense"),
                ("GET", "/api/v1/pharmacy/queue"), ("POST", "/api/v1/catalog/drugs")]),
])
def test_role_cannot_call_other_roles_endpoints(app_client, roles, role, forbidden):
    c = roles.as_(role)
    for method, path in forbidden:
        r = c.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 403, f"{role} {method} {path} -> {r.status_code}: {r.text[:140]}"
        assert r.json()["error"]["code"] == "permission_denied"


def test_session_revocation_is_immediate(app_client, roles):
    admin, doc = roles.as_("admin"), roles.as_("dr-003")
    users = admin.get("/api/v1/admin/users").json()["items"]
    uid = next(u["id"] for u in users if u["username"] == "dr-003")
    assert doc.get("/api/v1/auth/me").status_code == 200
    assert admin.patch(f"/api/v1/admin/users/{uid}", json={"active": False}).status_code == 200
    assert doc.get("/api/v1/auth/me").status_code == 401                         # existing cookie dies at once, no waiting for expiry
    r = roles.anon().post("/api/v1/auth/login", json={"username": "dr-003", "password": "Demo12345"})
    assert r.status_code == 401
    me_id = next(u["id"] for u in users if u["username"] == "admin")
    assert admin.patch(f"/api/v1/admin/users/{me_id}", json={"active": False}).status_code == 409   # cannot lock yourself out


def test_login_lockout_over_http_and_generic_errors(app_client, roles):
    a = roles.anon()
    for _ in range(5):
        r = a.post("/api/v1/auth/login", json={"username": "dr-002", "password": "wrong"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials"
    r = a.post("/api/v1/auth/login", json={"username": "dr-002", "password": "Demo12345"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "account_locked"
    unknown = a.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    assert unknown.status_code == 401 and unknown.json()["error"]["message"] == "工号或密码错误"   # no user enumeration


def test_cookie_flags_and_security_headers(app_client):
    r = app_client.post("/api/v1/auth/login", json={"username": "dr-001", "password": "Demo12345"})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "xy_session=" in sc
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    assert r.headers["x-request-id"]


def test_error_shape_is_uniform_and_never_leaks_internals(app_client, roles):
    d = roles.as_("dr-001")
    for r in (d.get("/api/v1/encounters/does-not-exist"), d.post("/api/v1/patients", json={}), roles.anon().get("/api/v1/nope"), d.get("/api/v1/plans/zzz")):
        body = r.json()
        assert set(body) == {"error"} and {"code", "message", "request_id"} <= set(body["error"])
        assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()


def test_path_traversal_and_object_exposure_are_gone(app_client, roles):
    """The legacy API served `/objects//etc/hostname`. The new API has no path-based file route at all."""
    d = roles.as_("dr-001")
    for p in ("/objects//etc/hostname", "/objects/../../.env", "/api/v1/objects//etc/hostname", "/api/v1/artifacts/..%2f..%2f.env/file"):
        r = d.get(p)
        assert r.status_code in (401, 404, 405), (p, r.status_code)
        assert "JWT" not in r.text and "root:" not in r.text


def test_must_change_password_is_enforced_server_side(app_client, roles):
    admin = roles.as_("admin")
    r = admin.post("/api/v1/admin/users", json={"username": "dr-new", "display_name": "新医生", "role": "DOCTOR", "password": "Temp12345"})
    assert r.status_code == 201
    c = roles.anon()
    login = c.post("/api/v1/auth/login", json={"username": "dr-new", "password": "Temp12345"})
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    # everything except me/logout/change-password is refused — not merely hidden in the UI
    for path in ("/api/v1/patients", "/api/v1/encounters", "/api/v1/prescriptions", "/api/v1/records", "/api/v1/doctors"):
        rr = c.get(path)
        assert rr.status_code == 403 and rr.json()["error"]["code"] == "password_change_required", path
    assert c.get("/api/v1/auth/me").status_code == 200
    assert c.post("/api/v1/auth/change-password", json={"current_password": "Temp12345", "new_password": "short"}).status_code == 422
    assert c.post("/api/v1/auth/change-password", json={"current_password": "Temp12345", "new_password": "NewPass2026x"}).status_code == 200
    assert c.get("/api/v1/patients").status_code == 200
