"""Domain invariants: patients/encounters, diagnoses→plan staleness, prescriptions lifecycle, record merge safety."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from server.db.models import (Diagnosis, Encounter, LabItem, MedicalRecord, Patient, Prescription, RecordVersion,
                              TreatmentPlan)
from server.domain import encounters as enc_svc
from server.domain import plans as plan_svc
from server.domain import prescriptions as rx_svc
from server.domain import records as rec_svc
from server.domain.enums import DxStatus, PlanStatus, RxStatus
from server.errors import Conflict, Forbidden, StaleVersion, Unprocessable
from server.security.audit import Actor


@pytest.fixture()
def world(db, make_user):
    doc = make_user("dr-w", name="张医生", title="主任医师", license_no="L-001")
    other = make_user("dr-o", name="李医生")
    a = Actor.staff(doc)
    p, _ = enc_svc.create_patient(db, a, name="王测试", gender="女", age=34, phone="13900001111", allergies=["青霉素"])
    enc = enc_svc.create_encounter(db, a, patient=p, doctor_id=doc.id, chief_complaint="头痛3天", hpi="伴眩晕")
    db.commit()
    return type("W", (), dict(doc=doc, other=other, actor=a, patient=p, enc=enc))


def dx(db, w, name="高血压", module="western", status=DxStatus.ACCEPTED.value):
    d = Diagnosis(encounter_id=w.enc.id, module=module, name=name, status=status, origin="ai", sort_order=len(
        db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == w.enc.id)).all()))
    db.add(d); db.commit()
    return d


# ================================================================== patients
def test_duplicate_phone_and_name_reuses_patient(db, world):
    p2, dups = enc_svc.create_patient(db, world.actor, name="王测试", gender="女", age=34, phone="13900001111")
    assert p2.id == world.patient.id and dups == []


def test_same_name_different_person_is_flagged_not_merged(db, world):
    p2, dups = enc_svc.create_patient(db, world.actor, name="王测试", gender="女", age=35, phone="13900002222")
    assert p2.id != world.patient.id and world.patient.id in [d.id for d in dups]


def test_patient_validation(db, world):
    for kw in ({"name": ""}, {"name": "A", "phone": "123"}, {"name": "A", "gender": "X"}, {"name": "A", "age": 200}):
        with pytest.raises(Unprocessable):
            enc_svc.create_patient(db, world.actor, **kw)


# ================================================================== inputs version
def test_fact_edit_bumps_inputs_version_and_requires_fresh_row_version(db, world):
    enc = world.enc
    v0, rv0 = enc.inputs_version, enc.row_version
    res = enc_svc.update_facts(db, world.actor, enc, {"hpi": "伴眩晕、恶心"}, expected_version=rv0)
    db.commit()
    assert res["inputs_changed"] and enc.inputs_version == v0 + 1
    with pytest.raises(StaleVersion):
        enc_svc.update_facts(db, world.actor, enc, {"hpi": "x"}, expected_version=rv0)       # old row_version
    res = enc_svc.update_facts(db, world.actor, enc, {"hpi": "伴眩晕、恶心"}, expected_version=enc.row_version)
    assert res["changed_fields"] == []                                                       # no-op: no bump


def test_verifying_a_lab_value_does_not_stale_anything_but_correcting_it_does(db, world):
    """Verification is bookkeeping (no clinical change) — it must not invalidate a confirmed plan; a corrected value must."""
    from server.db.models import LabItem
    dx(db, world)
    lab = LabItem(encounter_id=world.enc.id, patient_id=world.enc.patient_id, name="谷丙转氨酶", value_text="19", unit="U/L", ref_text="0-40", flag="N", source="ocr", verified=False)
    db.add(lab); db.flush(); enc_svc.refresh_inputs(db, world.enc)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    v0 = world.enc.inputs_version
    lab.verified = True
    assert enc_svc.refresh_inputs(db, world.enc) is False and world.enc.inputs_version == v0           # verifying changes nothing
    db.commit(); assert plan.status == PlanStatus.CONFIRMED.value and not plan_svc.plan_staleness(db, plan, world.enc)["stale"]
    lab.value_text = "190"; lab.flag = "H"                                                               # the doctor CORRECTS the value
    assert enc_svc.refresh_inputs(db, world.enc) is True and world.enc.inputs_version == v0 + 1
    db.commit(); assert plan_svc.plan_staleness(db, plan, world.enc)["inputs_changed"]


def test_allergy_added_changes_inputs_hash(db, world):
    h0 = world.enc.inputs_hash
    enc_svc.sync_allergies(db, world.patient, ["磺胺类"], source="doctor", actor_id=world.doc.id)
    assert enc_svc.refresh_inputs(db, world.enc) and world.enc.inputs_hash != h0


# ================================================================== diagnoses → plan staleness
def test_final_set_and_hash_follow_review_actions(db, world):
    d1, d2 = dx(db, world, "高血压"), dx(db, world, "肝阳上亢证", "tcm", DxStatus.PENDING.value)
    h1 = plan_svc.dx_set_hash(plan_svc.final_diagnoses(db, world.enc.id))
    plan_svc.review_diagnosis(db, world.actor, d2, action="accept"); db.commit()
    h2 = plan_svc.dx_set_hash(plan_svc.final_diagnoses(db, world.enc.id))
    plan_svc.review_diagnosis(db, world.actor, d2, action="modify", name="肝阳上亢兼痰湿证"); db.commit()
    h3 = plan_svc.dx_set_hash(plan_svc.final_diagnoses(db, world.enc.id))
    assert len({h1, h2, h3}) == 3
    assert d2.original_name == "肝阳上亢证" and d2.name == "肝阳上亢兼痰湿证"          # AI original preserved


def test_modify_requires_name_and_withdraw_only_doctor_authored(db, world):
    d = dx(db, world, "x", status=DxStatus.PENDING.value)
    with pytest.raises(Unprocessable):
        plan_svc.review_diagnosis(db, world.actor, d, action="modify", name=" ")
    with pytest.raises(Unprocessable):
        plan_svc.review_diagnosis(db, world.actor, d, action="withdraw")
    mine = plan_svc.add_doctor_diagnosis(db, world.actor, world.enc, module="western", name="偏头痛"); db.commit()
    plan_svc.review_diagnosis(db, world.actor, mine, action="withdraw"); db.commit()
    assert mine.status == DxStatus.WITHDRAWN.value


def test_only_one_primary_diagnosis(db, world):
    a, b = dx(db, world, "A"), dx(db, world, "B")
    plan_svc.review_diagnosis(db, world.actor, a, action="accept", primary=True)
    plan_svc.review_diagnosis(db, world.actor, b, action="accept", primary=True); db.commit()
    assert (a.is_primary, b.is_primary) == (False, True)


def test_one_live_plan_and_supersede_history(db, world):
    dx(db, world, "高血压")
    p1 = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False); db.commit()
    p2 = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False); db.commit()
    live = db.scalars(select(TreatmentPlan).where(TreatmentPlan.encounter_id == world.enc.id,
                      TreatmentPlan.status.in_(["generating", "draft", "confirmed"]))).all()
    assert [p.id for p in live] == [p2.id] and p1.status == PlanStatus.SUPERSEDED.value and p1.superseded_by == p2.id
    assert (p1.version_no, p2.version_no) == (1, 2)


def test_plan_needs_final_diagnoses(db, world):
    with pytest.raises(Unprocessable) as e:
        plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False)
    assert e.value.code == "no_final_diagnoses"


def test_confirmed_plan_drops_to_draft_when_diagnosis_set_changes(db, world):
    d = dx(db, world, "高血压")
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    assert plan.status == PlanStatus.CONFIRMED.value
    plan_svc.review_diagnosis(db, world.actor, d, action="reject"); db.commit()
    assert plan.status == PlanStatus.DRAFT.value and plan.confirmed_at is None
    assert plan_svc.plan_staleness(db, plan, world.enc)["dx_changed"]
    with pytest.raises(Conflict) as e:
        plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version)
    assert e.value.code == "plan_stale"


def test_plan_goes_stale_when_clinical_inputs_change(db, world):
    dx(db, world)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False); db.commit()
    assert not plan_svc.plan_staleness(db, plan, world.enc)["stale"]
    enc_svc.update_facts(db, world.actor, world.enc, {"chief_complaint": "胸痛"}, expected_version=world.enc.row_version); db.commit()
    assert plan_svc.plan_staleness(db, plan, world.enc)["inputs_changed"]


def test_rebase_keeps_doctor_edits_clears_staleness_and_requires_reconfirmation(db, world):
    """Doctor says "this plan still applies" after the facts changed: no regeneration, content untouched, but the
    plan is no longer stale, drops to draft (must be re-confirmed) and the written reason is audited."""
    from server.db.models import AuditEvent
    dx(db, world)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False)
    plan_svc.edit_plan_content(db, world.actor, plan, world.enc, {"summary": "医师手写的方案概述"}, expected_version=plan.row_version)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    enc_svc.update_facts(db, world.actor, world.enc, {"chief_complaint": "胸痛"}, expected_version=world.enc.row_version); db.commit()
    assert plan_svc.plan_staleness(db, plan, world.enc)["stale"]
    with pytest.raises(Unprocessable) as e:                                          # a bare click is not enough
        plan_svc.rebase_plan(db, world.actor, plan, world.enc, reason="好", expected_version=plan.row_version)
    assert e.value.code == "reason_required"
    plan_svc.rebase_plan(db, world.actor, plan, world.enc, reason="新增主诉与本方案无关，方案仍适用", expected_version=plan.row_version); db.commit()
    assert not plan_svc.plan_staleness(db, plan, world.enc)["stale"]
    assert plan.content_json["summary"] == "医师手写的方案概述"                          # doctor text untouched
    assert plan.status == PlanStatus.DRAFT.value and plan.confirmed_at is None         # must be confirmed again
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version)    # and now it can be
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "plan.rebase", AuditEvent.entity_id == plan.id)).all()
    assert len(ev) == 1 and "新增主诉与本方案无关" in ev[0].summary


def test_rebase_of_a_fresh_plan_is_a_noop_and_superseded_plans_cannot_be_rebased(db, world):
    dx(db, world)
    p1 = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False); db.commit()
    v = p1.row_version
    plan_svc.rebase_plan(db, world.actor, p1, world.enc, reason="没有变化也可调用", expected_version=v)
    assert p1.row_version == v                                                          # idempotent, nothing re-anchored
    plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False); db.commit()
    with pytest.raises(Conflict):
        plan_svc.rebase_plan(db, world.actor, p1, world.enc, reason="旧版本不能沿用", expected_version=p1.row_version)


def test_plan_safety_preview_flags_allergy_and_antagonism_without_storing_anything(db, world):
    """The plan's drafted medication is checked by the same engine used at signing — before any prescription exists."""
    from server.db.models import Prescription as Rx
    dx(db, world)
    enc_svc.sync_allergies(db, world.enc.patient, ["青霉素"], source="doctor"); db.commit()
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication", "herbal"], ai_used=False)
    plan_svc.edit_plan_content(db, world.actor, plan, world.enc, {
        "medication": {"western": [{"name": "阿莫西林", "dose_text": "0.5g", "frequency": "tid", "route": "口服", "days": 5}], "patent": []},
        "herbal": [{"title": "测试方", "herbs": [{"name": "甘草", "dose_g": 6}, {"name": "甘遂", "dose_g": 1}], "doses_count": 7,
                    "decoction": "水煎", "usage": "每日1剂"}]}, expected_version=plan.row_version); db.commit()
    before = db.scalar(select(func.count(Rx.id)))
    out = rx_svc.preview_plan_safety(db, plan, world.enc)
    rules = {i["rule_id"] for g in out["groups"] for i in g["issues"]}
    assert "allergy.drug" in rules or "allergy.direct" in rules
    assert "herb.eighteen_antagonism" in rules
    assert out["summary"]["critical"] >= 2 and [g["ref"] for g in out["groups"]] == ["western", "herbal:0"]
    assert db.scalar(select(func.count(Rx.id))) == before                         # read-only


