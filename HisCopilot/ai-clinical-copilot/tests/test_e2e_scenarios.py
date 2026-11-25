"""End-to-end business scenarios over real HTTP (fake model backend, real everything else).

S1  patient intake → persisted → async pre-analysis → doctor opens and sees saved result (no re-run)
S2  review several diagnoses → ONE unified plan → prescriptions (separate objects) → sign → finalise record
S3  record + prescriptions survive "restart" (new app on the same DB), PDFs render
S4  authorisation matrix across roles / patients
S5  AI/ASR/OCR failure states are visible and recoverable
S6  idempotency / double-clicks / concurrency
"""
from __future__ import annotations

import io
import struct
import wave

import pytest
from sqlalchemy import func, select

from server.db.base import session_scope
from server.db.models import (Analysis, Artifact, Diagnosis, Encounter, Job, LabItem, MedicalRecord, Patient, Prescription, TreatmentPlan)
from server.jobs import engine as jobs
from tests.conftest_http import err, ok

PDF_MAGIC = b"%PDF-"


def wav_bytes(seconds=1.5, tag=b""):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * ((i // 40) % 2 * 2 - 1))) for i in range(int(16000 * seconds))))
    return buf.getvalue() + tag


def png_bytes(color=(200, 50, 50), size=(320, 240)):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "PNG"); return b.getvalue()


def drain():
    return jobs.drain(max_jobs=60, ignore_delay=True)


# ---------------------------------------------------------------------------------- helpers
def make_link(roles, doctor="dr-001"):
    d = roles.as_(doctor)
    return ok(d.post("/api/v1/intake-links", json={"hours": 2}), 201)["url"].split("#t=")[1]


def intake_client(roles, link_token):
    c = roles.anon()
    r = ok(c.post("/api/v1/p/intake/sessions", headers={"X-XY-Token": link_token}), 201)
    c.headers["X-XY-Token"] = r["token"]
    return c


SUBMIT = dict(name="周新患", gender="男", age=47, phone="13912345678", insurance="自费", allergies=["青霉素"], no_allergies_confirmed=False,
              chronic=["高血压"], chief_complaint="头痛3天，伴头晕", hpi="3天前开始，晨起明显，伴乏力口干", symptoms=["头晕", "乏力"], consent=True,
              current_meds=[{"name": "氨氯地平", "dose": "5mg", "frequency": "qd"}])


# ================================================================================== S1
def test_s1_patient_intake_to_doctor_sees_saved_analysis(app_client, roles):
    link = make_link(roles, "dr-001")
    p = intake_client(roles, link)
    # draft autosave survives a "page reload"
    ok(p.put("/api/v1/p/intake/draft", json={"draft": {"name": "周新患", "chief_complaint": "头痛", "step": 2, "evil": "x"}}))
    assert ok(p.get("/api/v1/p/intake/draft"))["draft"] == {"name": "周新患", "chief_complaint": "头痛", "step": 2}       # unknown keys dropped
    # voice + lab report + tongue photo
    a = ok(p.post("/api/v1/p/intake/files", data={"kind": "audio", "purpose": "hpi"}, files={"file": ("rec.wav", wav_bytes(), "audio/wav")}), 201)["file"]
    lab = ok(p.post("/api/v1/p/intake/files", data={"kind": "lab_image"}, files={"file": ("lab.png", png_bytes(), "image/png")}), 201)["file"]
    tg = ok(p.post("/api/v1/p/intake/files", data={"kind": "tongue_image"}, files={"file": ("t.png", png_bytes((220, 120, 120)), "image/png")}), 201)["file"]
    assert a["proc_status"] == "pending" and tg["proc_status"] == "not_required"
    drain()
    st = ok(p.get("/api/v1/p/intake/draft"))["files"]
    voice = next(f for f in st if f["kind"] == "audio")
    assert voice["proc_status"] == "succeeded" and "头痛" in voice["text"]
    assert next(f for f in st if f["kind"] == "lab_image")["lab_items_found"] == 4
    # patient may correct THEIR OWN transcript
    ok(p.patch(f"/api/v1/p/intake/files/{voice['id']}/text", json={"text": "我头痛三天，晨起明显，还有点头晕。"}))
    # submit twice (double click) → one encounter
    h = {"Idempotency-Key": "k-1"}
    r1 = ok(p.post("/api/v1/p/intake/submit", json=SUBMIT, headers=h))
    r2 = ok(p.post("/api/v1/p/intake/submit", json=SUBMIT, headers=h))
    assert r1["encounter_no"] == r2["encounter_no"] and r2["already_submitted"] and not r1["already_submitted"]
    with session_scope() as s:
        assert s.scalar(select(func.count(Encounter.id))) == 1 and s.scalar(select(func.count(Patient.id))) == 1
        e = s.scalar(select(Encounter))
        pt = s.get(Patient, e.patient_id)
        # exact facts the patient typed are what is persisted, linked by stable ids
        assert (pt.name, pt.gender, pt.age_years, pt.phone) == ("周新患", "男", 47, "13912345678")
        assert e.chief_complaint == "头痛3天，伴头晕" and e.doctor_id is not None and e.source == "patient_intake"
        assert e.patient_reported_json["chief_complaint"] == "头痛3天，伴头晕"                                        # immutable original snapshot
        assert {x.allergen for x in pt.allergies} == {"青霉素"}
        assert s.scalar(select(func.count(Artifact.id)).where(Artifact.encounter_id == e.id)) == 3                 # every upload attached
        assert s.scalar(select(func.count(LabItem.id)).where(LabItem.encounter_id == e.id)) == 4
        eid = e.id
    drain()                                                                                                      # async auto pre-analysis
    d = roles.as_("dr-001")
    b = ok(d.get(f"/api/v1/encounters/{eid}"))
    assert b["patient"]["name"] == "周新患" and b["encounter"]["chief_complaint"] == "头痛3天，伴头晕"
    assert b["analysis"]["status"] == "succeeded" and not b["staleness"]["analysis_stale"]
    assert {m["module"]: m["status"] for m in b["analysis"]["modules"]} == {"tongue": "succeeded", "western": "succeeded", "tcm": "succeeded", "synthesis": "succeeded"}
    assert any(dx["status"] == "pending" for dx in b["diagnoses"])
    # opening the visit did NOT trigger another model run; asking again reuses the saved result
    before = _count(Analysis)
    again = ok(d.post(f"/api/v1/encounters/{eid}/analysis", json={}), 202)
    assert again["reused"] and again["analysis_id"] == b["analysis"]["id"] and _count(Analysis) == before
    # doctor changes a clinical fact → saved analysis becomes visibly stale, and re-run is now allowed
    ok(d.patch(f"/api/v1/encounters/{eid}/facts", json={"hpi": b["encounter"]["hpi"] + "；夜间加重", "expected_version": b["encounter"]["row_version"]}))
    assert ok(d.get(f"/api/v1/encounters/{eid}"))["staleness"]["analysis_stale"] is True
    redo = ok(d.post(f"/api/v1/encounters/{eid}/analysis", json={}), 202)
    assert redo["reused"] is False and _count(Analysis) == before + 1
    drain()
    assert ok(d.get(f"/api/v1/encounters/{eid}"))["staleness"]["analysis_stale"] is False


def _count(model):
    with session_scope() as s:
        return s.scalar(select(func.count(model.id)))


# ================================================================================== S2 + S3
def run_to_final(roles, eid, doc="dr-001"):
    d = roles.as_(doc)
    b = ok(d.get(f"/api/v1/encounters/{eid}"))
    # accept two AI candidates (one western, one TCM), reject one, add own diagnosis
    pend = [x for x in b["diagnoses"] if x["status"] == "pending"]
    west = next(x for x in pend if x["module"] == "western"); tcm = next(x for x in pend if x["module"] == "tcm")
    ok(d.post(f"/api/v1/diagnoses/{west['id']}/review", json={"action": "accept", "primary": True}))
    ok(d.post(f"/api/v1/diagnoses/{tcm['id']}/review", json={"action": "modify", "name": "肝阳上亢兼痰湿证", "expected_version": tcm["row_version"]}))
    for x in pend:
        if x["id"] not in (west["id"], tcm["id"]):
            ok(d.post(f"/api/v1/diagnoses/{x['id']}/review", json={"action": "reject", "note": "与病史不符"}))
    mine = ok(d.post(f"/api/v1/encounters/{eid}/diagnoses", json={"module": "western", "name": "颈椎病（待排）", "note": "医师追加"}), 201)["diagnosis"]
    return west, tcm, mine


def test_s2_s3_diagnoses_to_unified_plan_prescriptions_record_and_persistence(settings_env, monkeypatch):
    from tests.conftest_http import Roles
    from fastapi.testclient import TestClient
    monkeypatch.setenv("XY_SEED_DEMO_USERS", "true"); monkeypatch.setenv("XY_DEMO_PASSWORD", "Demo12345"); monkeypatch.setenv("XY_COOKIE_SECURE", "false")
    from server import config; config.reset_settings_cache()
    from server.app import create_app

    with TestClient(create_app(), base_url="http://testserver") as c1:
        roles = Roles(c1)
        eid = _intake_and_analyse(roles)
        d = roles.as_("dr-001")
        west, tcm, mine = run_to_final(roles, eid)
        b = ok(d.get(f"/api/v1/encounters/{eid}"))
        finals = [x for x in b["diagnoses"] if x["status"] in ("accepted", "modified")]
        assert len(finals) == 3 and {x["name"] for x in finals} >= {"肝阳上亢兼痰湿证", "颈椎病（待排）"}
        assert next(x for x in finals if x["module"] == "tcm")["original_name"] == "肝阳上亢证"             # AI original preserved on modify

        # ---- ONE unified plan for all three diagnoses (not one per diagnosis)
        g = ok(d.post(f"/api/v1/encounters/{eid}/plan/generate", json={"sections": ["medication", "herbal", "therapy"]}), 202)
        again = ok(d.post(f"/api/v1/encounters/{eid}/plan/generate", json={"sections": ["medication", "herbal", "therapy"]}), 202)
        assert again["plan_id"] == g["plan_id"] and again["reused"]                                          # double click → same plan
        drain()
        plan = ok(d.get(f"/api/v1/plans/{g['plan_id']}"))["plan"]
        assert plan["status"] == "draft" and len(plan["dx_snapshot"]) == 3 and _count(TreatmentPlan) == 1
        assert plan["content"]["herbal"] and plan["therapy_items"][0]["status"] == "pending"
        # cannot confirm while therapy items are unreviewed
        err(d.post(f"/api/v1/plans/{plan['id']}/confirm", json={}), 409, "therapy_pending")
        t = plan["therapy_items"][0]
        ok(d.post(f"/api/v1/therapy-items/{t['id']}/review", json={"action": "modify", "edits": {"acupoints": ["太冲", "风池", "百会", "合谷"], "detail": {"frequency": "每周3次"}}}))
        plan = ok(d.post(f"/api/v1/plans/{plan['id']}/confirm", json={"expected_version": ok(d.get(f"/api/v1/plans/{plan['id']}"))["plan"]["row_version"]}))["plan"]
        assert plan["status"] == "confirmed" and plan["therapy_items"][0]["original"]["acupoints"] == ["太冲", "风池", "百会"]   # edit kept next to original

        # ---- prescriptions are SEPARATE objects created from the plan (several, linked, editable)
        rx = ok(d.post(f"/api/v1/encounters/{eid}/prescriptions/from-plan", json={"plan_id": plan["id"]}, headers={"Idempotency-Key": "fp-1"}), 201)
        rx_again = ok(d.post(f"/api/v1/encounters/{eid}/prescriptions/from-plan", json={"plan_id": plan["id"]}, headers={"Idempotency-Key": "fp-1"}), 201)
        assert [r["id"] for r in rx["prescriptions"]] == [r["id"] for r in rx_again["prescriptions"]] and _count(Prescription) == 2
        types = {r["rx_type"]: r for r in rx["prescriptions"]}
        assert set(types) == {"western", "herbal"} and all(r["plan_id"] == plan["id"] for r in rx["prescriptions"])
        # not yet from the plan again via a different key → skipped, no duplicates
        skip = ok(d.post(f"/api/v1/encounters/{eid}/prescriptions/from-plan", json={"plan_id": plan["id"]}, headers={"Idempotency-Key": "fp-2"}), 201)
        assert skip["prescriptions"] == [] or len(skip["prescriptions"]) == 0
        assert _count(Prescription) == 2
        # doctor edits the herbal Rx (structured items) with optimistic locking
        h = types["herbal"]
        items = h["items"] + [{"name": "山药", "dose_g": 15}]
        e1 = ok(d.patch(f"/api/v1/prescriptions/{h['id']}", json={"items": items, "expected_version": h["row_version"]}))["prescription"]
        err(d.patch(f"/api/v1/prescriptions/{h['id']}", json={"items": items, "expected_version": h["row_version"]}), 409, "stale_version")
        # ---- finalisation blocked until prescriptions are signed
        rec = ok(d.post(f"/api/v1/encounters/{eid}/record"))["record"]
        e = err(d.post(f"/api/v1/records/{rec['id']}/finalize", json={}), 409, "finalize_blocked")
        assert any(b["code"] == "rx_unsigned" for b in e["details"]["blockers"])
        # sign (western has an unacknowledged allergy-free but warning-free path; herbal may carry warnings)
        for r in (types["western"], ok(d.get(f"/api/v1/prescriptions/{h['id']}"))["prescription"]):
            _sign_with_acks(d, r)
        # simulated HIS is labelled simulated
        his = ok(d.post(f"/api/v1/prescriptions/{h['id']}/submit-his"))
        assert his["simulated"] and his["prescription"]["his"]["simulated"] and "未接入真实 HIS" in his["notice"]
        # doctor-written record text + auto sections, then finalise
        cur = ok(d.get(f"/api/v1/records/{rec['id']}"))["record"]
        ok(d.put(f"/api/v1/records/{rec['id']}/draft", json={"sections": {"physical_exam": "BP 148/92mmHg，神清，颈软。"}, "base_version_no": cur["version"]["version_no"]}))
        fin = ok(d.post(f"/api/v1/records/{rec['id']}/finalize", json={}))["record"]
        assert fin["status"] == "finalized" and fin["version"]["kind"] == "final"
        assert ok(d.get(f"/api/v1/encounters/{eid}"))["encounter"]["status"] == "COMPLETED"
        ids = dict(rec=rec["id"], rx=[types["western"]["id"], h["id"]], eid=eid, final_hash=fin["version"]["content_hash"])

    # ---- S3: "service restart": a brand-new app instance on the SAME database file
    config.reset_settings_cache()
    with TestClient(create_app(), base_url="http://testserver") as c2:
        roles2 = Roles(c2)
        d2 = roles2.as_("dr-001")
        got = ok(d2.get(f"/api/v1/records/{ids['rec']}"))
        assert got["record"]["status"] == "finalized" and got["integrity_ok"] and got["record"]["version"]["content_hash"] == ids["final_hash"]
        secs = got["record"]["version"]["sections"]
        assert "BP 148/92" in secs["physical_exam"]["text"] and "颈椎病" in secs["diagnoses"]["text"]
        assert "天麻" in secs["treatment"]["text"] and "氨氯地平" in secs["treatment"]["text"] and "RX" in secs["treatment"]["text"]   # signed Rx reflected in the frozen record
        assert got["snapshot"]["patient"]["name"] == "周新患" and len(got["snapshot"]["prescriptions"]) == 2
        for rid in ids["rx"]:
            assert ok(d2.get(f"/api/v1/prescriptions/{rid}"))["prescription"]["status"] in ("signed", "pharmacy_review", "approved")
        # search / reopen by number
        listing = ok(d2.get("/api/v1/records?q=周新患"))["items"]
        assert listing and listing[0]["status"] == "finalized" and listing[0]["diagnoses"]
        # PDFs: real, non-trivial, not-a-draft
        rp = d2.get(f"/api/v1/records/{ids['rec']}/pdf"); assert rp.status_code == 200 and rp.content.startswith(PDF_MAGIC) and len(rp.content) > 5000
        for rid in ids["rx"]:
            xp = d2.get(f"/api/v1/prescriptions/{rid}/pdf"); assert xp.status_code == 200 and xp.content.startswith(PDF_MAGIC) and len(xp.content) > 3000
        # finalized record cannot be silently edited; amendment needs a reason and keeps the old version
        v_before = got["record"]["version"]["version_no"]
        err(d2.put(f"/api/v1/records/{ids['rec']}/draft", json={"sections": {"hpi": "偷改"}}), 409, "record_finalized")
        err(d2.post(f"/api/v1/records/{ids['rec']}/amend", json={"sections": {"hpi": "x"}, "reason": "短"}), 422)
        am = ok(d2.post(f"/api/v1/records/{ids['rec']}/amend", json={"sections": {"hpi": "更正：发病时间为2天前"}, "reason": "患者补充发病时间有误"}))["record"]
        assert am["version"]["kind"] == "amendment" and am["version"]["version_no"] == v_before + 1
        hist = ok(d2.get(f"/api/v1/records/{ids['rec']}"))["record"]["versions"]
        assert [v["kind"] for v in hist][-1] == "draft" or {"final", "amendment"} <= {v["kind"] for v in hist}
        old = ok(d2.get(f"/api/v1/records/{ids['rec']}?version_id={got['record']['version']['id']}"))
        assert "发病时间为2天前" not in old["record"]["version"]["sections"]["hpi"]["text"]                    # v-final untouched


def _sign_with_acks(d, rx):
    r = d.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={"expected_version": rx["row_version"]})
    if r.status_code == 200:
        return r.json()["prescription"]
    e = r.json()["error"]
    assert e["code"] == "safety_unresolved", e
    acks = {b["key"]: ("经核对，临床获益大于风险，已告知患者并监测" if b["need"] == "reason" else "ack") for b in e["details"]["blockers"] if b["need"] in ("reason", "ack")}
    assert not [b for b in e["details"]["blockers"] if b["need"] == "fix"], e
    ok(d.post(f"/api/v1/prescriptions/{rx['id']}/ack", json={"acks": acks}))
    cur = ok(d.get(f"/api/v1/prescriptions/{rx['id']}"))["prescription"]
    return ok(d.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={"expected_version": cur["row_version"]}))["prescription"]


