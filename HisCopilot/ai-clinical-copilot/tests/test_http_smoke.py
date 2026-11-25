from tests.conftest_http import ok, err


def test_boot_health_and_login(app_client, roles):
    assert app_client.get("/healthz").json()["status"] == "ok"
    me = ok(roles.as_("dr-001").get("/api/v1/auth/me"))["user"]
    assert me["role"] == "DOCTOR" and "rx.sign" in me["permissions"]
    err(roles.anon().get("/api/v1/auth/me"), 401, "unauthenticated")


def _intake_session(roles, doctor="dr-001"):
    link = ok(roles.as_(doctor).post("/api/v1/intake-links", json={"hours": 2}), 201)["url"].split("#t=")[1]
    anon = roles.anon()
    tok = ok(anon.post("/api/v1/p/intake/sessions", headers={"X-XY-Token": link}), 201)["token"]
    return anon, {"X-XY-Token": tok}


SUBMIT = {"name": "并发测试", "phone": "13700002222", "chief_complaint": "头痛三天", "allergies": ["青霉素"], "consent": True}


def test_concurrent_intake_submits_create_exactly_one_encounter_and_never_500(app_client, roles):
    """Regression from the browser double-tap: two simultaneous POST /p/intake/submit used to pick the same MRN → one got HTTP 500."""
    from concurrent.futures import ThreadPoolExecutor
    anon, h = _intake_session(roles)
    def go(_):
        return anon.post("/api/v1/p/intake/submit", json=SUBMIT, headers={**h, "Idempotency-Key": "same-click"})
    with ThreadPoolExecutor(6) as ex:
        rs = list(ex.map(go, range(6)))
    assert all(r.status_code in (200, 409) for r in rs), [(r.status_code, r.text[:120]) for r in rs]
    wins = [r for r in rs if r.status_code == 200]
    assert wins and len({r.json()["encounter_no"] for r in wins}) == 1                        # everyone who succeeded got the SAME visit
    assert sum(1 for r in wins if not r.json()["already_submitted"]) == 1                     # and exactly one actually created it
    assert all(r.json()["error"]["code"] == "submit_in_progress" for r in rs if r.status_code == 409)        # a loser only ever asks to retry
    q = ok(roles.as_("dr-001").get("/api/v1/encounters?scope=all&q=并发测试"))
    assert q["total"] == 1
    again = anon.post("/api/v1/p/intake/submit", json=SUBMIT, headers=h)                       # a later retry is a plain idempotent 200
    assert again.status_code == 200 and again.json()["already_submitted"] is True


def test_sequential_numbers_stay_unique_under_concurrent_creation(app_client, roles):
    """MRN / encounter numbers come from an atomic counter: 12 patients+encounters created in parallel never collide."""
    from concurrent.futures import ThreadPoolExecutor
    nu = roles.as_("nu-001")
    def one(i):
        p = nu.post("/api/v1/patients", json={"name": f"并行{i}", "phone": f"1390000{i:04d}"})
        assert p.status_code == 201, p.text
        e = nu.post("/api/v1/encounters", json={"patient_id": p.json()["patient"]["id"], "chief_complaint": "测试"}, headers={"Idempotency-Key": f"k{i}"})
        assert e.status_code in (200, 201), e.text
        return p.json()["patient"]["mrn"], e.json()["encounter"]["encounter_no"]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, range(12)))
    assert len({m for m, _ in res}) == 12 and len({e for _, e in res}) == 12


