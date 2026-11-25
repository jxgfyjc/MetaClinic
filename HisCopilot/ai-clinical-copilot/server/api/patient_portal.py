"""Patient-facing endpoints. Authentication = scoped, hashed, expiring tokens in the `X-XY-Token` header.

What a patient can do here, and ONLY this:
  intake link  → learn the clinic/doctor name; start a private intake session
  intake session → autosave a draft, upload voice/report/tongue files, see processing status of THEIR files and
                   correct THEIR OWN transcript, submit once (idempotent)
  portal → answer follow-up questions, message the clinic, see doctor replies (+ the doctor-RELEASED care summary)
There is no endpoint here that returns diagnoses, AI output, plans, prescriptions, doctor notes or other patients.
Everything is rate-limited per IP/token and every action is audited as a patient actor.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from server.api import serializers as S
from server.config import get_settings
from server.db.base import get_db
from server.db.models import (AccessToken, Artifact, Encounter, FollowupMessage, FollowupTask, IntakeSession, Job, Patient, User)
from server.deps import Ctx, PatientActor, get_ctx, intake_link_actor, intake_session_actor, portal_actor
from server.domain import artifacts as art_svc
from server.domain import encounters as enc_svc
from server.domain import followup as fu_svc
from server.domain.enums import ArtifactKind, IntakeStatus, ProcStatus, TokenKind
from server.domain.ids import iso, utcnow
from server.errors import Conflict, NotFound, TooMany, Unprocessable
from server.jobs import engine as jobs
from server.security import audit
from server.security.audit import Actor
from server.security.auth import issue_token

router = APIRouter(prefix="/p", tags=["patient"])

from server.security.ratelimit import rate_limit  # noqa: E402


def _patient_actor(ctx: Ctx, patient_id: str | None, label: str = "患者") -> Actor:
    return Actor.patient(patient_id or "anonymous", ip=ctx.ip, request_id=ctx.request_id, label=label)


# ======================================================================= intake
@router.get("/intake/link")
def link_info(actor: PatientActor = Depends(intake_link_actor), db: Session = Depends(get_db)):
    doc = db.get(User, actor.token.doctor_id) if actor.token.doctor_id else None
    return {"clinic": get_settings().clinic_name, "doctor": ({"name": doc.display_name, "title": doc.title, "dept": doc.dept} if doc else None),
            "expires_at": iso(actor.token.expires_at)}


@router.post("/intake/sessions", status_code=201)
def start_session(request: Request, actor: PatientActor = Depends(intake_link_actor), db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    rate_limit(f"intake-start:{ctx.ip}", get_settings().intake_start_per_ip_hour, 3600)
    link = actor.token
    plain, tok = issue_token(db, kind=TokenKind.INTAKE_SESSION, ttl=timedelta(hours=12), doctor_id=link.doctor_id, parent_id=link.id, label="intake")
    sess = IntakeSession(link_token_id=link.id, session_token_id=tok.id, doctor_id=link.doctor_id, expires_at=utcnow() + timedelta(hours=12),
                         client_ip=ctx.ip, user_agent=ctx.user_agent)
    db.add(sess)
    link.use_count += 1
    link.last_used_at = utcnow()
    db.flush()
    audit.record(db, _patient_actor(ctx, None, "患者(匿名)"), "intake.start", entity_type="intake", entity_id=sess.id, summary="患者扫码开始问诊")
    db.commit()
    return {"token": plain, "intake_id": sess.id, "expires_at": iso(sess.expires_at)}


def _session(db: Session, actor: PatientActor) -> IntakeSession:
    s = db.scalar(select(IntakeSession).where(IntakeSession.session_token_id == actor.token.id))
    if s is None:
        raise NotFound("问诊会话不存在", code="intake_not_found")
    if s.status == IntakeStatus.SUBMITTED.value:
        return s
    if s.status != IntakeStatus.DRAFT.value or s.expires_at <= utcnow():
        raise Conflict("问诊会话已过期，请重新扫码", code="intake_expired")
    return s


class DraftIn(BaseModel):
    draft: dict


@router.get("/intake/draft")
def get_draft(actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db)):
    s = _session(db, actor)
    arts = db.scalars(select(Artifact).where(Artifact.intake_id == s.id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at)).all()
    return {"status": s.status, "draft": s.draft_json or {}, "files": [S.artifact_patient_out(a) for a in arts],
            "submitted": s.status == IntakeStatus.SUBMITTED.value, "encounter_no": _enc_no(db, s)}


def _enc_no(db: Session, s: IntakeSession) -> str | None:
    if s.encounter_id:
        e = db.get(Encounter, s.encounter_id)
        return e.encounter_no if e else None
    return None


ALLOWED_DRAFT_KEYS = {"name", "gender", "age", "phone", "insurance", "allergies", "no_allergies_confirmed", "chronic", "chief_complaint", "hpi", "symptoms", "current_meds", "step"}


@router.put("/intake/draft")
def save_draft(body: DraftIn, actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    s = _session(db, actor)
    if s.status == IntakeStatus.SUBMITTED.value:
        raise Conflict("问诊已提交，不能再修改", code="already_submitted")
    rate_limit(f"intake-draft:{actor.token.id}", 240, 3600)
    clean = {k: v for k, v in body.draft.items() if k in ALLOWED_DRAFT_KEYS}
    if len(str(clean)) > 20000:
        raise Unprocessable("内容过长", code="too_long")
    s.draft_json = {**(s.draft_json or {}), **clean}
    db.commit()
    return {"saved_at": iso(utcnow())}


@router.post("/intake/files", status_code=201)
async def upload_file(kind: str = Form(...), purpose: str = Form(""), file: UploadFile = File(...), actor: PatientActor = Depends(intake_session_actor),
                      db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    s = _session(db, actor)
    if s.status == IntakeStatus.SUBMITTED.value:
        raise Conflict("问诊已提交，不能再上传", code="already_submitted")
    if kind not in ("audio", "lab_image", "tongue_image"):
        raise Unprocessable("不支持的文件类别", code="bad_kind")
    rate_limit(f"intake-upload:{actor.token.id}", 40, 3600)
    count = len(db.scalars(select(Artifact.id).where(Artifact.intake_id == s.id, Artifact.deleted_at.is_(None))).all())
    if count >= 20:
        raise Unprocessable("上传文件数量已达上限（20）", code="too_many_files")
    data = await file.read(get_settings().max_audio_mb * 1024 * 1024 + 1)
    a, created = art_svc.store(db, kind=kind, data=data, original_name=file.filename or "", purpose=purpose,
                               owner={"intake_id": s.id, "by_kind": "patient", "by_id": s.id})
    job = None
    if created:
        jk = art_svc.processing_job_kind(kind)
        if jk and kind != "tongue_image":                    # tongue VLM analysis runs with the doctor-side analysis (GPU is single-flight)
            job = jobs.enqueue(jk, {"artifact_id": a.id}, subject_type="artifact", subject_id=a.id, idempotency_key=f"{jk}:{a.id}:0", created_by="patient",
                               priority=40, max_attempts=2, session=db)
            a.proc_job_id = job.id
        else:
            a.proc_status = ProcStatus.NOT_REQUIRED.value
        audit.record(db, _patient_actor(ctx, None), "intake.upload", entity_type="artifact", entity_id=a.id, summary=f"患者上传 {kind}", detail={"size": a.size_bytes})
    db.commit()
    return {"file": S.artifact_patient_out(a), "created": created}


@router.get("/intake/files/{aid}")
def file_status(aid: str, actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db)):
    s = db.scalar(select(IntakeSession).where(IntakeSession.session_token_id == actor.token.id))
    a = db.get(Artifact, aid)
    if not s or not a or a.intake_id != s.id or a.deleted_at:
        raise NotFound("文件不存在")
    return {"file": S.artifact_patient_out(a)}


class TranscriptIn(BaseModel):
    text: str = Field(max_length=4000)


@router.patch("/intake/files/{aid}/text")
def correct_transcript(aid: str, body: TranscriptIn, actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db)):
    """The patient may correct THEIR OWN voice transcript before submitting (the model's original is kept)."""
    s = _session(db, actor)
    a = db.get(Artifact, aid)
    if not a or a.intake_id != s.id or a.deleted_at or a.kind != "audio":
        raise NotFound("录音不存在")
    if s.status == IntakeStatus.SUBMITTED.value:
        raise Conflict("问诊已提交，不能再修改", code="already_submitted")
    if a.proc_status != ProcStatus.SUCCEEDED.value:
        raise Conflict("录音尚未转写成功", code="not_ready")
    a.edited_text = body.text.strip()
    db.commit()
    return {"file": S.artifact_patient_out(a)}


@router.post("/intake/files/{aid}/retry")
def retry_file(aid: str, actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db)):
    s = _session(db, actor)
    a = db.get(Artifact, aid)
    if not a or a.intake_id != s.id or a.deleted_at:
        raise NotFound("文件不存在")
    if a.proc_status != ProcStatus.FAILED.value:
        raise Conflict("该文件无需重试", code="not_failed")
    rate_limit(f"intake-retry:{actor.token.id}", 20, 3600)
    a.proc_status, a.error_code, a.error_detail = ProcStatus.PENDING.value, "", ""
    old = db.get(Job, a.proc_job_id) if a.proc_job_id else None
    if old is not None and old.status in ("failed", "cancelled"):
        db.commit()
        jobs.retry(old.id)
    else:
        jk = art_svc.processing_job_kind(a.kind)
        j = jobs.enqueue(jk, {"artifact_id": a.id}, subject_type="artifact", subject_id=a.id, idempotency_key=f"{jk}:{a.id}:{a.attempt_count}", created_by="patient", session=db)
        a.proc_job_id = j.id
        db.commit()
    return {"file": S.artifact_patient_out(a)}