def _intake_and_analyse(roles, with_file=False, **override):
    link = make_link(roles, "dr-001")
    p = intake_client(roles, link)
    if with_file:
        ok(p.post("/api/v1/p/intake/files", data={"kind": "tongue_image"}, files={"file": ("t.png", png_bytes((210, 110, 110)), "image/png")}), 201)
    ok(p.post("/api/v1/p/intake/submit", json={**SUBMIT, **override}))
    drain()
    with session_scope() as s:
        return s.scalar(select(Encounter.id).order_by(Encounter.created_at.desc()))


# ================================================================================== S4  authorisation
def test_s4_authorisation_matrix(app_client, roles):
    eid = _intake_and_analyse(roles, with_file=True)
    anon = roles.anon()
    doc1, doc2, nurse, ph, admin = (roles.as_(u) for u in ("dr-001", "dr-002", "nu-001", "ph-001", "admin"))
    # --- no login → 401 everywhere in the staff API
    for method, path in (("get", "/api/v1/patients"), ("get", f"/api/v1/encounters/{eid}"), ("get", "/api/v1/prescriptions"), ("get", "/api/v1/records"),
                         ("get", "/api/v1/admin/audit"), ("get", "/api/v1/catalog/drugs?q=a"), ("post", f"/api/v1/encounters/{eid}/analysis")):
        err(getattr(anon, method)(path), 401)
    # --- patient token cannot reach staff API; staff cookie cannot act as patient
    link = make_link(roles); p = intake_client(roles, link)
    for path in ("/api/v1/patients", f"/api/v1/encounters/{eid}", "/api/v1/admin/audit"):
        err(p.get(path), 401)                                                                              # X-XY-Token is NOT a staff credential
    err(doc1.get("/api/v1/p/intake/draft"), 401)                                                           # staff session is not a patient session
    # --- doctor ownership: dr-002 cannot read/modify dr-001's encounter (but can see unassigned pool)
    err(doc2.get(f"/api/v1/encounters/{eid}"), 403, "not_your_encounter")
    err(doc2.patch(f"/api/v1/encounters/{eid}/facts", json={"hpi": "x"}), 403)
    err(doc2.post(f"/api/v1/encounters/{eid}/analysis", json={}), 403)
    assert eid not in [e["id"] for e in ok(doc2.get("/api/v1/encounters?scope=all"))["items"]]
    # --- nurse: can read + record facts, cannot diagnose / plan / sign / finalise
    ok(nurse.get(f"/api/v1/encounters/{eid}"))
    pend = next(x for x in ok(doc1.get(f"/api/v1/encounters/{eid}"))["diagnoses"] if x["status"] == "pending")
    err(nurse.post(f"/api/v1/diagnoses/{pend['id']}/review", json={"action": "accept"}), 403, "permission_denied")
    err(nurse.post(f"/api/v1/encounters/{eid}/plan/generate", json={}), 403)
    err(nurse.post(f"/api/v1/encounters/{eid}/analysis", json={}), 403)
    # --- admin: read-only on clinical data, cannot sign/review/finalise
    ok(admin.get("/api/v1/admin/audit")); err(admin.post(f"/api/v1/diagnoses/{pend['id']}/review", json={"action": "accept"}), 403)
    err(doc1.get("/api/v1/admin/audit"), 403); err(doc1.get("/api/v1/admin/users"), 403); err(nurse.get("/api/v1/pharmacy/queue"), 403)
    # --- pharmacist: sees signed prescriptions only; no encounters/patients/records
    err(ph.get(f"/api/v1/encounters/{eid}"), 403); err(ph.get("/api/v1/patients"), 403); err(ph.get("/api/v1/records"), 403)
    ok(ph.get("/api/v1/pharmacy/queue"))
    # draft prescription invisible to the pharmacist, and not even by direct id
    west, tcm, mine = run_to_final(roles, eid)
    plan_id = ok(doc1.post(f"/api/v1/encounters/{eid}/plan/generate", json={}), 202)["plan_id"]; drain()
    rx = ok(doc1.post(f"/api/v1/encounters/{eid}/prescriptions", json={"rx_type": "western", "items": [{"name": "二甲双胍片", "dose_text": "0.5g", "frequency": "bid", "route": "口服", "days": 14}]}), 201)["prescription"]
    err(ph.get(f"/api/v1/prescriptions/{rx['id']}"), 403, "rx_not_signed"); err(ph.get(f"/api/v1/prescriptions/{rx['id']}/pdf"), 403)
    assert rx["id"] not in [r["id"] for r in ok(ph.get("/api/v1/prescriptions"))["items"]]
    # only the prescriber can sign/edit; others cannot, even another doctor
    err(doc2.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={}), 403); err(nurse.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={}), 403)
    err(admin.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={}), 403)
    # pharmacy cannot act on unsigned rx; cannot sign
    err(ph.post(f"/api/v1/pharmacy/prescriptions/{rx['id']}/start"), 409, "bad_transition"); err(ph.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={}), 403)
    # signed → pharmacy sees, can review/dispense; doctor can no longer edit
    signed = _sign_with_acks(doc1, rx)
    ok(ph.get(f"/api/v1/prescriptions/{rx['id']}"))
    err(doc1.patch(f"/api/v1/prescriptions/{rx['id']}", json={"items": []}), 409, "rx_not_editable")
    ok(ph.post(f"/api/v1/pharmacy/prescriptions/{rx['id']}/start")); ok(ph.post(f"/api/v1/pharmacy/prescriptions/{rx['id']}/decide", json={"approve": True}))
    assert ok(ph.post(f"/api/v1/pharmacy/prescriptions/{rx['id']}/dispense"))["prescription"]["status"] == "dispensed"
    err(doc1.post(f"/api/v1/pharmacy/prescriptions/{rx['id']}/dispense"), 403, "permission_denied")        # prescriber cannot dispense own Rx
    err(doc1.post(f"/api/v1/prescriptions/{rx['id']}/void", json={"reason": "撤销"}), 409, "bad_transition")  # dispensed is final
    # --- denied attempts were audited
    log = ok(admin.get("/api/v1/admin/audit?outcome=denied&limit=200"))["items"]
    assert any(a["action"] == "access.denied" and a["actor"] for a in log)
    # --- object access goes through authenticated endpoints only
    with session_scope() as s:
        aid = s.scalar(select(Artifact.id).where(Artifact.encounter_id == eid))
    assert aid is not None
    err(anon.get(f"/api/v1/artifacts/{aid}/file"), 401)
    err(doc2.get(f"/api/v1/artifacts/{aid}/file"), 403, "not_your_encounter")
    assert doc1.get(f"/api/v1/artifacts/{aid}/file").status_code == 200
    err(ph.get(f"/api/v1/artifacts/{aid}/file"), 403)
    # --- audit chain intact after all of this
    assert ok(admin.get("/api/v1/admin/audit/verify"))["ok"] is True