def test_unique_violation_that_slips_through_is_a_409_not_a_500(app_client):
    """Safety net: any UNIQUE race that reaches the top maps to 409 (refresh & retry); other integrity errors remain 500s."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from server.db.base import new_session
    app = app_client.app
    @app.get("/__race", include_in_schema=False)
    def race():
        s = new_session()
        s.execute(text("INSERT INTO number_counters(key,value) VALUES ('dup',1)")); s.execute(text("INSERT INTO number_counters(key,value) VALUES ('dup',2)"))
    r = TestClient(app, raise_server_exceptions=False).get("/__race")
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_appointments_accept_aware_and_naive_times_and_filter_by_clinic_day(app_client, roles):
    """Regression: an aware ("…Z") slot_start used to raise TypeError (aware < naive) → HTTP 500. The browser sends UTC ISO."""
    from datetime import datetime, timedelta, timezone
    nu = roles.as_("nu-001")
    doc_id = ok(nu.get("/api/v1/doctors"))["items"][0]["id"]
    pid = ok(nu.post("/api/v1/patients", json={"name": "预约测试", "phone": "13800001111"}), 201)["patient"]["id"]
    tomorrow = (datetime.now(timezone.utc) + timedelta(hours=8) + timedelta(days=1)).date()           # clinic-local (UTC+8) tomorrow
    start_utc = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 1, 0, tzinfo=timezone.utc)      # = 09:00 clinic time
    a = ok(nu.post("/api/v1/appointments", json={"patient_id": pid, "doctor_id": doc_id, "slot_start": start_utc.isoformat().replace("+00:00", "Z"), "duration_min": 20}), 201)
    assert a["slot_start"].startswith(f"{tomorrow.isoformat()}T01:00:00")                              # returned as the same UTC instant
    err(nu.post("/api/v1/appointments", json={"patient_id": pid, "doctor_id": doc_id, "slot_start": (start_utc + timedelta(minutes=10)).replace(tzinfo=None).isoformat()}), 409, "slot_taken")   # naive form accepted; overlap detected
    assert [x["id"] for x in ok(nu.get(f"/api/v1/appointments?day={tomorrow.isoformat()}"))["items"]] == [a["id"]]               # inside the clinic-day window
    assert ok(nu.get(f"/api/v1/appointments?day={(tomorrow + timedelta(days=1)).isoformat()}"))["items"] == []
    err(nu.post("/api/v1/appointments", json={"patient_id": pid, "doctor_id": doc_id, "slot_start": "2020-01-01T01:00:00Z"}), 422, "past_slot")


# ====================================================================== real parallel requests (found by the browser e2e)
def _par(fn, n):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(lambda _: fn(), range(n)))


def _visit(roles, name="并发医师", phone="13611112222"):
    d = roles.as_("dr-001")
    pid = ok(d.post("/api/v1/patients", json={"name": name, "phone": phone}), 201)["patient"]["id"]
    return d, pid, ok(d.post("/api/v1/encounters", json={"patient_id": pid, "chief_complaint": "头痛三天"}, headers={"Idempotency-Key": "seed-" + phone}), 201)["encounter"]


def test_parallel_writers_with_the_same_row_version_exactly_one_wins(app_client, roles):
    """Optimistic locking must be atomic: it used to be check-then-write, so N parallel PATCHes ALL succeeded (silent lost update)."""
    d, pid, enc = _visit(roles)
    v = ok(d.get(f"/api/v1/encounters/{enc['id']}"))["encounter"]["row_version"]
    rs = _par(lambda: d.patch(f"/api/v1/encounters/{enc['id']}/facts", json={"expected_version": v, "hpi": "并发写入"}), 6)
    codes = sorted(r.status_code for r in rs)
    assert codes == [200] + [409] * 5, codes
    assert all(r.json()["error"]["code"] == "stale_version" for r in rs if r.status_code == 409)


def test_parallel_analyze_clicks_start_exactly_one_analysis(app_client, roles):
    d, pid, enc = _visit(roles, "并发分析", "13611113333")
    before = len(ok(d.get(f"/api/v1/encounters/{enc['id']}/analysis/history"))["items"])
    rs = _par(lambda: d.post(f"/api/v1/encounters/{enc['id']}/analysis", json={}), 6)
    assert all(r.status_code == 202 for r in rs), [r.status_code for r in rs]
    assert len(ok(d.get(f"/api/v1/encounters/{enc['id']}/analysis/history"))["items"]) - before == 1
    assert len({r.json()["analysis_id"] for r in rs}) == 1


def test_parallel_create_encounter_with_one_idempotency_key_yields_one_visit(app_client, roles):
    d = roles.as_("dr-001")
    pid = ok(d.post("/api/v1/patients", json={"name": "并发建诊", "phone": "13611114444"}), 201)["patient"]["id"]
    rs = _par(lambda: d.post("/api/v1/encounters", json={"patient_id": pid, "chief_complaint": "测试"}, headers={"Idempotency-Key": "one-click"}), 8)
    assert all(r.status_code < 300 for r in rs), [(r.status_code, r.text[:100]) for r in rs]
    assert len({r.json()["encounter"]["id"] for r in rs}) == 1
    assert ok(d.get(f"/api/v1/encounters?scope=all&patient_id={pid}"))["total"] == 1


def test_parallel_create_encounter_without_a_key_still_dedupes_per_patient_and_doctor(app_client, roles):
    d = roles.as_("dr-001")
    pid = ok(d.post("/api/v1/patients", json={"name": "无键并发", "phone": "13611115555"}), 201)["patient"]["id"]
    rs = _par(lambda: d.post("/api/v1/encounters", json={"patient_id": pid, "chief_complaint": "测试"}), 8)
    assert all(r.status_code < 300 for r in rs)
    assert ok(d.get(f"/api/v1/encounters?scope=all&patient_id={pid}"))["total"] == 1


def test_idempotency_key_reused_with_a_different_body_is_refused(app_client, roles):
    d = roles.as_("dr-001")
    pid = ok(d.post("/api/v1/patients", json={"name": "键复用", "phone": "13611116666"}), 201)["patient"]["id"]
    ok(d.post("/api/v1/encounters", json={"patient_id": pid, "chief_complaint": "A"}, headers={"Idempotency-Key": "k-reuse"}), 201)
    err(d.post("/api/v1/encounters", json={"patient_id": pid, "chief_complaint": "B"}, headers={"Idempotency-Key": "k-reuse"}), 409, "idempotency_key_reuse")


def test_ping_and_healthz_expose_the_same_instance_id_without_auth(app_client):
    """Ops uses this to prove the web proxy talks to THIS api (a prod build once silently pointed at another backend)."""
    h = app_client.get("/healthz").json(); p = app_client.get("/api/v1/ping").json()
    assert h["status"] == "ok" and p["ok"] is True and h["instance"] == p["instance"] and len(p["instance"]) >= 8
