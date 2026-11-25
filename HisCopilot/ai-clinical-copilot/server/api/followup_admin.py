"""Follow-up (staff side), intake links / QR, patient portal links, admin: audit, statistics, system status, jobs."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from server.api import serializers as S
from server.config import get_settings
from server.db.base import get_db
from server.db.models import (AccessToken, Analysis, AuditEvent, Encounter, FollowupAlert, FollowupMessage, FollowupPlan, FollowupResponse, FollowupTask, Job,
                              Patient, Prescription, User)
from server.deps import Ctx, actor_of, get_ctx, idem_header, load_encounter_for, require
from server.domain import followup as fu
from server.domain.enums import FuPlanStatus, FuTaskStatus, TokenKind
from server.domain.ids import iso, utcnow
from server.errors import Conflict, NotFound, Unprocessable
from server.security import audit
from server.security.auth import issue_token, revoke_token

router = APIRouter(tags=["followup-admin"])


def _public_base(request: Request) -> str:
    s = get_settings()
    if s.public_base_url.strip():
        return s.public_base_url.strip().rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{proto}://{host}"


# ======================================================================= intake links (QR)
class IntakeLinkIn(BaseModel):
    hours: int = Field(24, ge=1, le=168)
    max_uses: int = Field(0, ge=0, le=1000)
    label: str = ""


@router.post("/intake-links", status_code=201)
def create_intake_link(body: IntakeLinkIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("intake.link")), ctx: Ctx = Depends(get_ctx)):
    """A QR/link patients scan to start a private intake session bound to this doctor. Shown ONCE (only a hash is stored)."""
    doctor_id = user.id if user.role == "DOCTOR" else None
    plain, row = issue_token(db, kind=TokenKind.INTAKE_LINK, ttl=timedelta(hours=body.hours), doctor_id=doctor_id, max_uses=body.max_uses, label=body.label or "二维码",
                             created_by=user.id)
    audit.record(db, actor_of(user, ctx), "intake_link.create", entity_type="access_token", entity_id=row.id, summary="生成问诊二维码")
    db.commit()
    return {"id": row.id, "url": f"{_public_base(request)}/p/intake#t={plain}", "expires_at": iso(row.expires_at), "doctor_id": doctor_id}


@router.get("/intake-links")
def list_intake_links(db: Session = Depends(get_db), user: User = Depends(require("intake.link"))):
    q = select(AccessToken).where(AccessToken.kind == TokenKind.INTAKE_LINK.value)
    if user.role == "DOCTOR":
        q = q.where(AccessToken.doctor_id == user.id)
    now = utcnow()
    return {"items": [{"id": t.id, "label": t.label, "expires_at": iso(t.expires_at), "use_count": t.use_count, "revoked": bool(t.revoked_at),
                       "active": not t.revoked_at and t.expires_at > now} for t in db.scalars(q.order_by(AccessToken.created_at.desc()).limit(30))]}


@router.delete("/intake-links/{tid}")
def revoke_intake_link(tid: str, db: Session = Depends(get_db), user: User = Depends(require("intake.link")), ctx: Ctx = Depends(get_ctx)):
    t = db.get(AccessToken, tid)
    if not t or t.kind != TokenKind.INTAKE_LINK.value or (user.role == "DOCTOR" and t.doctor_id != user.id):
        raise NotFound("二维码不存在")
    revoke_token(db, tid)
    audit.record(db, actor_of(user, ctx), "intake_link.revoke", entity_type="access_token", entity_id=tid, summary="作废问诊二维码")
    db.commit()
    return {"ok": True}


# ======================================================================= follow-up plans / tasks
class FuProposeIn(BaseModel):
    schedule: list[dict] | None = None
    revisit_advice: str = ""


@router.post("/encounters/{eid}/followup-plan", status_code=201)
def propose_followup(eid: str, body: FuProposeIn, db: Session = Depends(get_db), user: User = Depends(require("followup.write")), ctx: Ctx = Depends(get_ctx)):
    e = load_encounter_for(db, user, eid, write=True)
    plan = fu.propose_plan(db, actor_of(user, ctx), e, schedule=body.schedule, revisit_advice=body.revisit_advice)
    db.commit()
    return {"plan": _plan_out(plan)}


def _plan_out(p: FollowupPlan) -> dict:
    return {"id": p.id, "encounter_id": p.encounter_id, "status": p.status, "schedule": p.schedule_json, "revisit_advice": p.revisit_advice,
            "tasks": [S.followup_task_out(t) | {"due_state": fu.due_state(t)} for t in p.tasks]}


@router.get("/encounters/{eid}/followup-plan")
def get_followup(eid: str, db: Session = Depends(get_db), user: User = Depends(require("followup.read"))):
    load_encounter_for(db, user, eid)
    p = db.scalar(select(FollowupPlan).where(FollowupPlan.encounter_id == eid, FollowupPlan.status != "cancelled").order_by(FollowupPlan.created_at.desc()).limit(1))
    return {"plan": _plan_out(p) if p else None}


class FuActivateIn(BaseModel):
    schedule: list[dict] | None = None


@router.post("/followup-plans/{pid}/activate")
def activate_followup(pid: str, body: FuActivateIn, db: Session = Depends(get_db), user: User = Depends(require("followup.write")), ctx: Ctx = Depends(get_ctx)):
    p = db.get(FollowupPlan, pid)
    if not p:
        raise NotFound("随访计划不存在")
    load_encounter_for(db, user, p.encounter_id, write=True)
    fu.activate_plan(db, actor_of(user, ctx), p, schedule=body.schedule)
    db.commit()
    db.refresh(p)
    return {"plan": _plan_out(p)}


@router.post("/followup-plans/{pid}/cancel")
def cancel_followup(pid: str, db: Session = Depends(get_db), user: User = Depends(require("followup.write")), ctx: Ctx = Depends(get_ctx)):
    p = db.get(FollowupPlan, pid)
    if not p:
        raise NotFound("随访计划不存在")
    load_encounter_for(db, user, p.encounter_id, write=True)
    p.status = FuPlanStatus.CANCELLED.value
    for t in p.tasks:
        if t.status == FuTaskStatus.SCHEDULED.value:
            t.status = FuTaskStatus.CANCELLED.value
    audit.record(db, actor_of(user, ctx), "followup.cancel", entity_type="followup_plan", entity_id=pid, encounter_id=p.encounter_id, patient_id=p.patient_id, summary="取消随访计划")
    db.commit()
    return {"ok": True}


@router.post("/followup-tasks/{tid}/portal-link", status_code=201)
def portal_link(tid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("followup.write")), ctx: Ctx = Depends(get_ctx)):
    """Link the patient opens to answer follow-up questions / message the clinic (no login; scoped to this patient)."""
    t = db.get(FollowupTask, tid)
    if not t:
        raise NotFound("随访任务不存在")
    load_encounter_for(db, user, t.encounter_id, write=True)
    plain, row = issue_token(db, kind=TokenKind.PORTAL, ttl=timedelta(days=30), patient_id=t.patient_id, encounter_id=t.encounter_id, doctor_id=user.id,
                             label="随访入口", created_by=user.id)
    audit.record(db, actor_of(user, ctx), "portal_link.create", entity_type="access_token", entity_id=row.id, patient_id=t.patient_id, encounter_id=t.encounter_id, summary="生成患者随访入口链接")
    db.commit()
    return {"url": f"{_public_base(request)}/p/portal#t={plain}", "expires_at": iso(row.expires_at)}


@router.get("/followup/inbox")
def followup_inbox(db: Session = Depends(get_db), user: User = Depends(require("followup.read"))):
    """Doctor's follow-up worklist: due/overdue tasks, answered-awaiting-review, open alerts, unread patient messages."""
    now = utcnow()
    tq = select(FollowupTask, Patient.name).join(Patient, Patient.id == FollowupTask.patient_id)
    aq = select(FollowupAlert, Patient.name).join(Patient, Patient.id == FollowupAlert.patient_id).where(FollowupAlert.status == "open")
    if user.role == "DOCTOR":
        tq, aq = tq.where(FollowupTask.doctor_id == user.id), aq.where(FollowupAlert.doctor_id == user.id)
    due = db.execute(tq.where(FollowupTask.status == "scheduled", FollowupTask.due_at <= now + timedelta(days=1)).order_by(FollowupTask.due_at).limit(100)).all()
    answered = db.execute(tq.where(FollowupTask.status == "answered").order_by(FollowupTask.answered_at.desc()).limit(100)).all()
    alerts = db.execute(aq.order_by(FollowupAlert.level.desc(), FollowupAlert.created_at.desc()).limit(100)).all()
    mq = select(FollowupMessage, Patient.name).join(Patient, Patient.id == FollowupMessage.patient_id).where(FollowupMessage.sender_kind == "patient", FollowupMessage.read_at.is_(None))
    msgs = db.execute(mq.order_by(FollowupMessage.created_at.desc()).limit(50)).all()
    return {"due": [S.followup_task_out(t, n) | {"due_state": fu.due_state(t)} for t, n in due],
            "answered": [S.followup_task_out(t, n) for t, n in answered],
            "alerts": [{"id": a.id, "level": a.level, "patient_id": a.patient_id, "patient_name": n, "encounter_id": a.encounter_id, "rules": a.rules_json, "created_at": iso(a.created_at),
                        "response_id": a.response_id} for a, n in alerts],
            "unread_messages": [S.message_out(m) | {"patient_id": m.patient_id, "patient_name": n} for m, n in msgs],
            "counts": {"due": len(due), "answered": len(answered), "alerts": len(alerts), "messages": len(msgs)}}


