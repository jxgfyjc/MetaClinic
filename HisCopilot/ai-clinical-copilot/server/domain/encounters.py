"""Patient & encounter services: registration, de-duplication, facts editing, change tracking.

The `inputs_hash` of an encounter covers every clinical fact an AI analysis or treatment plan may rely on
(chief complaint, history, vitals, exam, meds, allergies, special status, verified labs, confirmed uploads).
Whenever it changes, `inputs_version` is bumped: current analyses/plans that were built on an older version
are *reported stale* (never silently reused, never silently deleted).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from server.db.models import (Artifact, Diagnosis, Encounter, LabItem, Patient, PatientAllergy, TreatmentPlan)
from server.domain.enums import ArtifactKind, DxStatus, EncounterSource, EncounterStatus, PlanStatus, ProcStatus, VisitType
from server.domain.ids import hash_obj, utcnow
from server.domain.numbering import next_encounter_no, next_mrn
from server.errors import Conflict, NotFound, Unprocessable
from server.security import audit
from server.security.audit import Actor

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def normalize_phone(p: str) -> str:
    return re.sub(r"[\s\-+]", "", p or "").removeprefix("86") if (p or "").startswith(("+86", "86")) else re.sub(r"[\s\-]", "", p or "")


def split_list(s: str | list | None) -> list[str]:
    if isinstance(s, list):
        return [str(x).strip() for x in s if str(x).strip()]
    return [x.strip() for x in re.split(r"[、,，;；\n]+", s or "") if x.strip()]


def calc_age(p: Patient) -> int | None:
    if p.birth_date:
        t = date.today()
        b = p.birth_date
        return t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    return p.age_years


# --------------------------------------------------------------------------- patients
def find_duplicates(db: Session, *, name: str, phone: str = "", gender: str = "", age: int | None = None,
                    exclude_id: str | None = None) -> list[Patient]:
    """Likely-same-person candidates: same phone, or same name+gender with close age."""
    conds = []
    ph = normalize_phone(phone)
    if ph:
        conds.append(Patient.phone == ph)
    if name:
        conds.append((Patient.name == name.strip()))
    if not conds:
        return []
    q = select(Patient).where(or_(*conds), Patient.archived_at.is_(None))
    if exclude_id:
        q = q.where(Patient.id != exclude_id)
    out = []
    for p in db.scalars(q.limit(20)):
        same_phone = bool(ph) and p.phone == ph
        same_name = p.name == name.strip()
        age_ok = age is None or calc_age(p) is None or abs((calc_age(p) or 0) - age) <= 2
        gender_ok = not gender or not p.gender or p.gender == gender
        if same_phone and same_name:
            out.append(p)
        elif same_phone and gender_ok and age_ok:
            out.append(p)
        elif same_name and gender_ok and age_ok and age is not None and calc_age(p) is not None:
            out.append(p)
    return out


def create_patient(db: Session, actor: Actor, *, name: str, gender: str = "", age: int | None = None,
                   birth_date: date | None = None, phone: str = "", id_card: str = "", insurance: str = "",
                   address: str = "", allergies: list[str] | list[dict] | None = None,
                   chronic: list[str] | str | None = None, created_by: str = "", allow_duplicate: bool = False,
                   source: str = "patient") -> tuple[Patient, list[Patient]]:
    name = (name or "").strip()
    if not name:
        raise Unprocessable("姓名不能为空", code="name_required")
    if gender not in ("", "男", "女"):
        raise Unprocessable("性别只能为 男 / 女", code="bad_gender")
    if age is not None and not (0 <= age <= 130):
        raise Unprocessable("年龄不合理", code="bad_age")
    ph = normalize_phone(phone)
    if ph and not PHONE_RE.match(ph):
        raise Unprocessable("手机号格式不正确（需 11 位大陆手机号）", code="bad_phone")
    dups = find_duplicates(db, name=name, phone=ph, gender=gender, age=age)
    if dups and not allow_duplicate:
        # same phone + same name is the same person: reuse instead of creating a twin
        exact = [d for d in dups if ph and d.phone == ph and d.name == name]
        if exact:
            return exact[0], []
    p = Patient(mrn=next_mrn(db), name=name, gender=gender, age_years=age, birth_date=birth_date, phone=ph,
                id_card=id_card.strip(), insurance=insurance.strip(), address=address.strip(),
                chronic_conditions_json=split_list(chronic), created_by=created_by or actor.id,
                dup_of_candidates_json=[d.id for d in dups])
    db.add(p)
    db.flush()
    for a in _allergy_names(allergies):
        db.add(PatientAllergy(patient_id=p.id, allergen=a, source=source, confirmed=(source == "doctor")))
    audit.record(db, actor, "patient.create", entity_type="patient", entity_id=p.id, patient_id=p.id,
                 summary=f"建档 {p.name}（{p.mrn}）", detail={"duplicates": [d.id for d in dups], "source": source})
    return p, dups


def _allergy_names(allergies) -> list[str]:
    out: list[str] = []
    for a in allergies or []:
        n = (a.get("allergen") if isinstance(a, dict) else str(a)).strip()
        if n and n not in out and n not in ("无", "无过敏", "否"):
            out.append(n)
    return out


def sync_allergies(db: Session, patient: Patient, names: list[str], *, source: str, actor_id: str = "") -> bool:
    """Add missing allergens (never delete silently). Returns True if anything was added."""
    have = {a.allergen for a in patient.allergies if a.active}
    added = False
    for n in names:
        if n not in have:
            db.add(PatientAllergy(patient_id=patient.id, allergen=n, source=source, confirmed=(source == "doctor"),
                                  confirmed_by=actor_id if source == "doctor" else None))
            added = True
    if added:
        db.flush()
        db.refresh(patient)
    return added


def active_allergy_names(p: Patient) -> list[str]:
    return [a.allergen for a in p.allergies if a.active]


# --------------------------------------------------------------------------- encounter facts / change tracking
def facts_for_hash(db: Session, enc: Encounter) -> dict:
    labs = db.scalars(select(LabItem).where(LabItem.encounter_id == enc.id, LabItem.deleted_at.is_(None))
                      .order_by(LabItem.sort_order, LabItem.id)).all()
    arts = db.scalars(select(Artifact).where(Artifact.encounter_id == enc.id, Artifact.deleted_at.is_(None))
                      .order_by(Artifact.id)).all()
    return {
        "cc": enc.chief_complaint, "hpi": enc.hpi, "past": enc.past_history, "fam": enc.personal_family_history,
        "exam": enc.physical_exam, "sym": enc.symptoms_json, "meds": enc.current_meds_json, "vit": enc.vitals_json,
        "spec": enc.special_status_json, "tcm": enc.tcm_four_json,
        "allergies": sorted(active_allergy_names(enc.patient)),
        "gender": enc.patient.gender, "age": calc_age(enc.patient),
        # NOT `verified`: ticking "I checked this against the image" changes no clinical content, so it must not invalidate a
        # confirmed plan / analysis. Correcting a VALUE (name/value/unit/flag) does change the hash, as it should.
        "labs": [[l.name, l.value_text, l.unit, l.flag] for l in labs],
        "tongue": [a.sha256 for a in arts if a.kind == ArtifactKind.TONGUE_IMAGE.value],
        "voice": [(a.sha256, a.edited_text or a.result_text) for a in arts if a.kind == ArtifactKind.AUDIO.value
                  and a.proc_status == ProcStatus.SUCCEEDED.value],
    }


def refresh_inputs(db: Session, enc: Encounter) -> bool:
    """Recompute inputs_hash; bump inputs_version when facts changed. Returns True if changed."""
    h = hash_obj(facts_for_hash(db, enc))
    if h != enc.inputs_hash:
        first = not enc.inputs_hash
        enc.inputs_hash = h
        if not first:
            enc.inputs_version += 1
        enc.row_version += 1
        return not first
    return False


def create_encounter(db: Session, actor: Actor, *, patient: Patient, doctor_id: str | None,
                     source: str = EncounterSource.STAFF.value, visit_type: str = VisitType.INITIAL.value,
                     chief_complaint: str = "", hpi: str = "", symptoms: list[str] | None = None,
                     current_meds: list[dict] | None = None, past_history: str = "", created_by: str = "",
                     intake_id: str | None = None, appointment_id: str | None = None,
                     prev_encounter_id: str | None = None, patient_reported: dict | None = None) -> Encounter:
    if prev_encounter_id:
        prev = db.get(Encounter, prev_encounter_id)
        if prev is None or prev.patient_id != patient.id:
            raise Unprocessable("复诊关联的就诊不属于该患者", code="bad_prev_encounter")
        visit_type = VisitType.REVISIT.value if visit_type == VisitType.INITIAL.value else visit_type
    enc = Encounter(encounter_no=next_encounter_no(db), patient_id=patient.id, doctor_id=doctor_id,
                    status=EncounterStatus.WAITING.value, source=source, visit_type=visit_type,
                    prev_encounter_id=prev_encounter_id, appointment_id=appointment_id, intake_id=intake_id,
                    chief_complaint=(chief_complaint or "").strip(), hpi=(hpi or "").strip(),
                    past_history=past_history, symptoms_json=symptoms or [], current_meds_json=current_meds or [],
                    patient_reported_json=patient_reported or {}, created_by=created_by or actor.id)
    db.add(enc)
    db.flush()
    db.refresh(enc)
    refresh_inputs(db, enc)
    audit.record(db, actor, "encounter.create", entity_type="encounter", entity_id=enc.id, encounter_id=enc.id,
                 patient_id=patient.id, summary=f"新建就诊 {enc.encounter_no}（{source}）",
                 detail={"doctor_id": doctor_id, "visit_type": visit_type, "intake_id": intake_id})
    return enc


EDITABLE_FACTS = ("chief_complaint", "hpi", "past_history", "personal_family_history", "physical_exam",
                  "symptoms_json", "current_meds_json", "vitals_json", "special_status_json", "tcm_four_json")


def update_facts(db: Session, actor: Actor, enc: Encounter, changes: dict, *, expected_version: int | None) -> dict:
    """Patch clinical facts with optimistic locking. Returns {changed_fields, inputs_changed, stale}."""
    from server.deps import check_version
    if enc.status in (EncounterStatus.COMPLETED.value, EncounterStatus.CANCELLED.value):
        raise Conflict("就诊已结束，不能再修改临床事实（如需更正请对病历做更正版本）", code="encounter_closed")
    check_version(enc.row_version, expected_version, "就诊资料", db=db, obj=enc)
    changed = []
    for k, v in changes.items():
        if k not in EDITABLE_FACTS:
            continue
        if isinstance(v, str):
            v = v.strip()
        if getattr(enc, k) != v:
            setattr(enc, k, v)
            changed.append(k)
    inputs_changed = False
    if changed:
        inputs_changed = refresh_inputs(db, enc)
        audit.record(db, actor, "encounter.facts.update", entity_type="encounter", entity_id=enc.id,
                     encounter_id=enc.id, patient_id=enc.patient_id, summary="更新就诊临床资料：" + "、".join(changed),
                     detail={"fields": changed, "inputs_version": enc.inputs_version})
    return {"changed_fields": changed, "inputs_changed": inputs_changed, "inputs_version": enc.inputs_version}


def staleness(db: Session, enc: Encounter) -> dict:
    """What derived artefacts no longer match the encounter's current facts / diagnosis set."""
    from server.domain.plans import dx_set_hash, final_diagnoses
    from server.db.models import Analysis
    cur = db.scalar(select(Analysis).where(Analysis.encounter_id == enc.id, Analysis.is_current.is_(True))
                    .order_by(Analysis.created_at.desc()).limit(1))
    plan = db.scalar(select(TreatmentPlan).where(
        TreatmentPlan.encounter_id == enc.id,
        TreatmentPlan.status.in_([PlanStatus.DRAFT.value, PlanStatus.CONFIRMED.value, PlanStatus.GENERATING.value]))
        .order_by(TreatmentPlan.version_no.desc()).limit(1))
    dx_hash = dx_set_hash(final_diagnoses(db, enc.id))
    return {
        "analysis_stale": bool(cur and cur.status in ("succeeded", "partial") and cur.inputs_hash != enc.inputs_hash),
        "plan_stale_inputs": bool(plan and plan.status != PlanStatus.GENERATING.value and plan.inputs_hash != enc.inputs_hash),
        "plan_stale_diagnoses": bool(plan and plan.status != PlanStatus.GENERATING.value and plan.dx_set_hash != dx_hash),
        "inputs_version": enc.inputs_version,
    }


def unreviewed_candidates(db: Session, encounter_id: str) -> int:
    return db.scalar(select(func.count(Diagnosis.id)).where(
        Diagnosis.encounter_id == encounter_id, Diagnosis.status == DxStatus.PENDING.value)) or 0