def test_editing_confirmed_plan_invalidates_confirmation(db, world):
    dx(db, world)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    plan_svc.edit_plan_content(db, world.actor, plan, world.enc, {"summary": "改"}, expected_version=plan.row_version); db.commit()
    assert plan.status == PlanStatus.DRAFT.value and plan.confirmed_content_hash == ""


# ================================================================== prescriptions
def herbal_items():
    return [{"name": "黄芪", "dose_g": 20}, {"name": "麦冬", "dose_g": 12}, {"name": "五味子", "dose_g": 6}]


def make_rx(db, w, rx_type="herbal", items=None, **kw):
    hdr = {"doses_count": 7, "decoction": "水煎400ml", "herbal_usage": "每日1剂"} if rx_type == "herbal" else None
    rx = rx_svc.create_prescription(db, w.actor, w.enc, w.doc, rx_type=rx_type, items=items if items is not None else herbal_items(),
                                    header=hdr, **kw)
    db.commit()
    return rx


def test_prescription_independent_of_plan_and_many_per_encounter(db, world):
    dx(db, world)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication", "herbal"], ai_used=False); db.commit()
    r1 = make_rx(db, world, plan_id=plan.id)
    r2 = make_rx(db, world, "western", [{"name": "氨氯地平", "dose_text": "5mg", "frequency": "qd", "days": 14, "route": "口服"}], plan_id=plan.id)
    assert {r1.plan_id, r2.plan_id} == {plan.id} and r1.rx_no != r2.rx_no
    r3 = make_rx(db, world)                                                         # no plan at all is allowed
    assert r3.plan_id is None