def test_patient_portal_isolation_and_minimal_surface(app_client, roles):
    eid = _intake_and_analyse(roles)
    p = roles.anon()
    # intake session tokens cannot be used as portal tokens (kind-scoped), and vice versa
    link = make_link(roles); ps = intake_client(roles, link)
    err(ps.get("/api/v1/p/portal"), 401)
    # nothing under /p returns clinical fields
    body = ps.get("/api/v1/p/intake/draft").text.lower()
    for secret in ("diagnos", "prescription", "analysis", "doctor_note", "treatment", "therapy", "safety"):
        assert secret not in body, secret
    # a portal token is bound to ONE patient: its tasks only
    d = roles.as_("dr-001")
    west, tcm, mine = run_to_final(roles, eid)
    ok(d.post(f"/api/v1/encounters/{eid}/plan/generate", json={}), 202); drain()
    fp = ok(d.post(f"/api/v1/encounters/{eid}/followup-plan", json={}), 201)["plan"]
    fp = ok(d.post(f"/api/v1/followup-plans/{fp['id']}/activate", json={}))["plan"]
    tid = fp["tasks"][0]["id"]
    url = ok(d.post(f"/api/v1/followup-tasks/{tid}/portal-link"), 201)["url"]; tok = url.split("#t=")[1]
    portal = roles.anon(); portal.headers["X-XY-Token"] = tok
    home = ok(portal.get("/api/v1/p/portal"))
    assert set(home) == {"clinic", "patient", "tasks", "messages", "care_summaries"} and home["care_summaries"] == []        # nothing released yet → no clinical content
    # second patient + their task: not reachable with the first patient's token
    eid2 = _intake_and_analyse(roles, name="另一位患者", phone="13800009999")
    fp2 = ok(d.post(f"/api/v1/encounters/{eid2}/followup-plan", json={}), 201)["plan"]
    fp2 = ok(d.post(f"/api/v1/followup-plans/{fp2['id']}/activate", json={}))["plan"]
    err(portal.post(f"/api/v1/p/portal/tasks/{fp2['tasks'][0]['id']}/respond", json={"free_text": "x"}), 404, "task_not_found")


