"""Legacy → v3 import: safety, idempotency, fidelity. Uses a COPY of the real legacy DB when present, else a built fixture."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select

from server.db.base import session_scope
from server.db.models import (Analysis, Diagnosis, Encounter, FollowupAlert, FollowupResponse, LegacyMap, MedicalRecord, Patient, Prescription,
                              RecordVersion, TreatmentPlan, TherapyItem, User)

REAL = Path(__file__).resolve().parents[1] / "data" / "ai_clinical.db"


def build_fixture(path: Path):
    """A miniature legacy DB with the REAL legacy column names, including the nasty cases the live data has."""
    c = sqlite3.connect(path)
    c.executescript("""
    create table users(id text primary key, email text, name text, role text, password_hash text, is_active int, created_at text);
    create table patients(id text primary key, name text, gender text, age int, phone text, is_demo int, created_at text, extra_json text);
    create table encounters(id text primary key, patient_id text, doctor_id text, encounter_no text, chief_complaint text, status text, created_at text, clinical_data text, final_prescription_id text);
    create table lab_results(id text primary key, lab_report_id text, patient_id text, encounter_id text, test_name text, canonical_name text, value text, unit text, reference_low text, reference_high text, abnormal_flag text, test_time text, source_image_id text, ocr_confidence real, normalization_confidence real);
    create table tongue_images(id text primary key, encounter_id text, patient_id text, object_key text, created_at text);
    create table ai_runs(id text primary key, patient_id text, encounter_id text, model_name text, model_version text, prompt_version text, knowledge_version text, input_hash text, output_hash text, status text, latency_ms int, created_at text, module_timings_json text, is_stale int);
    create table ai_outputs(id text primary key, ai_run_id text, output_type text, structured_json text, display_text text);
    create table diagnosis_records(id text primary key, encounter_id text, patient_id text, doctor_id text, ai_output_id text, module text, western_diagnosis text, tcm_syndrome text, main_evidence text, treatment_goal text, review_status text, doctor_notes text, source text, created_at text);
    create table prescription_drafts(id text primary key, encounter_id text, patient_id text, doctor_id text, ai_run_id text, structured_json text, status text, created_at text, target_diagnosis_id text, is_stale int, diagnosis_snapshot_hash text);
    create table e_prescriptions(id text primary key, encounter_id text, patient_id text, doctor_id text, prescription_draft_id text, doc_type text, diagnosis_summary text, prescription_json text, signature_id text, doctor_name text, his_status text, request_id text, created_at text);
    create table therapy_item_reviews(id text primary key, prescription_draft_id text, encounter_id text, patient_id text, item_index int, status text, original_content text, edited_content text, doctor_id text, created_at text, updated_at text);
    create table medical_records(id text primary key, encounter_id text, patient_id text, content_json text, raw_text text, created_at text, status text, finalized_at text, doctor_id text, signature_id text, followup_plan_id text, updated_at text);
    create table followup_plans(id text primary key, patient_id text, encounter_id text, template text, days int, questions_json text, status text, created_at text);
    create table followup_responses(id text primary key, followup_plan_id text, patient_id text, answers_json text, ai_summary_json text, risk_level text, created_at text);
    create table followup_risk_events(id text primary key, followup_response_id text, patient_id text, rule_triggered text, risk_level text, escalated int, created_at text);
    create table doctor_profiles(doctor_id text primary key, dept text, specialties text, syndrome_preference text, formula_preference text, herb_preference text, non_drug_preference text, analysis_focus text, experience_notes text, updated_at text);
    """)
    J = json.dumps
    c.executemany("insert into users values(?,?,?,?,?,?,?)", [("dr-001", "d@x", "张明 主任医师", "DOCTOR", "h", 1, "2026-10-09 00:00:00"), ("dr-009", "z@x", "孙某 主治医师", "DOCTOR", "h", 1, "2026-10-09 00:00:00")])
    c.execute("insert into doctor_profiles values('dr-001','全科','[\"糖尿病\"]','辨证偏好','','','','', '[]','')")
    c.executemany("insert into patients values(?,?,?,?,?,?,?,?)", [
        ("p-001", "赵建国", "男", 45, "DEMO-138****1001", 1, "2026-10-09 08:17:18.453647", J({"insurance": "DEMO-INS-001", "allergies": [{"allergen": "青霉素", "severity": "严重"}]})),
        ("p-new", "王测试", "女", 35, "13900002023", 0, "2026-10-15 09:00:00.000000", J({"allergies": [], "id_card": "", "address": "", "insurance": ""}))])
    c.executemany("insert into encounters values(?,?,?,?,?,?,?,?,?)", [
        ("enc-001", "p-001", "dr-001", "enc-001", "发热3天伴咽痛咳嗽", "IN_PROGRESS", "2026-10-09 08:17:18", J({"source": "doctor", "raw_text": "发热3天", "history": {"symptoms": ["发热", "咽痛"], "comorbidities": []}, "medications": [{"name": "布洛芬", "dose": "0.3g", "freq": "prn"}], "allergies": [{"allergen": "青霉素", "severity": "严重"}, {"allergen": "海鲜", "severity": "轻"}]}), "x"),
        ("enc-orphan", "p-MISSING", "dr-001", "enc-o", "x", "PENDING", "2026-10-09 08:17:18", "{}", ""),
        ("enc-new", "p-new", "", "enc-n", "头痛", "PENDING", "2026-10-15 09:00:00", J({"source": "patient", "raw_text": "头痛三天"}), "")])
    c.executemany("insert into lab_results values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [("l1", "r1", "p-001", "enc-001", "HbA1c", "", "8.2", "%", "", "<7", "HIGH", "", "", 0, 0)])
    c.execute("insert into ai_runs values('run-ok','p-001','enc-001','orchestrator','','','','h1','', 'SUCCEEDED', 1000, '2026-10-09 09:00:00', '{}', 0)")
    c.execute("insert into ai_runs values('run-stuck','p-001','enc-001','orchestrator','','','','h2','', 'RUNNING', 0, '2026-10-09 10:00:00', '{}', 0)")
    c.execute("insert into ai_outputs values('o1','run-ok','western',?, 'x')", (J({"summary": "西医摘要", "differential_diagnoses": []}),))
    c.execute("insert into ai_outputs values('o2','run-ok','tongue',?, 'x')", (J({"skipped": True, "note": "no tongue image"}),))
    c.executemany("insert into diagnosis_records values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("d1", "enc-001", "p-001", "dr-001", "run-ok:western:0", "western", "急性咽炎", "", "咽痛发热", "", "accepted", "", "ai", "2026-10-09 09:10:00"),
        ("d2", "enc-001", "p-001", "dr-001", "run-ok:western:0", "western", "急性咽炎", "", "咽痛发热", "", "accepted", "", "ai", "2026-10-09 09:11:00"),   # legacy duplicate
        ("d3", "enc-001", "p-001", "dr-001", "", "tcm", "", "风热犯肺证", "", "", "modified", "", "ai", "2026-10-09 09:12:00")])
    unified = {"unified_plan_summary": "中西协同", "prescription": {"western_medications": [{"medication": "阿莫西林胶囊", "dose": "0.5g", "frequency": "每日3次", "duration": "5天"}],
               "herbal_drafts": [{"name": "银翘散加减", "formula_type": "classic", "herbs": ["金银花", "连翘", "甘草"], "doses": {"金银花": 15, "连翘": "15g", "甘草": 6}, "decoction": "水煎"}], "safety_notes": ["青霉素过敏"]},
               "therapy": {"candidates": [{"category": "推拿", "name": "颈部推拿", "source_type": "hospital", "acupoints": ["风池"], "contraindications": "皮肤破损"}], "lifestyle_advice": ["多饮水"]}}
    c.executemany("insert into prescription_drafts values(?,?,?,?,?,?,?,?,?,?,?)", [
        ("rx-u", "enc-001", "p-001", "dr-001", "", J(unified), "SUBMITTED_TO_HIS", "2026-10-09 09:30:00", "", 0, "hash1"),
        ("rx-per-dx", "enc-001", "p-001", "dr-001", "", J(unified), "AI_GENERATED", "2026-10-09 09:20:00", "d1", 1, ""),            # legacy per-diagnosis draft → NOT migrated
        ("rx-gen", "enc-001", "p-001", "dr-001", "", J({"status": "generating"}), "AI_GENERATED", "2026-10-09 09:21:00", "", 0, ""),
        ("rx-u2", "enc-001", "p-001", "dr-001", "", J(unified), "AI_GENERATED", "2026-10-09 09:50:00", "", 0, "hash2"),         # 2nd unified plan on the same encounter, no e-Rx
        ("rx-open", "enc-new", "p-new", "", "", J(unified), "AI_GENERATED", "2026-10-15 09:30:00", "", 0, "hash3")])           # AI draft on an OPEN encounter
    c.executemany("insert into e_prescriptions values(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("ep1", "enc-001", "p-001", "dr-001", "rx-u", "western_drugs", "急性咽炎", J({"western_medications": [{"medication": "布洛芬", "dose": "0.3g", "frequency": "bid", "usage": "口服", "days": 3}], "patent_medications": []}), "sig-1", "张明", "ACCEPTED", "rq", "2026-10-09 09:40:00"),
        ("ep2", "enc-001", "p-001", "dr-001", "rx-u", "herbal_decoction", "急性咽炎", J({"herbal_draft": {"name": "银翘散加减", "herbs": ["金银花", "连翘"], "doses": {"金银花": "15g", "连翘": "12g"}}}), "sig-1", "张明", "ACCEPTED", "rq", "2026-10-09 09:41:00")])
    c.execute("insert into therapy_item_reviews values('tr1','rx-u','enc-001','p-001',0,'EDITED','{}',?, 'dr-001','','')", (J({"acupoints": ["风池", "天突"]}),))
    c.executemany("insert into medical_records values(?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("mr-draft", "enc-001", "p-001", J({"text": "主诉：发热。处理：对症"}), "主诉：发热", "2026-10-09 09:00:00", "draft", None, "dr-001", "", "", "2026-10-09 09:00:00"),
        ("mr-final", "enc-001", "p-001", J({"patient_info": {"name": "赵建国", "gender": "男", "age": 45}, "chief_complaint": "发热3天伴咽痛咳嗽", "history": {"comorbidities": []}, "allergies": [{"allergen": "青霉素"}], "medications": [], "labs": [{"item": "HbA1c", "value": "8.2", "unit": "%"}],
                                                      "confirmed_diagnoses": [{"western_diagnosis": "急性咽炎", "module": "western", "review_status": "accepted"}], "editor_text": "【综合治疗方案】..."}), "x", "2026-10-09 10:00:00", "finalized", "2026-10-09 10:00:00", "dr-001", "", "", "2026-10-09 10:00:00"),
        ("mr-final2", "enc-001", "p-001", J({"patient_info": {"name": "赵建国"}, "chief_complaint": "发热3天伴咽痛咳嗽", "history": {}, "allergies": [], "medications": [], "labs": [], "confirmed_diagnoses": [], "editor_text": "再次定稿"}), "x", "2026-10-09 10:30:00", "finalized", "2026-10-09 10:30:00", "dr-001", "", "", "2026-10-09 10:30:00"),
        ("mr-new", "enc-new", "p-new", J({"text": "头痛"}), "头痛", "2026-10-15 09:00:00", "draft", None, "", "", "", "2026-10-15 09:00:00")])
    c.executemany("insert into followup_plans values(?,?,?,?,?,?,?,?)", [
        ("f1", "p-001", "enc-001", "post_visit", 14, J([{"q": "症状是否缓解？", "k": "x"}]), "ACTIVE", "2026-10-09 11:00:00"),
        ("f2", "p-001", "enc-001", "post_visit", 14, J([]), "ACTIVE", "2026-10-09 11:01:00")])                                    # duplicate plan
    c.executemany("insert into followup_responses values(?,?,?,?,?,?,?)", [
        ("fr1", "f1", "p-001", J({"text": "胸痛加重，呼吸困难"}), J({}), "high_risk", "2026-10-10 08:00:00"),
        ("fr-orphan", "nonexistent-plan", "p-001", J({"text": "probe"}), J({}), "normal", "2026-10-10 09:00:00")])
    c.execute("insert into followup_risk_events values('re1','fr1','p-001','keyword:胸痛','high_risk',1,'2026-10-10 08:00:00')")
    c.commit(); c.close()


@pytest.fixture()
def legacy(settings_env):
    s = settings_env
    s.data_dir.mkdir(parents=True, exist_ok=True)
    build_fixture(s.legacy_db_path)
    return s.legacy_db_path


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(settings_env, dry=False):
    from server.migrate_legacy import run_import
    return run_import(dry_run=dry)


def test_legacy_db_is_never_modified(settings_env, legacy):
    before = sha(legacy)
    run(settings_env); run(settings_env)
    assert sha(legacy) == before
    assert not Path(str(legacy) + "-wal").exists()


def test_import_is_idempotent_and_traceable(settings_env, legacy):
    r1 = run(settings_env)
    with session_scope() as s:
        counts = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in (Patient, Encounter, Diagnosis, Prescription, TreatmentPlan, MedicalRecord, RecordVersion, LegacyMap)}
    r2 = run(settings_env)
    with session_scope() as s:
        again = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in (Patient, Encounter, Diagnosis, Prescription, TreatmentPlan, MedicalRecord, RecordVersion, LegacyMap)}
    assert counts == again and counts["patients"] == 2 and counts["encounters"] == 2          # orphan encounter skipped; second run adds nothing
    assert any("orphan" in w or "missing" in w for w in r1["warnings"])


def test_dry_run_writes_nothing(settings_env, legacy):
    run(settings_env, dry=True)
    with session_scope() as s:
        assert s.scalar(select(func.count(Patient.id))) == 0


def test_patients_fidelity_no_fabrication(settings_env, legacy):
    run(settings_env)
    with session_scope() as s:
        a = s.scalar(select(Patient).where(Patient.name == "赵建国")); b = s.scalar(select(Patient).where(Patient.name == "王测试"))
        assert a.is_sample and a.phone == "" and a.gender == "男" and a.age_years == 45                 # DEMO phone dropped, not invented
        assert {x.allergen for x in a.allergies} == {"青霉素", "海鲜"}                                 # patient + encounter allergies unioned, no dupes
        assert not b.is_sample and b.phone == "13900002023" and b.mrn.startswith("P2610") and a.mrn.startswith("P2610")


def test_encounters_keep_facts_and_original_dates(settings_env, legacy):
    run(settings_env)
    with session_scope() as s:
        e = s.scalar(select(Encounter).where(Encounter.chief_complaint == "发热3天伴咽痛咳嗽"))
        assert e.encounter_no == "E20261009-001" and e.hpi == "发热3天" and e.source == "staff" and e.doctor.username == "dr-001"
        assert e.current_meds_json == [{"name": "布洛芬", "dose": "0.3g", "frequency": "prn"}] and e.symptoms_json == ["发热", "咽痛"]
        n = s.scalar(select(Encounter).where(Encounter.chief_complaint == "头痛"))
        assert n.source == "patient_intake" and n.doctor_id is None and n.status == "WAITING"
        assert n.patient_reported_json["chief_complaint"] == "头痛"                                     # original self-report preserved


def test_diagnoses_deduplicated_and_ai_original_kept(settings_env, legacy):
    run(settings_env)
    with session_scope() as s:
        live = [d for d in s.scalars(select(Diagnosis).where(Diagnosis.status.in_(["accepted", "modified"])))]
        assert sorted((d.module, d.name) for d in live) == [("tcm", "风热犯肺证"), ("western", "急性咽炎")]
        assert any(d.status == "withdrawn" for d in s.scalars(select(Diagnosis)))                       # duplicate retired, not deleted


def test_per_diagnosis_drafts_not_migrated_unified_becomes_one_plan_and_separate_prescriptions(settings_env, legacy):
    r = run(settings_env)
    with session_scope() as s:
        all_plans = s.scalars(select(TreatmentPlan)).all()
        by_enc = {}
        for pl in all_plans:
            by_enc.setdefault(pl.encounter_id, []).append(pl)
        e1 = s.scalar(select(Encounter).where(Encounter.chief_complaint == "发热3天伴咽痛咳嗽"))
        live1 = [p for p in by_enc[e1.id] if p.status in ("draft", "confirmed")]
        assert len(by_enc[e1.id]) == 2 and len(live1) == 1                                              # two legacy plans → exactly ONE live, one superseded
        assert next(p for p in by_enc[e1.id] if p.status == "superseded").superseded_by == live1[0].id
        plans = [live1[0]]
        assert plans[0].content_json["summary"] == "中西协同" and plans[0].ai_original_json["legacy"]
        assert r["imported"].get("skipped_legacy_drafts") == 2                                          # the per-diagnosis + the 'generating' placeholder
        # therapy review (EDITED) applied, original retained
        t = plans[0].therapy_items[0]
        assert t.status == "modified" and t.acupoints_json == ["风池", "天突"] and t.original_json["acupoints"] == ["风池"]
        assert t.source_type == "ai"                                                                    # legacy 'hospital' claim without a code is not trusted
        # e-prescriptions → separate Prescription rows linked to the plan, with structured items
        rxs = s.scalars(select(Prescription).where(Prescription.encounter_id == e1.id).order_by(Prescription.rx_no)).all()
        assert {r.rx_type for r in rxs} == {"western", "herbal"} and len(rxs) == 2                       # NO unsigned AI drafts piled onto the finalised visit
        assert all(r.plan_id == plans[0].id or s.get(TreatmentPlan, r.plan_id).status == "superseded" for r in rxs)
        # …but the AI draft on the OPEN encounter IS carried over as an unsigned, editable draft
        en = s.scalar(select(Encounter).where(Encounter.chief_complaint == "头痛"))
        open_rx = s.scalars(select(Prescription).where(Prescription.encounter_id == en.id)).all()
        assert open_rx and all(r.status == "draft" and r.signed_at is None and r.his_mode == "none" for r in open_rx)
        herbal = next(r for r in rxs if r.rx_type == "herbal")
        assert [(i.name, i.dose_g) for i in herbal.items] == [("金银花", 15.0), ("连翘", 12.0)]
        western = next(r for r in rxs if r.rx_type == "western")
        assert western.items[0].name == "布洛芬" and western.items[0].frequency == "bid" and western.items[0].days == 3
        # legacy signed Rx are labelled MOCK, never claimed as real HIS sync; safety layer evaluated them
        assert all(r.his_mode == "mock" and r.his_ref == "LEGACY-MOCK" for r in rxs) and all(r.safety_json["rules_version"] for r in rxs)
        # a legacy plan can never be "current": staleness is detected by construction
        from server.domain import plans as P
        enc = s.get(Encounter, plans[0].encounter_id)
        assert P.plan_staleness(s, plans[0], enc)["stale"] is True


def test_records_become_versions_finalized_stays_final_and_frozen(settings_env, legacy):
    run(settings_env)
    with session_scope() as s:
        rec = s.scalar(select(MedicalRecord).join(Encounter, Encounter.id == MedicalRecord.encounter_id).where(Encounter.chief_complaint == "发热3天伴咽痛咳嗽"))
        assert rec.status == "finalized" and rec.final_version_id
        vers = s.scalars(select(RecordVersion).where(RecordVersion.record_id == rec.id).order_by(RecordVersion.version_no)).all()
        assert [v.kind for v in vers] == ["draft", "final", "amendment"] and vers[1].legacy_source == "medical_records:mr-final"
        assert rec.final_version_id == vers[2].id
        assert "再次定稿" in vers[2].note and "更正版本" in vers[2].note
        fin = vers[1]
        assert fin.content_json["sections"]["chief_complaint"]["text"] == "发热3天伴咽痛咳嗽" and "急性咽炎" in fin.content_json["sections"]["diagnoses"]["text"]
        assert fin.snapshot_json["patient"]["name"] == "赵建国" and fin.snapshot_json["legacy"] is True
        from server.domain.records import verify_version
        assert verify_version(fin) and s.get(Encounter, rec.encounter_id).status == "COMPLETED"
        # the draft-only encounter stays an editable draft
        r2 = s.scalar(select(MedicalRecord).where(MedicalRecord.status == "draft"))
        assert r2 is not None and "头痛" in s.get(RecordVersion, r2.working_version_id).content_json["sections"]["hpi"]["text"]


def test_followups_duplicates_merged_orphans_quarantined_alert_kept(settings_env, legacy):
    r = run(settings_env)
    with session_scope() as s:
        from server.db.models import FollowupPlan
        assert s.scalar(select(func.count(FollowupPlan.id))) == 1                                       # 2 identical legacy plans → 1
        assert s.scalar(select(func.count(FollowupResponse.id))) == 1                                   # orphan response (plan missing) NOT attached to anything
        al = s.scalar(select(FollowupAlert))
        assert al.level == "high_risk" and al.status == "open" and al.rules_json[0]["rule"] == "keyword:胸痛"
        m = s.scalar(select(LegacyMap).where(LegacyMap.legacy_table == "followup_responses", LegacyMap.legacy_id == "fr-orphan"))
        assert m is not None and m.new_table == "skipped"                                               # traceable: we know exactly what was left out and why
    assert any("orphan" in w for w in r["warnings"])


def test_ai_history_kept_readonly_and_stuck_run_is_not_pretended_complete(settings_env, legacy):
    run(settings_env)
    with session_scope() as s:
        ok_, stuck = s.scalars(select(Analysis).order_by(Analysis.created_at)).all()
        assert ok_.status == "succeeded" and not ok_.is_current and ok_.trigger == "legacy" and {m.module for m in ok_.modules} == {"western", "tongue"}
        assert stuck.status == "failed" and stuck.error_code == "legacy_interrupted"                    # the Oct-9 'RUNNING forever' run is honestly marked
        assert next(m for m in ok_.modules if m.module == "tongue").status == "skipped"


def test_legacy_users_without_passwords_are_disabled_stubs(settings_env, legacy):
    run(settings_env)
    with session_scope() as s:
        u = s.scalar(select(User).where(User.username == "dr-009"))
        assert u.active is False and u.password_hash == "" and u.display_name == "孙某" and u.title == "主治医师"     # unusable hash → disabled stub
        d1 = s.scalar(select(User).where(User.username == "dr-001"))
        assert d1.profile_json["syndrome_preference"] == "辨证偏好"                                    # doctor profile carried over


@pytest.mark.skipif(not REAL.exists(), reason="real legacy DB not present")
def test_import_of_a_copy_of_the_real_database(settings_env, tmp_path):
    settings_env.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(REAL, settings_env.legacy_db_path)
    r = run(settings_env)
    with session_scope() as s:
        assert s.scalar(select(func.count(Patient.id))) >= 15 and s.scalar(select(func.count(Encounter.id))) >= 12
        # every migrated encounter keeps its patient; the number of migrated encounters equals the legacy encounters whose patient exists
        src = sqlite3.connect(f"file:{REAL}?mode=ro", uri=True)
        expect = src.execute("select count(*) from encounters e join patients p on p.id=e.patient_id").fetchone()[0]
        src.close()
        assert s.scalar(select(func.count(Encounter.id))) == expect
        for e in s.scalars(select(Encounter)):
            assert e.patient is not None and e.encounter_no.startswith("E2026")
        n_final = s.scalar(select(func.count(MedicalRecord.id)).where(MedicalRecord.status == "finalized"))
        assert n_final >= 3
        assert s.scalar(select(func.count(TreatmentPlan.id))) >= 2
    assert run(settings_env)["imported"] == {}                                                           # idempotent on real data too


def test_legacy_passwords_carry_over_but_default_ones_force_a_change(settings_env, legacy):
    import bcrypt
    c = sqlite3.connect(legacy)
    good = bcrypt.hashpw(b"Real-Secret-2026", bcrypt.gensalt(rounds=4)).decode()
    default = bcrypt.hashpw(b"demo123", bcrypt.gensalt(rounds=4)).decode()
    c.execute("update users set password_hash=? where id='dr-001'", (default,))
    c.execute("update users set password_hash=? where id='dr-009'", (good,))
    c.commit(); c.close()
    r = run(settings_env)
    with session_scope() as s:
        a = s.scalar(select(User).where(User.username == "dr-001")); b = s.scalar(select(User).where(User.username == "dr-009"))
        assert a.active and a.must_change_password and bcrypt.checkpw(b"demo123", a.password_hash.encode())      # still logs in, but must change at once
        assert b.active and not b.must_change_password and bcrypt.checkpw(b"Real-Secret-2026", b.password_hash.encode())
    assert any("known default password" in w for w in r["warnings"])