def test_rx_from_stale_plan_requires_acknowledgement(db, world):
    d = dx(db, world)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["herbal"], ai_used=False); db.commit()
    plan_svc.review_diagnosis(db, world.actor, d, action="reject"); db.commit()
    dx(db, world, "新诊断")
    with pytest.raises(Conflict) as e:
        make_rx(db, world, plan_id=plan.id)
    assert e.value.code == "plan_stale"
    assert make_rx(db, world, plan_id=plan.id, acknowledge_stale="诊断已调整，方案核对无误").plan_id == plan.id


def test_edit_only_in_draft_with_optimistic_lock(db, world):
    rx = make_rx(db, world)
    with pytest.raises(StaleVersion):
        rx_svc.update_prescription(db, world.actor, rx, world.enc, items=herbal_items(), header=None, title=None, notes=None, expected_version=999)
    rx_svc.update_prescription(db, world.actor, rx, world.enc, items=herbal_items() + [{"name": "山药", "dose_g": 15}], header=None, title=None,
                               notes=None, expected_version=rx.row_version); db.commit()
    assert len(rx.items) == 4


def test_sign_blocked_until_safety_resolved(db, world):
    rx = make_rx(db, world, items=[{"name": "甘草", "dose_g": 6}, {"name": "甘遂", "dose_g": 1}])
    with pytest.raises(Conflict) as e:
        rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version)
    assert e.value.code == "safety_unresolved"
    need = {b["key"]: b["need"] for b in e.value.details["blockers"]}
    assert "reason" in need.values()
    rx_svc.acknowledge(db, world.actor, rx, world.enc, {k: "ok" for k in need}); db.commit()            # too short
    with pytest.raises(Conflict):
        rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version)
    rx_svc.acknowledge(db, world.actor, rx, world.enc, {k: "甘遂半夏汤经方配伍，已告知并监测" for k in need}); db.commit()
    rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version); db.commit()
    assert rx.status == RxStatus.SIGNED.value and rx.signature_hash and rx.override_reasons_json


