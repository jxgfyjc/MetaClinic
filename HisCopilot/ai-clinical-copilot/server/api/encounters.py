"""Encounter workspace: queue, bundle, facts, artifacts, analysis, diagnoses, job status."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from server.api import serializers as S
from server.db.base import acquire_write_lock, get_db
from server.db.models import (Analysis, Artifact, Diagnosis, Encounter, Job, JobEvent, LabItem, Patient, Prescription, TreatmentPlan, User)
from server.deps import Ctx, Idempotent, actor_of, get_ctx, idem_header, load_encounter_for, require
from server.domain import artifacts as art_svc
from server.domain import encounters as enc_svc
from server.domain import plans as plan_svc
from server.domain.enums import (AnalysisStatus, ArtifactKind, EncounterStatus, JobStatus, ProcStatus)
from server.domain.ids import iso, utcnow
from server.errors import Conflict, Forbidden, NotFound, Unprocessable
from server.jobs import engine as jobs
from server.security import audit

router = APIRouter(tags=["encounters"])
OPEN = (EncounterStatus.WAITING.value, EncounterStatus.IN_PROGRESS.value, EncounterStatus.PENDING_CONFIRM.value)


# ----------------------------------------------------------------------- queue / list
@router.get("/encounters")
def list_encounters(scope: str = Query("today", pattern="^(today|open|mine|all|completed)$"), q: str = "", patient_id: str | None = None,
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
                    user: User = Depends(require("encounter.read"))):
    stmt = select(Encounter)
    if user.role == "DOCTOR" and scope != "all":
        stmt = stmt.where(or_(Encounter.doctor_id == user.id, Encounter.doctor_id.is_(None)))
    elif user.role == "DOCTOR":
        stmt = stmt.where(or_(Encounter.doctor_id == user.id, Encounter.doctor_id.is_(None)))
    if patient_id:
        stmt = stmt.where(Encounter.patient_id == patient_id)
    if scope == "today":
        start = datetime.combine(utcnow().date(), datetime.min.time()) - timedelta(hours=8)   # clinic day (UTC+8) boundary
        stmt = stmt.where(or_(Encounter.created_at >= start, Encounter.status.in_(OPEN)))
    elif scope == "open":
        stmt = stmt.where(Encounter.status.in_(OPEN))
    elif scope == "mine":
        stmt = stmt.where(Encounter.doctor_id == user.id)
    elif scope == "completed":
        stmt = stmt.where(Encounter.status == EncounterStatus.COMPLETED.value)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.join(Patient, Patient.id == Encounter.patient_id).where(or_(Patient.name.like(like), Patient.phone.like(like),
                                                                                  Patient.mrn.like(like), Encounter.encounter_no.like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Encounter.created_at.desc()).limit(limit).offset(offset)).all()
    ids = [e.id for e in rows]
    ana, pending_dx, rx_open, art_fail = {}, {}, {}, {}
    if ids:
        for eid, st in db.execute(select(Analysis.encounter_id, Analysis.status).where(Analysis.encounter_id.in_(ids), Analysis.is_current.is_(True))
                                  .order_by(Analysis.created_at)):
            ana[eid] = st
        for eid, n in db.execute(select(Diagnosis.encounter_id, func.count()).where(Diagnosis.encounter_id.in_(ids), Diagnosis.status == "pending")
                                 .group_by(Diagnosis.encounter_id)):
            pending_dx[eid] = n
        for eid, n in db.execute(select(Prescription.encounter_id, func.count()).where(Prescription.encounter_id.in_(ids),
                                 Prescription.status.in_(["draft", "returned"])).group_by(Prescription.encounter_id)):
            rx_open[eid] = n
        for eid, n in db.execute(select(Artifact.encounter_id, func.count()).where(Artifact.encounter_id.in_(ids), Artifact.proc_status == "failed",
                                 Artifact.deleted_at.is_(None)).group_by(Artifact.encounter_id)):
            art_fail[eid] = n
    items = [S.encounter_out(e, brief=True, extra={"analysis_status": ana.get(e.id), "pending_diagnoses": pending_dx.get(e.id, 0),
                                                    "open_prescriptions": rx_open.get(e.id, 0), "failed_artifacts": art_fail.get(e.id, 0)}) for e in rows]
    return {"items": items, "total": total}


class EncounterIn(BaseModel):
    patient_id: str
    doctor_id: str | None = None
    chief_complaint: str = ""
    hpi: str = ""
    visit_type: str = "initial"
    prev_encounter_id: str | None = None


@router.post("/encounters", status_code=201)
def create_encounter(body: EncounterIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("encounter.create")),
                     ctx: Ctx = Depends(get_ctx), idem_key: str | None = Depends(idem_header)):
    idem = Idempotent(db, request, actor_key=user.id, key=idem_key, body=body.model_dump())
    if idem.replay:
        return idem.replay[1]
    p = db.get(Patient, body.patient_id)
    if not p:
        idem.abort()
        raise NotFound("患者不存在", code="patient_not_found")
    doctor_id = body.doctor_id or (user.id if user.role == "DOCTOR" else None)
    if doctor_id and not db.scalar(select(User.id).where(User.id == doctor_id, User.role == "DOCTOR", User.active.is_(True))):
        idem.abort()
        raise Unprocessable("所选医师不存在", code="bad_doctor")
    # one open encounter per patient+doctor on the same day is almost always a double-click: reuse it unless asked otherwise
    acquire_write_lock(db)                       # serialise creators so each sees the previous one's committed visit (check-then-insert race)
    dup = db.scalar(select(Encounter).where(Encounter.patient_id == p.id, Encounter.doctor_id == doctor_id, Encounter.status.in_(OPEN),
                                            Encounter.created_at > utcnow() - timedelta(minutes=2), Encounter.source == "staff"))
    if dup:
        out = {"encounter": S.encounter_out(dup), "reused": True}
        idem.complete(200, out)
        return out
    e = enc_svc.create_encounter(db, actor_of(user, ctx), patient=p, doctor_id=doctor_id, source="staff", visit_type=body.visit_type,
                                 chief_complaint=body.chief_complaint, hpi=body.hpi, prev_encounter_id=body.prev_encounter_id, created_by=user.id)
    db.commit()
    out = {"encounter": S.encounter_out(e), "reused": False}
    idem.complete(201, out)
    return out


# ----------------------------------------------------------------------- workspace bundle
def _bundle(db: Session, e: Encounter, user: User) -> dict:
    arts = db.scalars(select(Artifact).where(Artifact.encounter_id == e.id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at)).all()
    labs = db.scalars(select(LabItem).where(LabItem.encounter_id == e.id, LabItem.deleted_at.is_(None)).order_by(LabItem.sort_order, LabItem.id)).all()
    ana = db.scalar(select(Analysis).where(Analysis.encounter_id == e.id, Analysis.is_current.is_(True)).order_by(Analysis.created_at.desc()).limit(1))
    dxs = db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == e.id, Diagnosis.status != "superseded").order_by(Diagnosis.sort_order)).all()
    plan = plan_svc.live_plan(db, e.id) or db.scalar(select(TreatmentPlan).where(TreatmentPlan.encounter_id == e.id, TreatmentPlan.status == "failed")
                                                    .order_by(TreatmentPlan.version_no.desc()).limit(1))
    rxs = db.scalars(select(Prescription).where(Prescription.encounter_id == e.id, Prescription.status != "voided").order_by(Prescription.created_at)).all()
    voided = db.scalar(select(func.count(Prescription.id)).where(Prescription.encounter_id == e.id, Prescription.status == "voided")) or 0
    stale = enc_svc.staleness(db, e)
    active_jobs = db.scalars(select(Job).where(Job.encounter_id == e.id, Job.status.in_(["queued", "running"]))).all()
    prev = db.scalar(select(Encounter).where(Encounter.patient_id == e.patient_id, Encounter.id != e.id, Encounter.created_at < e.created_at,
                                            Encounter.status == "COMPLETED").order_by(Encounter.created_at.desc()).limit(1))
    return {
        "encounter": S.encounter_out(e),
        "patient": S.patient_out(e.patient, detail=True),
        "artifacts": [S.artifact_out(a) for a in arts],
        "labs": [S.lab_out(l) for l in labs],
        "analysis": S.analysis_out(ana),
        "diagnoses": [S.diagnosis_out(d) for d in dxs],
        "plan": S.plan_out(plan, plan_svc.plan_staleness(db, plan, e) if plan else None),
        "prescriptions": [S.rx_out(r, brief=True) for r in rxs],
        "voided_prescriptions": voided,
        "staleness": stale,
        "active_jobs": [{"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.progress_msg} for j in active_jobs],
        "previous_encounter": ({"id": prev.id, "encounter_no": prev.encounter_no, "created_at": iso(prev.created_at),
                                "chief_complaint": prev.chief_complaint} if prev else None),
        "can": {"edit_facts": e.status not in ("COMPLETED", "CANCELLED"), "claim": e.doctor_id is None and user.role == "DOCTOR"},
    }


@router.get("/encounters/{eid}")
def get_encounter(eid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    e = load_encounter_for(db, user, eid)
    return _bundle(db, e, user)


@router.post("/encounters/{eid}/claim")
def claim(eid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.claim")), ctx: Ctx = Depends(get_ctx)):
    e = db.get(Encounter, eid)
    if not e:
        raise NotFound("就诊记录不存在", code="encounter_not_found")
    if e.doctor_id == user.id:
        return {"encounter": S.encounter_out(e)}
    if e.doctor_id:
        raise Conflict("该就诊已由其他医师接诊", code="already_claimed")
    from sqlalchemy import update
    res = db.execute(update(Encounter).where(Encounter.id == eid, Encounter.doctor_id.is_(None)).values(doctor_id=user.id, row_version=Encounter.row_version + 1))
    if res.rowcount != 1:                                      # lost the race
        db.rollback()
        raise Conflict("该就诊刚被其他医师接诊", code="already_claimed")
    db.refresh(e)
    audit.record(db, actor_of(user, ctx), "encounter.claim", entity_type="encounter", entity_id=eid, encounter_id=eid, patient_id=e.patient_id, summary="认领就诊")
    db.commit()
    return {"encounter": S.encounter_out(e)}


@router.post("/encounters/{eid}/start")
def start(eid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    if e.status == EncounterStatus.WAITING.value:
        if user.role == "DOCTOR" and not e.doctor_id:
            e.doctor_id = user.id
        e.status, e.started_at = EncounterStatus.IN_PROGRESS.value, utcnow()
        e.row_version += 1
        audit.record(db, actor_of(user, ctx), "encounter.start", entity_type="encounter", entity_id=eid, encounter_id=eid, patient_id=e.patient_id, summary="开始接诊")
        db.commit()
    return {"encounter": S.encounter_out(e)}


class CancelIn(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


@router.post("/encounters/{eid}/cancel")
def cancel_encounter(eid: str, body: CancelIn, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    if e.status == EncounterStatus.CANCELLED.value:
        return {"encounter": S.encounter_out(e)}
    if e.status == EncounterStatus.COMPLETED.value:
        raise Conflict("已完成的就诊不能取消", code="encounter_closed")
    live = db.scalar(select(func.count(Prescription.id)).where(Prescription.encounter_id == eid, Prescription.status.in_(
        ["signed", "pharmacy_review", "approved", "dispensed"]))) or 0
    if live:
        raise Conflict("该就诊已有已签署的处方，请先作废处方再取消就诊", code="has_signed_rx")
    e.status, e.cancel_reason = EncounterStatus.CANCELLED.value, body.reason
    e.row_version += 1
    audit.record(db, actor_of(user, ctx), "encounter.cancel", entity_type="encounter", entity_id=eid, encounter_id=eid, patient_id=e.patient_id, summary=f"取消就诊：{body.reason}")
    db.commit()
    return {"encounter": S.encounter_out(e)}


# ----------------------------------------------------------------------- facts
class FactsIn(BaseModel):
    expected_version: int | None = None
    chief_complaint: str | None = None
    hpi: str | None = None
    past_history: str | None = None
    personal_family_history: str | None = None
    physical_exam: str | None = None
    symptoms: list[str] | None = None
    current_meds: list[dict] | None = None
    vitals: dict | None = None
    special_status: dict | None = None
    tcm_four: dict | None = None


@router.patch("/encounters/{eid}/facts")
def patch_facts(eid: str, body: FactsIn, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    raw = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    mapping = {"symptoms": "symptoms_json", "current_meds": "current_meds_json", "vitals": "vitals_json", "special_status": "special_status_json",
               "tcm_four": "tcm_four_json"}
    changes = {mapping.get(k, k): v for k, v in raw.items()}
    if "special_status_json" in changes:
        allowed = {"pregnant", "lactating", "renal", "hepatic"}
        changes["special_status_json"] = {k: v for k, v in changes["special_status_json"].items() if k in allowed and (v is None or isinstance(v, bool))}
    if "current_meds_json" in changes:
        changes["current_meds_json"] = [{"name": str(m.get("name", "")).strip(), "dose": str(m.get("dose", "")).strip(), "frequency": str(m.get("frequency", "")).strip()}
                                        for m in changes["current_meds_json"] if str(m.get("name", "")).strip()]
    res = enc_svc.update_facts(db, actor_of(user, ctx), e, changes, expected_version=body.expected_version)
    db.commit()
    return {**res, "encounter": S.encounter_out(e), "staleness": enc_svc.staleness(db, e)}


# ----------------------------------------------------------------------- artifacts
def _enqueue_processing(db: Session, a: Artifact, user_id: str, e: Encounter | None) -> Job | None:
    kind = art_svc.processing_job_kind(a.kind)
    if not kind:
        a.proc_status = ProcStatus.NOT_REQUIRED.value
        return None
    j = jobs.enqueue(kind, {"artifact_id": a.id}, subject_type="artifact", subject_id=a.id, encounter_id=a.encounter_id,
                     idempotency_key=f"{kind}:{a.id}:{a.attempt_count}", created_by=user_id, max_attempts=2, session=db)
    a.proc_job_id = j.id
    return j


@router.post("/encounters/{eid}/artifacts", status_code=201)
async def upload_artifact(eid: str, kind: str = Form(...), purpose: str = Form(""), file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    if e.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("就诊已结束，不能再上传资料", code="encounter_closed")
    data = await file.read(art_svc.get_settings().max_audio_mb * 1024 * 1024 + 1)
    a, created = art_svc.store(db, kind=kind, data=data, original_name=file.filename or "", purpose=purpose,
                               owner={"encounter_id": e.id, "patient_id": e.patient_id, "by_kind": "staff", "by_id": user.id})
    job = _enqueue_processing(db, a, user.id, e) if created else None
    if created:
        enc_svc.refresh_inputs(db, e)
        audit.record(db, actor_of(user, ctx), "artifact.upload", entity_type="artifact", entity_id=a.id, encounter_id=e.id, patient_id=e.patient_id,
                     summary=f"上传{kind}", detail={"size": a.size_bytes, "sha256": a.sha256[:12], "job": job.id if job else None})
    db.commit()
    return {"artifact": S.artifact_out(a), "created": created, "job_id": job.id if job else a.proc_job_id}


@router.post("/artifacts/{aid}/retry")
def retry_artifact(aid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(Artifact, aid)
    if not a or a.deleted_at:
        raise NotFound("文件不存在", code="artifact_not_found")
    e = load_encounter_for(db, user, a.encounter_id, write=True) if a.encounter_id else None
    if a.proc_status != ProcStatus.FAILED.value:
        raise Conflict("只有处理失败的文件可以重试", code="not_failed")
    a.proc_status, a.error_code, a.error_detail = ProcStatus.PENDING.value, "", ""
    old = db.get(Job, a.proc_job_id) if a.proc_job_id else None
    if old is not None and old.status in ("failed", "cancelled"):
        db.commit()
        jobs.retry(old.id)
        j = old
    else:
        j = _enqueue_processing(db, a, user.id, e)
    audit.record(db, actor_of(user, ctx), "artifact.retry", entity_type="artifact", entity_id=a.id, encounter_id=a.encounter_id, patient_id=a.patient_id or "", summary="重试处理")
    db.commit()
    return {"artifact": S.artifact_out(a), "job_id": j.id if j else None}


class ArtifactTextIn(BaseModel):
    text: str = Field(max_length=6000)
    confirm: bool = True


@router.patch("/artifacts/{aid}/text")
def edit_transcript(aid: str, body: ArtifactTextIn, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    """Doctor corrects/confirms a voice transcript (the original ASR text is kept in result_text)."""
    a = db.get(Artifact, aid)
    if not a or a.deleted_at or a.kind != ArtifactKind.AUDIO.value or not a.encounter_id:
        raise NotFound("录音不存在")
    e = load_encounter_for(db, user, a.encounter_id, write=True)
    if a.proc_status != ProcStatus.SUCCEEDED.value:
        raise Conflict("录音尚未转写成功", code="not_ready")
    a.edited_text = body.text.strip()
    if body.confirm:
        a.confirmed_by, a.confirmed_at = user.id, utcnow()
    enc_svc.refresh_inputs(db, e)
    audit.record(db, actor_of(user, ctx), "artifact.transcript.confirm", entity_type="artifact", entity_id=a.id, encounter_id=e.id, patient_id=e.patient_id, summary="核对语音转写")
    db.commit()
    return {"artifact": S.artifact_out(a), "staleness": enc_svc.staleness(db, e)}


@router.delete("/artifacts/{aid}")
def delete_artifact(aid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(Artifact, aid)
    if not a or a.deleted_at:
        raise NotFound("文件不存在")
    e = load_encounter_for(db, user, a.encounter_id, write=True) if a.encounter_id else None
    a.deleted_at = utcnow()
    for l in db.scalars(select(LabItem).where(LabItem.artifact_id == a.id, LabItem.deleted_at.is_(None))):
        l.deleted_at = utcnow()
    if e:
        enc_svc.refresh_inputs(db, e)
    audit.record(db, actor_of(user, ctx), "artifact.delete", entity_type="artifact", entity_id=a.id, encounter_id=a.encounter_id or "", patient_id=a.patient_id or "", summary="删除资料文件")
    db.commit()
    return {"ok": True}


@router.get("/artifacts/{aid}/file")
def artifact_file(aid: str, thumb: bool = False, db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    a = db.get(Artifact, aid)
    if not a or a.deleted_at:
        raise NotFound("文件不存在")
    if a.encounter_id:
        load_encounter_for(db, user, a.encounter_id)
    p = art_svc.path_of(a, thumb=thumb)
    if not p.exists():
        raise NotFound("文件已被清理", code="file_missing")
    return FileResponse(str(p), media_type=a.mime if not (thumb and a.thumb_key) else "image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


# ----------------------------------------------------------------------- lab items
class LabIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=64)
    unit: str = ""
    ref: str = ""
    flag: str = ""


class LabPatch(BaseModel):
    name: str | None = None
    value: str | None = None
    unit: str | None = None
    ref: str | None = None
    flag: str | None = None
    verified: bool | None = None


def _lab_flag(value: str, ref: str, given: str) -> str:
    import re
    if given in ("H", "L", "N"):
        return given
    try:
        v = float(value)
    except ValueError:
        return ""
    m = re.match(r"^\s*([-+]?\d+(?:\.\d+)?)\s*[-~–]\s*([-+]?\d+(?:\.\d+)?)\s*$", ref or "")
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return "H" if v > hi else ("L" if v < lo else "N")
    return ""


@router.post("/encounters/{eid}/labs", status_code=201)
def add_lab(eid: str, body: LabIn, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    try:
        num = float(body.value)
    except ValueError:
        num = None
    l = LabItem(encounter_id=e.id, patient_id=e.patient_id, name=body.name.strip(), value_text=body.value.strip(), value_num=num, unit=body.unit, ref_text=body.ref,
                flag=_lab_flag(body.value, body.ref, body.flag), source="manual", verified=True, verified_by=user.id, verified_at=utcnow())
    db.add(l)
    db.flush()
    enc_svc.refresh_inputs(db, e)
    audit.record(db, actor_of(user, ctx), "lab.add", entity_type="lab", entity_id=l.id, encounter_id=e.id, patient_id=e.patient_id, summary=f"手工录入检验 {l.name}")
    db.commit()
    return {"lab": S.lab_out(l), "staleness": enc_svc.staleness(db, e)}


@router.patch("/labs/{lid}")
def patch_lab(lid: str, body: LabPatch, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    l = db.get(LabItem, lid)
    if not l or l.deleted_at:
        raise NotFound("检验项不存在")
    e = load_encounter_for(db, user, l.encounter_id, write=True)
    ch = body.model_dump(exclude_unset=True)
    if "name" in ch: l.name = ch["name"].strip()
    if "value" in ch:
        l.value_text = ch["value"].strip()
        try: l.value_num = float(l.value_text)
        except ValueError: l.value_num = None
    for k, col in (("unit", "unit"), ("ref", "ref_text")):
        if k in ch: setattr(l, col, ch[k])
    if {"value", "ref", "flag"} & set(ch):
        l.flag = _lab_flag(l.value_text, l.ref_text, ch.get("flag", ""))
    if ch.get("verified") is not None:
        l.verified, l.verified_by, l.verified_at = bool(ch["verified"]), user.id if ch["verified"] else None, utcnow() if ch["verified"] else None
    elif set(ch) - {"verified"}:
        l.verified, l.verified_by, l.verified_at = True, user.id, utcnow()        # a doctor's manual correction == verified
    enc_svc.refresh_inputs(db, e)
    audit.record(db, actor_of(user, ctx), "lab.edit", entity_type="lab", entity_id=l.id, encounter_id=e.id, patient_id=e.patient_id, summary=f"修改/核对检验 {l.name}")
    db.commit()
    return {"lab": S.lab_out(l), "staleness": enc_svc.staleness(db, e)}


@router.post("/encounters/{eid}/labs/verify-all")
def verify_all_labs(eid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    n = 0
    for l in db.scalars(select(LabItem).where(LabItem.encounter_id == eid, LabItem.deleted_at.is_(None), LabItem.verified.is_(False))):
        l.verified, l.verified_by, l.verified_at = True, user.id, utcnow()
        n += 1
    if n:
        enc_svc.refresh_inputs(db, e)
        audit.record(db, actor_of(user, ctx), "lab.verify_all", encounter_id=eid, patient_id=e.patient_id, entity_type="encounter", entity_id=eid, summary=f"核对全部 OCR 检验项（{n} 项）")
    db.commit()
    return {"verified": n}


@router.delete("/labs/{lid}")
def delete_lab(lid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    l = db.get(LabItem, lid)
    if not l or l.deleted_at:
        raise NotFound("检验项不存在")
    e = load_encounter_for(db, user, l.encounter_id, write=True)
    l.deleted_at = utcnow()
    enc_svc.refresh_inputs(db, e)
    audit.record(db, actor_of(user, ctx), "lab.delete", entity_type="lab", entity_id=l.id, encounter_id=e.id, patient_id=e.patient_id, summary=f"删除检验 {l.name}")
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- analysis
class AnalyzeIn(BaseModel):
    force: bool = False


@router.post("/encounters/{eid}/analysis", status_code=202)
def run_analysis(eid: str, body: AnalyzeIn, db: Session = Depends(get_db), user: User = Depends(require("analysis.run")), ctx: Ctx = Depends(get_ctx)):
    """Start (or reuse) an AI pre-analysis. Opening a visit NEVER re-runs the model; this endpoint only runs when
    there is no current analysis, the inputs changed since the last one, or the doctor explicitly forces it."""
    e = load_encounter_for(db, user, eid, write=True)
    if e.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("就诊已结束", code="encounter_closed")
    acquire_write_lock(db)                       # N simultaneous clicks must start ONE analysis: serialise, then re-read the current run
    cur = db.scalar(select(Analysis).where(Analysis.encounter_id == eid, Analysis.is_current.is_(True)).order_by(Analysis.created_at.desc()).limit(1))
    if cur and cur.status in (AnalysisStatus.QUEUED.value, AnalysisStatus.RUNNING.value):
        return {"analysis_id": cur.id, "job_id": cur.job_id, "reused": True, "reason": "已有分析正在进行"}
    if cur and cur.status in (AnalysisStatus.SUCCEEDED.value, AnalysisStatus.PARTIAL.value) and cur.inputs_hash == e.inputs_hash and not body.force:
        return {"analysis_id": cur.id, "job_id": cur.job_id, "reused": True, "reason": "临床资料未变化，沿用已保存的分析结果"}
    if cur:
        cur.is_current = False
    an = Analysis(encounter_id=eid, status=AnalysisStatus.QUEUED.value, trigger="reanalyze" if cur else "manual", requested_by=user.id,
                  inputs_version=e.inputs_version, inputs_hash=e.inputs_hash)
    db.add(an)
    db.flush()
    j = jobs.enqueue("analysis", {"analysis_id": an.id}, subject_type="analysis", subject_id=an.id, encounter_id=eid,
                     idempotency_key=f"analysis:{an.id}", created_by=user.id, max_attempts=2, timeout_s=900, session=db)
    an.job_id = j.id
    audit.record(db, actor_of(user, ctx), "analysis.start", entity_type="analysis", entity_id=an.id, encounter_id=eid, patient_id=e.patient_id,
                 summary="启动 AI 预分析" + ("（强制重新分析）" if body.force else ""), detail={"job": j.id})
    db.commit()
    return {"analysis_id": an.id, "job_id": j.id, "reused": False}


@router.get("/encounters/{eid}/analysis/history")
def analysis_history(eid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    load_encounter_for(db, user, eid)
    rows = db.scalars(select(Analysis).where(Analysis.encounter_id == eid).order_by(Analysis.created_at.desc()).limit(20)).all()
    return {"items": [{"id": a.id, "status": a.status, "created_at": iso(a.created_at), "is_current": a.is_current, "inputs_version": a.inputs_version,
                       "error_code": a.error_code or None} for a in rows]}


# ----------------------------------------------------------------------- diagnoses
class DxActionIn(BaseModel):
    action: str = Field(pattern="^(accept|modify|reject|revert|withdraw)$")
    name: str | None = None
    note: str | None = None
    icd_code: str | None = None
    primary: bool | None = None
    expected_version: int | None = None


class DxAddIn(BaseModel):
    module: str = Field(pattern="^(western|tcm)$")
    name: str = Field(min_length=1, max_length=200)
    icd_code: str = ""
    note: str = ""
    primary: bool = False


@router.post("/encounters/{eid}/diagnoses", status_code=201)
def add_diagnosis(eid: str, body: DxAddIn, db: Session = Depends(get_db), user: User = Depends(require("dx.review")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    d = plan_svc.add_doctor_diagnosis(db, actor_of(user, ctx), e, module=body.module, name=body.name, icd_code=body.icd_code, note=body.note, primary=body.primary)
    db.commit()
    return {"diagnosis": S.diagnosis_out(d), "staleness": enc_svc.staleness(db, e)}


@router.post("/diagnoses/{did}/review")
def review_dx(did: str, body: DxActionIn, db: Session = Depends(get_db), user: User = Depends(require("dx.review")), ctx: Ctx = Depends(get_ctx)):
    d = db.get(Diagnosis, did)
    if not d:
        raise NotFound("诊断不存在", code="dx_not_found")
    e = load_encounter_for(db, user, d.encounter_id, write=True)
    plan_svc.review_diagnosis(db, actor_of(user, ctx), d, action=body.action, name=body.name, note=body.note, icd_code=body.icd_code,
                              primary=body.primary, expected_version=body.expected_version)
    db.commit()
    return {"diagnosis": S.diagnosis_out(d), "staleness": enc_svc.staleness(db, e)}


# ----------------------------------------------------------------------- jobs
@router.get("/jobs/{jid}")
def job_status(jid: str, db: Session = Depends(get_db), user: User = Depends(require("job.read"))):
    j = db.get(Job, jid)
    if not j:
        raise NotFound("任务不存在", code="job_not_found")
    if j.encounter_id:
        load_encounter_for(db, user, j.encounter_id)
    evs = db.scalars(select(JobEvent).where(JobEvent.job_id == jid).order_by(JobEvent.id.desc()).limit(15)).all()
    return {"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.progress_msg, "attempts": j.attempts, "max_attempts": j.max_attempts,
            "error_code": j.error_code or None, "error_detail": j.error_detail or None, "created_at": iso(j.created_at), "started_at": iso(j.started_at),
            "finished_at": iso(j.finished_at), "events": [{"ts": iso(x.ts), "level": x.level, "message": x.message} for x in reversed(evs)]}


@router.post("/jobs/{jid}/retry")
def job_retry(jid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    j = db.get(Job, jid)
    if not j:
        raise NotFound("任务不存在")
    if j.encounter_id:
        load_encounter_for(db, user, j.encounter_id, write=True)
    r = jobs.retry(jid)
    if r is None:
        raise Conflict("只有失败或已取消的任务可以重试", code="not_retryable")
    audit.record(db, actor_of(user, ctx), "job.retry", entity_type="job", entity_id=jid, encounter_id=j.encounter_id or "", summary=f"重试任务 {j.kind}")
    db.commit()
    return {"id": jid, "status": "queued"}


@router.post("/jobs/{jid}/cancel")
def job_cancel(jid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.write_facts")), ctx: Ctx = Depends(get_ctx)):
    j = db.get(Job, jid)
    if not j:
        raise NotFound("任务不存在")
    if j.encounter_id:
        load_encounter_for(db, user, j.encounter_id, write=True)
    if not jobs.cancel(jid):
        raise Conflict("任务已结束，无法取消", code="not_cancellable")
    audit.record(db, actor_of(user, ctx), "job.cancel", entity_type="job", entity_id=jid, encounter_id=j.encounter_id or "", summary=f"取消任务 {j.kind}")
    db.commit()
    return {"id": jid, "cancel_requested": True}