# ================================================================================== S5  failures are visible & recoverable
def test_s5_asr_ocr_ai_failures_are_explicit_and_recoverable(app_client, roles):
    link = make_link(roles); p = intake_client(roles, link)
    bad = ok(p.post("/api/v1/p/intake/files", data={"kind": "audio"}, files={"file": ("x.wav", wav_bytes(1.2, b"FAKE_ASR_FAIL"), "audio/wav")}), 201)["file"]
    silent = ok(p.post("/api/v1/p/intake/files", data={"kind": "audio"}, files={"file": ("y.wav", wav_bytes(1.3), "audio/wav")}), 201)["file"]
    # file-type / size validation: content decides, not the filename
    e = err(p.post("/api/v1/p/intake/files", data={"kind": "lab_image"}, files={"file": ("virus.png", b"MZ\x90\x00not an image at all......", "image/png")}), 422); assert e["code"] == "not_an_image"
    err(p.post("/api/v1/p/intake/files", data={"kind": "audio"}, files={"file": ("a.wav", b"", "audio/wav")}), 422, "empty_file")
    err(p.post("/api/v1/p/intake/files", data={"kind": "audio"}, files={"file": ("t.wav", wav_bytes(0.1), "audio/wav")}), 422, "audio_too_short")
    # same file again → not duplicated, not re-queued
    dup = ok(p.post("/api/v1/p/intake/files", data={"kind": "audio"}, files={"file": ("x2.wav", wav_bytes(1.3), "audio/wav")}), 201); assert dup["created"] is False
    for _ in range(4):                                                                                      # let retries exhaust
        drain()
        with session_scope() as s:
            for j in s.scalars(select(Job).where(Job.status == "queued")):
                from server.domain.ids import utcnow; j.run_after = utcnow()
    files = {f["id"]: f for f in ok(p.get("/api/v1/p/intake/draft"))["files"]}
    assert files[bad["id"]]["proc_status"] == "failed" and files[bad["id"]]["error_code"].startswith("asr_") and files[bad["id"]]["error_detail"]
    assert files[bad["id"]]["text"] is None                                                                  # no invented transcript
    # recovery: the patient can retry the failed file; submission is NOT blocked by it
    from server.domain.artifacts import path_of
    with session_scope() as s:                       # "the ASR service came back": the same stored recording is now transcribable
        a = s.get(Artifact, bad["id"]); path_of(a).write_bytes(path_of(a).read_bytes().replace(b"FAKE_ASR_FAIL", b""))
    r = ok(p.post(f"/api/v1/p/intake/files/{bad['id']}/retry"))
    assert r["file"]["proc_status"] == "pending"
    drain()
    recovered = next(f for f in ok(p.get("/api/v1/p/intake/draft"))["files"] if f["id"] == bad["id"])
    assert recovered["proc_status"] == "succeeded" and "头痛" in recovered["text"] and recovered["error_code"] is None    # same file, same id, no duplicate
    # a second recording keeps failing (ASR down) and the patient submits anyway: submission is never blocked by a model outage
    ok(p.post("/api/v1/p/intake/files", data={"kind": "audio"}, files={"file": ("z.wav", wav_bytes(1.7, b"FAKE_ASR_FAIL"), "audio/wav")}), 201)
    for _ in range(4):
        drain()
    ok(p.post("/api/v1/p/intake/submit", json=SUBMIT))
    # doctor side: the failed recording is listed WITH reason + retry path; nothing pretends it was transcribed
    d = roles.as_("dr-001")
    with session_scope() as s:
        eid = s.scalar(select(Encounter.id))
    b = ok(d.get(f"/api/v1/encounters/{eid}"))
    failed = [a for a in b["artifacts"] if a["proc_status"] == "failed"]
    assert len(failed) == 1 and failed[0]["error_code"].startswith("asr_") and failed[0]["error_detail"] and failed[0]["result_text"] is None
    retried = ok(d.post(f"/api/v1/artifacts/{failed[0]['id']}/retry"))
    assert retried["artifact"]["proc_status"] == "pending" and retried["job_id"]
    err(d.post(f"/api/v1/artifacts/{failed[0]['id']}/retry"), 409, "not_failed")                           # not failed anymore → no duplicate retry
    # AI model down: analysis fails visibly with a reason; no diagnoses are fabricated
    with session_scope() as s:
        e = s.get(Encounter, eid); e.hpi = (e.hpi or "") + " FAKE_FAIL_LLM"
        from server.domain.encounters import refresh_inputs; refresh_inputs(s, e)
    drain()
    ok(d.post(f"/api/v1/encounters/{eid}/analysis", json={"force": True}), 202)
    for _ in range(4):
        drain()
        with session_scope() as s:
            for j in s.scalars(select(Job).where(Job.status == "queued")):
                j.run_after = utcnow()
    b = ok(d.get(f"/api/v1/encounters/{eid}"))
    assert b["analysis"]["status"] == "failed" and b["analysis"]["error_detail"]
    assert all(m["status"] in ("failed", "skipped") for m in b["analysis"]["modules"] if m["module"] in ("western", "tcm"))
    assert not [x for x in b["diagnoses"] if x["analysis_id"] == b["analysis"]["id"]]
    # the doctor can still work manually: add own diagnosis → create a manual plan (AI-free path)
    ok(d.post(f"/api/v1/encounters/{eid}/diagnoses", json={"module": "western", "name": "紧张性头痛"}), 201)
    mp = ok(d.post(f"/api/v1/encounters/{eid}/plan/manual", json={"sections": ["medication"]}), 201)["plan"]
    assert mp["status"] == "draft" and mp["ai_used"] is False