def test_penicillin_allergy_blocks_amoxicillin_until_reason(db, world):
    rx = make_rx(db, world, "western", [{"name": "阿莫西林胶囊", "dose_text": "0.5g", "frequency": "tid", "days": 5, "route": "口服"}])
    with pytest.raises(Conflict) as e:
        rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version)
    assert any("过敏" in b["message"] for b in e.value.details["blockers"])


def test_edit_after_ack_drops_stale_acks(db, world):
    rx = make_rx(db, world, items=[{"name": "黄连", "dose_g": 7}])                       # warning (above range)
    keys = [i["key"] for i in rx.safety_json["issues"] if i["rule_id"] == "herb.dose_above"]
    rx_svc.acknowledge(db, world.actor, rx, world.enc, {keys[0]: "ack"}); db.commit()
    rx_svc.update_prescription(db, world.actor, rx, world.enc, items=[{"name": "黄连", "dose_g": 12}], header=None, title=None,
                               notes=None, expected_version=rx.row_version); db.commit()
    assert keys[0] not in rx.safety_json["acks"]                                          # finding changed → ack gone


def test_only_prescriber_signs_and_signed_rx_is_immutable(db, world):
    rx = make_rx(db, world)
    with pytest.raises(Forbidden):
        rx_svc.sign(db, world.actor, rx, world.enc, world.other, expected_version=rx.row_version)
    rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version); db.commit()
    with pytest.raises(Conflict) as e:
        rx_svc.update_prescription(db, world.actor, rx, world.enc, items=[], header=None, title=None, notes=None, expected_version=rx.row_version)
    assert e.value.code == "rx_not_editable"
    rx_svc.unsign(db, world.actor, rx, world.enc, reason="剂量需调整"); db.commit()
    assert rx.status == RxStatus.DRAFT.value and rx.signature_hash == ""