@router.get("/followup-tasks/{tid}")
def get_task(tid: str, db: Session = Depends(get_db), user: User = Depends(require("followup.read"))):
    t = db.get(FollowupTask, tid)
    if not t:
        raise NotFound("随访任务不存在")
    load_encounter_for(db, user, t.encounter_id)
    resps = db.scalars(select(FollowupResponse).where(FollowupResponse.task_id == tid).order_by(FollowupResponse.submitted_at)).all()
    alerts = db.scalars(select(FollowupAlert).where(FollowupAlert.response_id.in_([r.id for r in resps]) if resps else FollowupAlert.id == "")).all()
    msgs = db.scalars(select(FollowupMessage).where(FollowupMessage.patient_id == t.patient_id).order_by(FollowupMessage.created_at)).all()
    p = db.get(Patient, t.patient_id)
    return {"task": S.followup_task_out(t, p.name), "responses": [S.followup_response_out(r) for r in resps],
            "alerts": [{"id": a.id, "level": a.level, "status": a.status, "note": a.handle_note} for a in alerts], "messages": [S.message_out(m) for m in msgs]}


@router.post("/followup-tasks/{tid}/close")
def close_task(tid: str, db: Session = Depends(get_db), user: User = Depends(require("followup.write")), ctx: Ctx = Depends(get_ctx)):
    t = db.get(FollowupTask, tid)
    if not t:
        raise NotFound("随访任务不存在")
    load_encounter_for(db, user, t.encounter_id, write=True)
    fu.close_task(db, actor_of(user, ctx), t)
    db.commit()
    return {"task": S.followup_task_out(t)}