@router.delete("/intake/files/{aid}")
def delete_file(aid: str, actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db)):
    s = _session(db, actor)
    a = db.get(Artifact, aid)
    if not a or a.intake_id != s.id or a.deleted_at:
        raise NotFound("文件不存在")
    if s.status == IntakeStatus.SUBMITTED.value:
        raise Conflict("问诊已提交，不能再修改", code="already_submitted")
    a.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


class SubmitIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    gender: str = ""
    age: int | None = Field(default=None, ge=0, le=130)
    phone: str = ""
    insurance: str = ""
    allergies: list[str] = []
    no_allergies_confirmed: bool = False
    chronic: list[str] = []
    chief_complaint: str = Field(min_length=2, max_length=500)
    hpi: str = Field(default="", max_length=4000)
    symptoms: list[str] = []
    current_meds: list[dict] = []
    consent: bool = False


@router.post("/intake/submit")
def submit(body: SubmitIn, actor: PatientActor = Depends(intake_session_actor), db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx),
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Single, idempotent submission: patient record + encounter + files attached + AI pre-analysis queued."""
    s = _session(db, actor)
    if s.status == IntakeStatus.SUBMITTED.value:                              # double click / retry → same result
        e = db.get(Encounter, s.encounter_id)
        return {"encounter_no": e.encounter_no, "already_submitted": True, "message": "您的资料已提交，请在候诊区等待医生叫号。"}
    if not body.consent:
        raise Unprocessable("请先阅读并同意个人信息使用说明", code="consent_required")
    if not body.allergies and not body.no_allergies_confirmed:
        raise Unprocessable("请填写过敏史；如确实没有，请勾选“无药物/食物过敏”", code="allergy_answer_required")
    pending = db.scalars(select(Artifact).where(Artifact.intake_id == s.id, Artifact.deleted_at.is_(None), Artifact.proc_status.in_(["pending", "processing"]))).all()
    # processing files do NOT block submission (doctor sees their live status); we only record them.
    rate_limit(f"intake-submit:{ctx.ip}", 10, 3600)
    # Atomic claim (compare-and-set): of N concurrent submits of the same session exactly one flips draft → submitted and goes on to
    # create the patient + encounter; the others wait on SQLite's write lock, find rowcount 0 and return the winner's result.
    claim = db.execute(update(IntakeSession).where(IntakeSession.id == s.id, IntakeSession.status == IntakeStatus.DRAFT.value)
                       .values(status=IntakeStatus.SUBMITTED.value, submitted_at=utcnow(), submit_key=idempotency_key or "")
                       .execution_options(synchronize_session=False))
    if claim.rowcount != 1:
        db.rollback()
        s = db.scalar(select(IntakeSession).where(IntakeSession.id == s.id).execution_options(populate_existing=True))
        e = db.get(Encounter, s.encounter_id) if s.encounter_id else None
        if e is None:                                                         # lost the race but the winner has not finished → ask to retry
            raise Conflict("您的资料正在提交中，请稍候几秒后刷新页面查看", code="submit_in_progress")
        return {"encounter_no": e.encounter_no, "already_submitted": True, "message": "您的资料已提交，请在候诊区等待医生叫号。"}
    patient_actor = _patient_actor(ctx, None)
    patient, dups = enc_svc.create_patient(db, patient_actor, name=body.name, gender=body.gender, age=body.age, phone=body.phone, insurance=body.insurance,
                                           allergies=body.allergies, chronic=body.chronic, source="patient", created_by="intake")
    if patient.allergies is not None and body.allergies:
        enc_svc.sync_allergies(db, patient, [a.strip() for a in body.allergies if a.strip()], source="patient")
    meds = [{"name": str(m.get("name", "")).strip(), "dose": str(m.get("dose", "")).strip(), "frequency": str(m.get("frequency", "")).strip()}
            for m in body.current_meds if str(m.get("name", "")).strip()]
    reported = {"submitted_at": iso(utcnow()), "name": body.name, "gender": body.gender, "age": body.age, "allergies": body.allergies,
                "no_allergies_confirmed": body.no_allergies_confirmed, "chief_complaint": body.chief_complaint, "hpi": body.hpi, "symptoms": body.symptoms}
    enc = enc_svc.create_encounter(db, patient_actor, patient=patient, doctor_id=s.doctor_id, source="patient_intake", chief_complaint=body.chief_complaint, hpi=body.hpi,
                                   symptoms=body.symptoms, current_meds=meds, intake_id=s.id, created_by="intake", patient_reported=reported)
    for a in db.scalars(select(Artifact).where(Artifact.intake_id == s.id, Artifact.deleted_at.is_(None))):
        a.encounter_id, a.patient_id = enc.id, patient.id
        if a.kind == "lab_image" and a.proc_status == ProcStatus.SUCCEEDED.value:
            from server.jobs.handlers import _replace_lab_items, parse_lab_text
            _replace_lab_items(db, a, parse_lab_text(a.result_text))
    db.flush()
    enc_svc.refresh_inputs(db, enc)
    s.status, s.patient_id, s.encounter_id, s.submitted_at, s.submit_key = IntakeStatus.SUBMITTED.value, patient.id, enc.id, utcnow(), idempotency_key or ""
    # OCR jobs finishing after submit: their handler writes lab items by artifact.encounter_id (now set), so nothing is lost.
    # Pre-analysis is queued only when there is enough text; otherwise the doctor starts it after collecting more.
    from server.db.models import Analysis
    an = Analysis(encounter_id=enc.id, status="queued", trigger="auto_intake", requested_by="system", inputs_version=enc.inputs_version, inputs_hash=enc.inputs_hash)
    db.add(an)
    db.flush()
    j = jobs.enqueue("analysis", {"analysis_id": an.id}, subject_type="analysis", subject_id=an.id, encounter_id=enc.id, idempotency_key=f"analysis:{an.id}", created_by="system",
                     priority=60, max_attempts=2, timeout_s=900, delay_s=2, session=db)
    an.job_id = j.id
    audit.record(db, Actor.patient(patient.id, ip=ctx.ip, request_id=ctx.request_id), "intake.submit", entity_type="encounter", entity_id=enc.id, encounter_id=enc.id,
                 patient_id=patient.id, summary=f"患者提交问诊 {enc.encounter_no}", detail={"files": len(pending), "duplicates": [d.id for d in dups]})
    db.commit()
    return {"encounter_no": enc.encounter_no, "already_submitted": False, "message": "提交成功，请在候诊区等待医生叫号。"}


# ======================================================================= portal (follow-up)
@router.get("/portal")
def portal_home(actor: PatientActor = Depends(portal_actor), db: Session = Depends(get_db)):
    pid = actor.token.patient_id
    p = db.get(Patient, pid)
    now = utcnow()
    tasks = db.scalars(select(FollowupTask).where(FollowupTask.patient_id == pid, FollowupTask.status == "scheduled", FollowupTask.due_at <= now + timedelta(days=1))
                       .order_by(FollowupTask.due_at)).all()
    msgs = db.scalars(select(FollowupMessage).where(FollowupMessage.patient_id == pid).order_by(FollowupMessage.created_at.desc()).limit(30)).all()
    released = db.scalars(select(Encounter).where(Encounter.patient_id == pid, Encounter.released_at.isnot(None)).order_by(Encounter.released_at.desc()).limit(3)).all()
    return {"clinic": get_settings().clinic_name, "patient": {"name": p.name},
            "tasks": [{"id": t.id, "due_at": iso(t.due_at), "kind": t.kind, "questions": t.questions_json or []} for t in tasks],
            "messages": [S.message_out(m) for m in reversed(msgs)],
            "care_summaries": [{"encounter_no": e.encounter_no, "date": iso(e.released_at), "summary": e.patient_summary_json or {}} for e in released]}


class RespondIn(BaseModel):
    answers: dict[str, str] = {}
    free_text: str = Field(default="", max_length=3000)
    symptom_trend: str = ""


@router.post("/portal/tasks/{tid}/respond")
def respond(tid: str, body: RespondIn, actor: PatientActor = Depends(portal_actor), db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx),
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    rate_limit(f"portal-respond:{actor.token.id}", 20, 3600)
    t = db.get(FollowupTask, tid)
    if not t or t.patient_id != actor.token.patient_id:                     # a token only ever reaches its own patient's tasks
        raise NotFound("随访任务不存在", code="task_not_found")
    answers = {**body.answers, **({"symptom_trend": body.symptom_trend} if body.symptom_trend else {})}
    resp, created, alert = fu_svc.submit_response(db, t, answers=answers, free_text=body.free_text, submit_key=idempotency_key or "")
    db.commit()
    msg = "已收到，医生会尽快查看。" if resp.risk_level == "normal" else "已收到。您的回答提示需要医生关注，诊所会尽快与您联系；如症状严重请立即前往急诊或拨打 120。"
    return {"ok": True, "created": created, "message": msg, "urgent": resp.risk_level == "high_risk"}


class PortalMsgIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.post("/portal/messages", status_code=201)
def portal_message(body: PortalMsgIn, actor: PatientActor = Depends(portal_actor), db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx),
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    rate_limit(f"portal-msg:{actor.token.id}", 30, 3600)
    pid = actor.token.patient_id
    p = db.get(Patient, pid)
    m, created = fu_svc.send_message(db, patient_id=pid, sender_kind="patient", sender_id=pid, sender_name=p.name, body=body.body, encounter_id=actor.token.encounter_id,
                                     client_key=idempotency_key or "")
    if created:
        audit.record(db, Actor.patient(pid, ip=ctx.ip, request_id=ctx.request_id), "followup.message", entity_type="message", entity_id=m.id, patient_id=pid, summary="患者发来消息")
    db.commit()
    return {"message": S.message_out(m), "created": created}