def test_pharmacy_flow_return_resign_and_dispense_immutability(db, world, make_user):
    ph = make_user("ph-1", role="PHARMACIST"); pa = Actor.staff(ph)
    rx = make_round_signed(db, world)
    rx_svc.pharmacy_start(db, pa, rx); rx_svc.pharmacy_start(db, pa, rx)                  # idempotent
    with pytest.raises(Unprocessable):
        rx_svc.pharmacy_decide(db, pa, rx, approve=False, note="")                        # must give reason
    rx_svc.pharmacy_decide(db, pa, rx, approve=False, note="黄芪剂量过大请核对"); db.commit()
    assert rx.status == RxStatus.RETURNED.value
    rx_svc.update_prescription(db, world.actor, rx, world.enc, items=[{"name": "黄芪", "dose_g": 15}, {"name": "麦冬", "dose_g": 12}], header=None,
                               title=None, notes=None, expected_version=rx.row_version)
    rx.status = RxStatus.DRAFT.value; db.commit()                                         # (returned→draft is the doctor's reopen action)
    rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version)
    rx_svc.pharmacy_start(db, pa, rx); rx_svc.pharmacy_decide(db, pa, rx, approve=True); rx_svc.pharmacy_dispense(db, pa, rx)
    rx_svc.pharmacy_dispense(db, pa, rx); db.commit()                                     # idempotent
    assert rx.status == RxStatus.DISPENSED.value
    with pytest.raises(Conflict):
        rx_svc.void(db, world.actor, rx, world.enc, reason="x" * 3)                        # dispensed cannot be voided


def make_round_signed(db, w):
    rx = make_rx(db, w)
    rx_svc.sign(db, w.actor, rx, w.enc, w.doc, expected_version=rx.row_version); db.commit()
    return rx


