"""Patients + appointments + today's queue."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from server.api.serializers import appointment_out, encounter_out, patient_out
from server.db.base import get_db
from server.db.models import Appointment, Encounter, Patient, PatientAllergy, User
from server.deps import Ctx, actor_of, get_ctx, require
from server.domain import encounters as enc_svc
from server.domain.enums import ApptStatus, EncounterSource, EncounterStatus
from server.domain.ids import iso, utcnow
from server.errors import Conflict, Forbidden, NotFound, Unprocessable
from server.security import audit

router = APIRouter(tags=["patients"])


class AllergyIn(BaseModel):
    allergen: str = Field(min_length=1, max_length=128)
    reaction: str = ""
    severity: str = ""


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    gender: str = ""
    age: int | None = Field(default=None, ge=0, le=130)
    birth_date: date | None = None
    phone: str = ""
    id_card: str = ""
    insurance: str = ""
    address: str = ""
    allergies: list[AllergyIn] = []
    chronic_conditions: list[str] = []
    allow_duplicate: bool = False


class PatientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    gender: str | None = None
    age: int | None = Field(default=None, ge=0, le=130)
    birth_date: date | None = None
    phone: str | None = None
    id_card: str | None = None
    insurance: str | None = None
    address: str | None = None
    chronic_conditions: list[str] | None = None
    notes: str | None = None
    expected_version: int | None = None


@router.get("/patients")
def list_patients(q: str = "", limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
                  user: User = Depends(require("patient.read"))):
    stmt = select(Patient).where(Patient.archived_at.is_(None))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Patient.name.like(like), Patient.phone.like(like), Patient.mrn.like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)).all()
    last = {}
    if rows:
        for pid, cnt, latest in db.execute(select(Encounter.patient_id, func.count(Encounter.id), func.max(Encounter.created_at))
                                           .where(Encounter.patient_id.in_([p.id for p in rows])).group_by(Encounter.patient_id)):
            last[pid] = (cnt, latest)
    items = []
    for p in rows:
        d = patient_out(p)
        cnt, latest = last.get(p.id, (0, None))
        d["encounter_count"], d["last_visit_at"] = cnt, iso(latest)
        items.append(d)
    return {"items": items, "total": total}


@router.post("/patients", status_code=201)
def create_patient(body: PatientIn, db: Session = Depends(get_db), user: User = Depends(require("patient.write")), ctx: Ctx = Depends(get_ctx)):
    p, dups = enc_svc.create_patient(db, actor_of(user, ctx), name=body.name, gender=body.gender, age=body.age, birth_date=body.birth_date,
                                     phone=body.phone, id_card=body.id_card, insurance=body.insurance, address=body.address,
                                     allergies=[a.allergen for a in body.allergies], chronic=body.chronic_conditions,
                                     allow_duplicate=body.allow_duplicate, source="doctor", created_by=user.id)
    for a in body.allergies:
        for row in p.allergies:
            if row.allergen == a.allergen and (a.reaction or a.severity):
                row.reaction, row.severity = a.reaction, a.severity
    db.commit()
    return {"patient": patient_out(p, detail=True), "duplicates": [patient_out(d) for d in dups]}


@router.get("/patients/{pid}")
def get_patient(pid: str, db: Session = Depends(get_db), user: User = Depends(require("patient.read"))):
    p = db.get(Patient, pid)
    if not p:
        raise NotFound("患者不存在", code="patient_not_found")
    encs = db.scalars(select(Encounter).where(Encounter.patient_id == pid).order_by(Encounter.created_at.desc())).all()
    return {"patient": patient_out(p, detail=True), "encounters": [encounter_out(e, brief=True) for e in encs]}


@router.patch("/patients/{pid}")
def update_patient(pid: str, body: PatientPatch, db: Session = Depends(get_db), user: User = Depends(require("patient.write")), ctx: Ctx = Depends(get_ctx)):
    from server.deps import check_version
    p = db.get(Patient, pid)
    if not p:
        raise NotFound("患者不存在", code="patient_not_found")
    check_version(p.row_version, body.expected_version, "患者资料", db=db, obj=p)
    ch = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    if "gender" in ch and ch["gender"] not in ("", "男", "女"):
        raise Unprocessable("性别只能为 男 / 女", code="bad_gender")
    if "phone" in ch:
        ph = enc_svc.normalize_phone(ch["phone"] or "")
        if ph and not enc_svc.PHONE_RE.match(ph):
            raise Unprocessable("手机号格式不正确", code="bad_phone")
        ch["phone"] = ph
    if "age" in ch:
        ch["age_years"] = ch.pop("age")
    if "chronic_conditions" in ch:
        ch["chronic_conditions_json"] = ch.pop("chronic_conditions")
    for k, v in ch.items():
        setattr(p, k, v)
    p.row_version += 1
    audit.record(db, actor_of(user, ctx), "patient.update", entity_type="patient", entity_id=p.id, patient_id=p.id,
                 summary="修改患者资料", detail={"fields": sorted(ch)})
    db.commit()
    return {"patient": patient_out(p, detail=True)}


class AllergyConfirmIn(BaseModel):
    allergen: str = Field(min_length=1, max_length=128)
    reaction: str = ""
    severity: str = ""


@router.post("/patients/{pid}/allergies", status_code=201)
def add_allergy(pid: str, body: AllergyConfirmIn, db: Session = Depends(get_db), user: User = Depends(require("patient.write")), ctx: Ctx = Depends(get_ctx)):
    p = db.get(Patient, pid)
    if not p:
        raise NotFound("患者不存在", code="patient_not_found")
    ex = next((a for a in p.allergies if a.allergen == body.allergen), None)
    if ex:
        ex.active, ex.confirmed, ex.confirmed_by = True, True, user.id
        ex.reaction, ex.severity = body.reaction or ex.reaction, body.severity or ex.severity
    else:
        db.add(PatientAllergy(patient_id=pid, allergen=body.allergen.strip(), reaction=body.reaction, severity=body.severity, source="doctor",
                              confirmed=True, confirmed_by=user.id))
    db.flush()
    db.refresh(p)
    for e in db.scalars(select(Encounter).where(Encounter.patient_id == pid, Encounter.status.in_(["WAITING", "IN_PROGRESS", "PENDING_CONFIRM"]))):
        enc_svc.refresh_inputs(db, e)                         # open encounters' analyses/plans become stale: safety-relevant
    audit.record(db, actor_of(user, ctx), "patient.allergy.add", entity_type="patient", entity_id=pid, patient_id=pid, summary=f"登记过敏：{body.allergen}")
    db.commit()
    return {"patient": patient_out(p, detail=True)}


@router.delete("/patients/{pid}/allergies/{aid}")
def remove_allergy(pid: str, aid: str, db: Session = Depends(get_db), user: User = Depends(require("patient.write")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(PatientAllergy, aid)
    if not a or a.patient_id != pid:
        raise NotFound("过敏记录不存在")
    a.active = False                                          # soft: history stays auditable
    p = db.get(Patient, pid)
    db.flush()
    db.refresh(p)
    for e in db.scalars(select(Encounter).where(Encounter.patient_id == pid, Encounter.status.in_(["WAITING", "IN_PROGRESS", "PENDING_CONFIRM"]))):
        enc_svc.refresh_inputs(db, e)
    audit.record(db, actor_of(user, ctx), "patient.allergy.remove", entity_type="patient", entity_id=pid, patient_id=pid, summary=f"移除过敏：{a.allergen}")
    db.commit()
    return {"patient": patient_out(p, detail=True)}


@router.post("/patients/{pid}/merge")
def merge_patients(pid: str, into: str = Query(...), db: Session = Depends(get_db), user: User = Depends(require("patient.merge")), ctx: Ctx = Depends(get_ctx)):
    """Merge duplicate `pid` INTO `into` (admin). Moves all clinical rows, keeps `pid` archived for audit."""
    src, dst = db.get(Patient, pid), db.get(Patient, into)
    if not src or not dst or src.id == dst.id:
        raise NotFound("患者不存在或相同", code="patient_not_found")
    from server.db.models import (AccessToken, Artifact, FollowupAlert, FollowupMessage, FollowupPlan, FollowupResponse, FollowupTask, LabItem,
                                  MedicalRecord, Prescription)
    for model in (Encounter, Prescription, MedicalRecord, LabItem, FollowupPlan, FollowupTask, FollowupResponse, FollowupMessage, FollowupAlert, Artifact,
                  Appointment, AccessToken):
        for row in db.scalars(select(model).where(model.patient_id == src.id)):
            row.patient_id = dst.id
    have = {a.allergen for a in dst.allergies}
    for a in src.allergies:
        if a.allergen not in have:
            db.add(PatientAllergy(patient_id=dst.id, allergen=a.allergen, reaction=a.reaction, severity=a.severity, source=a.source, confirmed=a.confirmed))
    src.archived_at, src.notes = utcnow(), f"已合并到 {dst.mrn}（{dst.id}）"
    audit.record(db, actor_of(user, ctx), "patient.merge", entity_type="patient", entity_id=src.id, patient_id=dst.id, summary=f"合并患者 {src.mrn} → {dst.mrn}")
    db.commit()
    return {"patient": patient_out(dst, detail=True)}


# ----------------------------------------------------------------------- appointments
CLINIC_UTC_OFFSET = timedelta(hours=8)            # the clinic runs on China time; the DB stores naive UTC


class ApptIn(BaseModel):
    patient_id: str | None = None
    guest_name: str = ""
    guest_phone: str = ""
    doctor_id: str
    slot_start: datetime

    @field_validator("slot_start")
    @classmethod
    def _to_naive_utc(cls, v: datetime) -> datetime:
        """Accept aware ("…Z" / "+08:00") or naive (treated as UTC, like every stored datetime) and normalise —
        comparing an aware value with the naive utcnow() used to raise TypeError → HTTP 500."""
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    duration_min: int = Field(20, ge=5, le=240)
    reason: str = ""
    kind: str = "visit"
    source_encounter_id: str | None = None


def _overlaps(db: Session, doctor_id: str, start: datetime, end: datetime, exclude: str | None = None) -> Appointment | None:
    q = select(Appointment).where(Appointment.doctor_id == doctor_id, Appointment.status.in_(["booked", "checked_in"]),
                                  Appointment.slot_start < end, Appointment.slot_end > start)
    if exclude:
        q = q.where(Appointment.id != exclude)
    return db.scalar(q.limit(1))


@router.get("/appointments")
def list_appointments(day: date | None = None, doctor_id: str | None = None, status: str | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require("appointment.manage"))):
    stmt = select(Appointment)
    if day:                                                   # `day` is a clinic-local calendar day → UTC window
        lo = datetime.combine(day, datetime.min.time()) - CLINIC_UTC_OFFSET
        stmt = stmt.where(Appointment.slot_start >= lo, Appointment.slot_start < lo + timedelta(days=1))
    if user.role == "DOCTOR":
        doctor_id = user.id
    if doctor_id:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if status:
        stmt = stmt.where(Appointment.status == status)
    rows = db.scalars(stmt.order_by(Appointment.slot_start).limit(300)).all()
    names = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_({a.doctor_id for a in rows}) if rows else User.id == ""))}
    pats = {p.id: p.name for p in db.scalars(select(Patient).where(Patient.id.in_({a.patient_id for a in rows if a.patient_id}) if rows else Patient.id == ""))}
    return {"items": [appointment_out(a, names.get(a.doctor_id, ""), pats.get(a.patient_id or "", "")) for a in rows]}


@router.post("/appointments", status_code=201)
def create_appointment(body: ApptIn, db: Session = Depends(get_db), user: User = Depends(require("appointment.manage")), ctx: Ctx = Depends(get_ctx)):
    doc = db.get(User, body.doctor_id)
    if not doc or doc.role != "DOCTOR" or not doc.active:
        raise Unprocessable("所选医师不存在或不可预约", code="bad_doctor")
    if body.patient_id:
        if not db.get(Patient, body.patient_id):
            raise NotFound("患者不存在", code="patient_not_found")
    elif not body.guest_name.strip():
        raise Unprocessable("请选择已有患者，或填写预约人姓名", code="patient_required")
    end = body.slot_start + timedelta(minutes=body.duration_min)
    if body.slot_start < utcnow() - timedelta(minutes=5):
        raise Unprocessable("不能预约过去的时间", code="past_slot")
    clash = _overlaps(db, body.doctor_id, body.slot_start, end)
    if clash:
        raise Conflict("该医师该时段已有预约", code="slot_taken", details={"existing": clash.id})
    a = Appointment(patient_id=body.patient_id, guest_name=body.guest_name.strip(), guest_phone=enc_svc.normalize_phone(body.guest_phone),
                    doctor_id=body.doctor_id, slot_start=body.slot_start, slot_end=end, reason=body.reason.strip(), kind=body.kind,
                    source_encounter_id=body.source_encounter_id, created_by=user.id)
    db.add(a)
    db.flush()
    audit.record(db, actor_of(user, ctx), "appointment.create", entity_type="appointment", entity_id=a.id, patient_id=a.patient_id or "",
                 summary=f"预约 {doc.display_name} {a.slot_start:%m-%d %H:%M}")
    db.commit()
    return appointment_out(a, doc.display_name)


@router.post("/appointments/{aid}/cancel")
def cancel_appointment(aid: str, reason: str = "", db: Session = Depends(get_db), user: User = Depends(require("appointment.manage")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(Appointment, aid)
    if not a:
        raise NotFound("预约不存在")
    if a.status == ApptStatus.CANCELLED.value:
        return appointment_out(a)
    if a.status not in (ApptStatus.BOOKED.value,):
        raise Conflict("只有“已预约、未到诊”的预约可以取消", code="bad_transition")
    a.status, a.cancelled_reason = ApptStatus.CANCELLED.value, reason[:200]
    audit.record(db, actor_of(user, ctx), "appointment.cancel", entity_type="appointment", entity_id=a.id, summary="取消预约")
    db.commit()
    return appointment_out(a)


class CheckInIn(BaseModel):
    chief_complaint: str = ""
    patient: PatientIn | None = None


@router.post("/appointments/{aid}/check-in")
def check_in(aid: str, body: CheckInIn, db: Session = Depends(get_db), user: User = Depends(require("encounter.create")), ctx: Ctx = Depends(get_ctx),
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """到诊登记：预约 → 就诊（待接诊）。重复点击返回同一个就诊（幂等）。"""
    a = db.get(Appointment, aid)
    if not a:
        raise NotFound("预约不存在")
    if a.status == ApptStatus.CHECKED_IN.value and a.encounter_id:
        e = db.get(Encounter, a.encounter_id)
        return {"encounter": encounter_out(e), "reused": True}
    if a.status != ApptStatus.BOOKED.value:
        raise Conflict("该预约不可登记到诊", code="bad_transition")
    patient = db.get(Patient, a.patient_id) if a.patient_id else None
    actor = actor_of(user, ctx)
    if patient is None:
        if body.patient is None and not a.guest_name:
            raise Unprocessable("该预约尚未建档，请先补充患者资料", code="patient_required")
        src = body.patient
        patient, _ = enc_svc.create_patient(db, actor, name=(src.name if src else a.guest_name), gender=(src.gender if src else ""),
                                            age=(src.age if src else None), phone=(src.phone if src else a.guest_phone), source="doctor",
                                            allergies=[x.allergen for x in (src.allergies if src else [])], created_by=user.id)
        a.patient_id = patient.id
    enc = enc_svc.create_encounter(db, actor, patient=patient, doctor_id=a.doctor_id, source=EncounterSource.APPOINTMENT.value,
                                   chief_complaint=body.chief_complaint or a.reason, appointment_id=a.id, created_by=user.id,
                                   prev_encounter_id=a.source_encounter_id if a.kind == "followup_visit" else None,
                                   visit_type="followup_visit" if a.kind == "followup_visit" else "initial")
    a.status, a.encounter_id = ApptStatus.CHECKED_IN.value, enc.id
    audit.record(db, actor, "appointment.check_in", entity_type="appointment", entity_id=a.id, encounter_id=enc.id, patient_id=patient.id,
                 summary="预约到诊登记")
    db.commit()
    return {"encounter": encounter_out(enc), "reused": False}


@router.get("/doctors")
def list_doctors(db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    return {"items": [{"id": u.id, "name": u.display_name, "dept": u.dept, "title": u.title}
                      for u in db.scalars(select(User).where(User.role == "DOCTOR", User.active.is_(True)).order_by(User.username))]}