class AlertHandleIn(BaseModel):
    status: str = Field(pattern="^(acknowledged|resolved)$")
    note: str = ""


@router.post("/followup-alerts/{aid}/handle")
def handle_alert(aid: str, body: AlertHandleIn, db: Session = Depends(get_db), user: User = Depends(require("followup.reply")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(FollowupAlert, aid)
    if not a:
        raise NotFound("预警不存在")
    load_encounter_for(db, user, a.encounter_id, write=True)
    fu.handle_alert(db, actor_of(user, ctx), a, status=body.status, note=body.note)
    db.commit()
    return {"id": a.id, "status": a.status}


class StaffMsgIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    task_id: str | None = None
    encounter_id: str | None = None


@router.post("/patients/{pid}/messages", status_code=201)
def send_staff_message(pid: str, body: StaffMsgIn, db: Session = Depends(get_db), user: User = Depends(require("followup.reply")), ctx: Ctx = Depends(get_ctx),
                       idem: str | None = Depends(idem_header)):
    p = db.get(Patient, pid)
    if not p:
        raise NotFound("患者不存在", code="patient_not_found")
    m, created = fu.send_message(db, patient_id=pid, sender_kind="doctor", sender_id=user.id, sender_name=user.display_name, body=body.body, encounter_id=body.encounter_id,
                                 task_id=body.task_id, client_key=idem or "")
    if created:
        for pm in db.scalars(select(FollowupMessage).where(FollowupMessage.patient_id == pid, FollowupMessage.sender_kind == "patient", FollowupMessage.read_at.is_(None))):
            pm.read_at = utcnow()
        audit.record(db, actor_of(user, ctx), "followup.reply", entity_type="message", entity_id=m.id, patient_id=pid, summary="回复患者消息")
    db.commit()
    return {"message": S.message_out(m), "created": created}


@router.get("/patients/{pid}/messages")
def staff_messages(pid: str, db: Session = Depends(get_db), user: User = Depends(require("followup.read"))):
    rows = db.scalars(select(FollowupMessage).where(FollowupMessage.patient_id == pid).order_by(FollowupMessage.created_at)).all()
    return {"items": [S.message_out(m) for m in rows]}


class ReleaseIn(BaseModel):
    summary: str = Field(min_length=2, max_length=2000)
    advice: list[str] = []
    revisit: str = ""


@router.post("/encounters/{eid}/release-summary")
def release_summary(eid: str, body: ReleaseIn, db: Session = Depends(get_db), user: User = Depends(require("record.write")), ctx: Ctx = Depends(get_ctx)):
    """Doctor-written patient-facing care summary (the ONLY clinical content a patient portal ever shows)."""
    e = load_encounter_for(db, user, eid, write=True)
    e.patient_summary_json = {"summary": body.summary.strip(), "advice": [a.strip() for a in body.advice if a.strip()], "revisit": body.revisit.strip()}
    e.released_at = utcnow()
    audit.record(db, actor_of(user, ctx), "encounter.release_summary", entity_type="encounter", entity_id=eid, encounter_id=eid, patient_id=e.patient_id, summary="发布患者端医嘱摘要")
    db.commit()
    return {"ok": True}


# ======================================================================= admin
@router.get("/admin/audit")
def audit_list(q: str = "", action: str = "", actor: str = "", patient_id: str = "", outcome: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), user: User = Depends(require("audit.read"))):
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(or_(AuditEvent.actor_id == actor, AuditEvent.actor_name.like(f"%{actor}%")))
    if patient_id:
        stmt = stmt.where(AuditEvent.patient_id == patient_id)
    if outcome:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    if q:
        stmt = stmt.where(AuditEvent.summary.like(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [{"id": r.id, "ts": iso(r.ts), "actor_kind": r.actor_kind, "actor": r.actor_name or r.actor_id, "role": r.role, "action": r.action,
                                       "summary": r.summary, "entity": f"{r.entity_type}:{r.entity_id}" if r.entity_type else "", "encounter_id": r.encounter_id or None,
                                       "patient_id": r.patient_id or None, "outcome": r.outcome, "ip": r.ip, "request_id": r.request_id, "detail": r.detail_json} for r in rows]}


@router.get("/admin/audit/verify")
def audit_verify(db: Session = Depends(get_db), user: User = Depends(require("audit.read"))):
    return audit.verify_chain(db)


@router.get("/admin/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(require("stats.read"))):
    now = utcnow()
    day0 = datetime.combine(now.date(), datetime.min.time()) - timedelta(hours=8)
    cnt = lambda stmt: db.scalar(select(func.count()).select_from(stmt.subquery())) or 0   # noqa: E731
    by_status = dict(db.execute(select(Encounter.status, func.count()).group_by(Encounter.status)).all())
    rx_status = dict(db.execute(select(Prescription.status, func.count()).group_by(Prescription.status)).all())
    jobs = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {"patients": cnt(select(Patient.id).where(Patient.archived_at.is_(None))), "encounters_today": cnt(select(Encounter.id).where(Encounter.created_at >= day0)),
            "encounters_by_status": by_status, "prescriptions_by_status": rx_status, "jobs_by_status": jobs,
            "open_alerts": cnt(select(FollowupAlert.id).where(FollowupAlert.status == "open")),
            "failed_jobs_24h": cnt(select(Job.id).where(Job.status == "failed", Job.finished_at >= now - timedelta(hours=24)))}


@router.get("/admin/jobs")
def list_jobs(status: str = "", kind: str = "", limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), user: User = Depends(require("job.manage"))):
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    return {"items": [{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "error_code": j.error_code or None, "error_detail": (j.error_detail or "")[:300] or None,
                       "encounter_id": j.encounter_id, "created_at": iso(j.created_at), "finished_at": iso(j.finished_at)}
                      for j in db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit))]}


@router.get("/system/status")
def system_status(db: Session = Depends(get_db), user: User = Depends(require("job.read"))):
    from server.ai.client import service_status
    s = get_settings()
    queue = dict(db.execute(select(Job.resource, func.count()).where(Job.status.in_(["queued", "running"])).group_by(Job.resource)).all())
    return {"models": service_status(), "queue_depth": queue, "his": {"mode": "mock", "connected": False, "notice": "未接入真实 HIS；提交为模拟流程"},
            "ai_mode": s.ai_mode, "version": "3.0.0", "env": s.app_env}