def test_pharmacy_return_after_finalization_can_be_corrected_and_resigned(db, world, make_user):
    """Dead-end guard: record finalized → pharmacy returns a signed Rx → doctor must be able to fix + re-sign it, while an
    ordinary never-reviewed draft on a completed visit still cannot be signed."""
    ph = make_user("ph-ret", role="PHARMACIST")
    dx(db, world)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["herbal"], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version)
    rx = make_rx(db, world)
    rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version); db.commit()
    rec = rec_svc.get_or_create(db, world.actor, world.enc); db.commit()
    rec_svc.finalize(db, world.actor, rec, world.enc, world.doc, ignore=[]); db.commit()
    assert world.enc.status == "COMPLETED"
    pact = Actor.staff(ph)
    rx_svc.pharmacy_start(db, pact, rx); rx_svc.pharmacy_decide(db, pact, rx, approve=False, note="剂量需复核"); db.commit()
    assert rx.status == "returned"
    rx.signed_by = rx.signed_at = None; rx.signature_hash = ""
    rx_svc._transition(db, rx, world.actor, "draft", "reopen"); db.commit()
    rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version); db.commit()      # allowed: it was returned
    assert rx.status == "signed"
    # …but an ordinary draft that the pharmacy never touched still cannot be signed on a finalized visit
    world.enc.status = "IN_PROGRESS"; stray = make_rx(db, world); world.enc.status = "COMPLETED"; db.commit()
    with pytest.raises(Conflict) as e:
        rx_svc.sign(db, world.actor, stray, world.enc, world.doc, expected_version=stray.row_version)
    assert e.value.code == "encounter_closed" and stray.status == "draft"


def test_mock_his_is_labelled_simulated_and_idempotent(db, world):
    rx = make_round_signed(db, world)
    rx_svc.submit_mock_his(db, world.actor, rx, world.enc); ref = rx.his_ref
    rx_svc.submit_mock_his(db, world.actor, rx, world.enc); db.commit()
    assert (rx.his_mode, rx.his_status) == ("mock", "mock_submitted") and rx.his_ref == ref and ref.startswith("MOCK-")


def test_mock_his_refuses_unsigned(db, world):
    with pytest.raises(Conflict):
        rx_svc.submit_mock_his(db, world.actor, make_rx(db, world), world.enc)


# ================================================================== medical record
def test_record_autoassembles_and_never_overwrites_doctor_text(db, world):
    rec = rec_svc.get_or_create(db, world.actor, world.enc); db.commit()
    cur = rec_svc.working_version(db, rec)
    assert cur.content_json["sections"]["chief_complaint"]["text"] == "头痛3天"
    assert "青霉素" in cur.content_json["sections"]["allergies"]["text"]
    # doctor writes the HPI by hand
    v, new = rec_svc.save_draft(db, world.actor, rec, world.enc, {"hpi": "医师手写：头痛伴眩晕3天，无呕吐"}, author_name="张医生", base_version_no=cur.version_no); db.commit()
    assert new and v.content_json["sections"]["hpi"]["origin"] == "doctor"
    # facts change + auto refresh → doctor's hpi preserved, suggestion offered, pristine auto sections updated
    enc_svc.update_facts(db, world.actor, world.enc, {"hpi": "患者新补充的现病史", "chief_complaint": "头痛5天"}, expected_version=world.enc.row_version); db.commit()
    res = rec_svc.refresh_auto_sections(db, world.actor, rec, world.enc); db.commit()
    secs = rec_svc.working_version(db, rec).content_json["sections"]
    assert secs["hpi"]["text"] == "医师手写：头痛伴眩晕3天，无呕吐" and "hpi" in res["suggestions"]
    assert secs["chief_complaint"]["text"] == "头痛5天" and "chief_complaint" in res["updated"]


def test_identical_save_creates_no_new_version_and_stale_base_rejected(db, world):
    rec = rec_svc.get_or_create(db, world.actor, world.enc); db.commit()
    cur = rec_svc.working_version(db, rec)
    _, new = rec_svc.save_draft(db, world.actor, rec, world.enc, {"hpi": cur.content_json["sections"]["hpi"]["text"]}, author_name="x", base_version_no=cur.version_no)
    assert not new
    rec_svc.save_draft(db, world.actor, rec, world.enc, {"hpi": "A"}, author_name="x", base_version_no=cur.version_no); db.commit()
    with pytest.raises(Conflict) as e:
        rec_svc.save_draft(db, world.actor, rec, world.enc, {"hpi": "B"}, author_name="x", base_version_no=cur.version_no)
    assert e.value.code == "stale_version"