# ================================================================================== S6  idempotency & concurrency
def test_s6_double_submit_and_concurrent_claims(app_client, roles):
    d1, d2 = roles.as_("dr-001"), roles.as_("dr-002")
    pt = ok(roles.as_("nu-001").post("/api/v1/patients", json={"name": "并发测试", "gender": "女", "age": 33, "phone": "13955550001"}), 201)["patient"]
    # nurse registers an encounter in the unassigned pool; same key twice → one encounter
    n = roles.as_("nu-001"); h = {"Idempotency-Key": "enc-1"}
    e1 = ok(n.post("/api/v1/encounters", json={"patient_id": pt["id"], "chief_complaint": "咳嗽"}, headers=h), 201)
    e2 = ok(n.post("/api/v1/encounters", json={"patient_id": pt["id"], "chief_complaint": "咳嗽"}, headers=h), 201)
    assert e1["encounter"]["id"] == e2["encounter"]["id"] and _count(Encounter) == 1
    # same key, DIFFERENT body → rejected (client bug), not silently merged
    err(n.post("/api/v1/encounters", json={"patient_id": pt["id"], "chief_complaint": "发热"}, headers=h), 409, "idempotency_key_reuse")
    eid = e1["encounter"]["id"]
    # two doctors claim at once → exactly one wins
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda c: c.post(f"/api/v1/encounters/{eid}/claim").status_code, (d1, d2)))
    assert sorted(res) == [200, 409]
    # patient duplicate registration with same phone+name → same patient
    again = ok(n.post("/api/v1/patients", json={"name": "并发测试", "gender": "女", "age": 33, "phone": "13955550001"}), 201)["patient"]
    assert again["id"] == pt["id"] and _count(Patient) == 1


def test_stale_edit_and_concurrent_double_sign_create_one_signature(app_client, roles):
    eid = _intake_and_analyse(roles); d = roles.as_("dr-001")
    west, tcm, mine = run_to_final(roles, eid)
    rx = ok(d.post(f"/api/v1/encounters/{eid}/prescriptions", json={"rx_type": "western", "items": [{"name": "二甲双胍片", "dose_text": "0.5g", "frequency": "bid", "route": "口服", "days": 14}]}), 201)["prescription"]
    import concurrent.futures as cf
    # penicillin-allergic patient already on amlodipine; metformin has no conflict → signing works; fire two sign requests at once
    with cf.ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda _: d.post(f"/api/v1/prescriptions/{rx['id']}/sign", json={"expected_version": rx["row_version"]}).status_code, range(2)))
    assert sorted(res) in ([200, 409], [409, 409], [200, 200])
    cur = ok(d.get(f"/api/v1/prescriptions/{rx['id']}"))["prescription"]
    assert cur["status"] == "signed" and len([e for e in ok(d.get(f"/api/v1/prescriptions/{rx['id']}"))["events"] if e["action"] == "sign"]) == 1