def test_finalize_requires_diagnosis_signed_rx_and_plan_and_freezes_snapshot(db, world):
    rec = rec_svc.get_or_create(db, world.actor, world.enc); db.commit()
    with pytest.raises(Conflict) as e:
        rec_svc.finalize(db, world.actor, rec, world.enc, world.doc)
    assert {b["code"] for b in e.value.details["blockers"]} >= {"no_diagnosis"}
    d = dx(db, world, "高血压")
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["herbal"], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    rx = make_rx(db, world, plan_id=plan.id)
    with pytest.raises(Conflict) as e:
        rec_svc.finalize(db, world.actor, rec, world.enc, world.doc)
    assert any(b["code"] == "rx_unsigned" for b in e.value.details["blockers"])
    rx_svc.sign(db, world.actor, rx, world.enc, world.doc, expected_version=rx.row_version); db.commit()
    v = rec_svc.finalize(db, world.actor, rec, world.enc, world.doc); db.commit()
    assert rec.status == "finalized" and world.enc.status == "COMPLETED" and v.kind == "final"
    snap = v.snapshot_json
    assert snap["diagnoses"][0]["name"] == "高血压" and snap["prescriptions"][0]["no"] == rx.rx_no
    assert snap["patient"]["name"] == "王测试" and rec_svc.verify_version(v)
    assert rec_svc.finalize(db, world.actor, rec, world.enc, world.doc).id == v.id        # idempotent


def test_finalize_ignore_can_waive_only_soft_blockers(db, world):
    """`ignore` may waive an unverified-OCR / unconfirmed-plan warning, but never a hard blocker such as an unsigned Rx."""
    dx(db, world)
    rx = make_rx(db, world)                                                    # draft, unsigned → HARD blocker
    rec = rec_svc.get_or_create(db, world.actor, world.enc); db.commit()
    with pytest.raises(Conflict) as e:
        rec_svc.finalize(db, world.actor, rec, world.enc, world.doc, ignore=["rx_unsigned", "no_diagnosis"])
    assert any(b["code"] == "rx_unsigned" for b in e.value.details["blockers"])
    assert rec.status != "finalized" and world.enc.status != "COMPLETED"


def test_finalized_record_cannot_be_edited_only_amended_with_reason(db, world):
    dx(db, world)
    rec = rec_svc.get_or_create(db, world.actor, world.enc); db.commit()
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=[], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    v1 = rec_svc.finalize(db, world.actor, rec, world.enc, world.doc); db.commit()
    with pytest.raises(Conflict):
        rec_svc.save_draft(db, world.actor, rec, world.enc, {"hpi": "偷改"}, author_name="x", base_version_no=None)
    with pytest.raises(Unprocessable):
        rec_svc.amend(db, world.actor, rec, world.enc, world.doc, sections={"hpi": "更正"}, reason="短")
    v2 = rec_svc.amend(db, world.actor, rec, world.enc, world.doc, sections={"hpi": "更正后的现病史"}, reason="患者补充发病时间有误"); db.commit()
    assert v2.kind == "amendment" and v2.version_no == v1.version_no + 1 and rec.final_version_id == v2.id
    assert db.get(RecordVersion, v1.id).content_json["sections"]["hpi"]["text"] == "伴眩晕"      # v1 untouched
    assert rec_svc.verify_version(v1) and rec_svc.verify_version(v2)


def test_unverified_ocr_lab_blocks_finalize(db, world):
    dx(db, world)
    db.add(LabItem(encounter_id=world.enc.id, patient_id=world.patient.id, name="ALT", value_text="19.0", unit="U/L", source="ocr", verified=False)); db.commit()
    rec = rec_svc.get_or_create(db, world.actor, world.enc)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=[], ai_used=False)
    plan_svc.confirm_plan(db, world.actor, plan, world.enc, expected_version=plan.row_version); db.commit()
    with pytest.raises(Conflict) as e:
        rec_svc.finalize(db, world.actor, rec, world.enc, world.doc)
    assert any(b["code"] == "labs_unverified" for b in e.value.details["blockers"])
