"""Treatment plan, prescriptions (doctor side), pharmacy queue, catalog search."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from server.api import serializers as S
from server.db.base import acquire_write_lock, get_db
from server.db.models import Drug, Encounter, Formula, Prescription, PrescriptionEvent, TherapyItem, TherapyProgram, TreatmentPlan, User
from server.deps import Ctx, Idempotent, actor_of, get_ctx, idem_header, load_encounter_for, require
from server.domain import encounters as enc_svc
from server.domain import plans as plan_svc
from server.domain import prescriptions as rx_svc
from server.domain.enums import PlanStatus, RxStatus
from server.domain.ids import iso, utcnow
from server.errors import Conflict, Forbidden, NotFound, Unprocessable
from server.jobs import engine as jobs
from server.security import audit
from server.security.audit import Actor

router = APIRouter(tags=["treatment"])
SECTION_SET = {"medication", "herbal", "therapy"}


# ====================================================================== treatment plan
class PlanGenIn(BaseModel):
    sections: list[str] = ["medication", "herbal", "therapy"]
    reason: str = ""


@router.post("/encounters/{eid}/plan/generate", status_code=202)
def generate_plan(eid: str, body: PlanGenIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("plan.write")),
                  ctx: Ctx = Depends(get_ctx), idem_key: str | None = Depends(idem_header)):
    """One unified plan for the whole final-diagnosis set. Reuses an identical live plan; supersedes a stale one."""
    e = load_encounter_for(db, user, eid, write=True)
    secs = [s for s in body.sections if s in SECTION_SET]
    if not secs:
        raise Unprocessable("请至少选择一个需要起草的部分", code="no_sections")
    idem = Idempotent(db, request, actor_key=user.id, key=idem_key, body=body.model_dump())
    if idem.replay:
        return idem.replay[1]
    acquire_write_lock(db)                       # one live plan per visit: serialise concurrent generate requests
    live = plan_svc.live_plan(db, eid)
    if live and live.status == PlanStatus.GENERATING.value:
        out = {"plan_id": live.id, "job_id": live.job_id, "reused": True, "reason": "已有方案正在生成"}
        idem.complete(202, out)
        return out
    if live and live.ai_used and live.status in (PlanStatus.DRAFT.value, PlanStatus.CONFIRMED.value) and set(secs) <= set(live.sections_requested_json or []):
        st = plan_svc.plan_staleness(db, live, e)
        if not st["stale"] and not body.reason:
            out = {"plan_id": live.id, "job_id": live.job_id, "reused": True, "reason": "诊断与临床资料未变化，沿用现有方案"}
            idem.complete(202, out)
            return out
    try:
        plan = plan_svc.new_plan_shell(db, actor_of(user, ctx), e, sections=secs, ai_used=True, reason=body.reason or "重新生成")
    except Exception:
        idem.abort()
        raise
    j = jobs.enqueue("plan", {"plan_id": plan.id}, subject_type="plan", subject_id=plan.id, encounter_id=eid, idempotency_key=f"plan:{plan.id}",
                     created_by=user.id, max_attempts=2, timeout_s=900, session=db)
    plan.job_id = j.id
    db.commit()
    out = {"plan_id": plan.id, "job_id": j.id, "reused": False}
    idem.complete(202, out)
    return out


class PlanManualIn(BaseModel):
    sections: list[str] = ["medication", "herbal", "therapy"]


@router.post("/encounters/{eid}/plan/manual", status_code=201)
def manual_plan(eid: str, body: PlanManualIn, db: Session = Depends(get_db), user: User = Depends(require("plan.write")), ctx: Ctx = Depends(get_ctx)):
    """Doctor starts an empty plan by hand (AI unavailable, or doctor prefers own plan)."""
    e = load_encounter_for(db, user, eid, write=True)
    plan = plan_svc.new_plan_shell(db, actor_of(user, ctx), e, sections=[s for s in body.sections if s in SECTION_SET], ai_used=False, reason="医师手工新建")
    db.commit()
    return {"plan": S.plan_out(plan, plan_svc.plan_staleness(db, plan, e))}


@router.get("/plans/{pid}")
def get_plan(pid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    p = db.get(TreatmentPlan, pid)
    if not p:
        raise NotFound("方案不存在", code="plan_not_found")
    e = load_encounter_for(db, user, p.encounter_id)
    return {"plan": S.plan_out(p, plan_svc.plan_staleness(db, p, e))}


@router.get("/encounters/{eid}/plans")
def list_plans(eid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    load_encounter_for(db, user, eid)
    rows = db.scalars(select(TreatmentPlan).where(TreatmentPlan.encounter_id == eid).order_by(TreatmentPlan.version_no.desc())).all()
    return {"items": [{"id": p.id, "version_no": p.version_no, "status": p.status, "ai_used": p.ai_used, "created_at": iso(p.created_at),
                       "supersede_reason": p.supersede_reason or None, "dx_snapshot": p.dx_snapshot_json} for p in rows]}


class PlanEditIn(BaseModel):
    content: dict
    expected_version: int | None = None


@router.patch("/plans/{pid}")
def edit_plan(pid: str, body: PlanEditIn, db: Session = Depends(get_db), user: User = Depends(require("plan.write")), ctx: Ctx = Depends(get_ctx)):
    p = db.get(TreatmentPlan, pid)
    if not p:
        raise NotFound("方案不存在", code="plan_not_found")
    e = load_encounter_for(db, user, p.encounter_id, write=True)
    plan_svc.edit_plan_content(db, actor_of(user, ctx), p, e, body.content, expected_version=body.expected_version)
    db.commit()
    return {"plan": S.plan_out(p, plan_svc.plan_staleness(db, p, e))}


class VersionIn(BaseModel):
    expected_version: int | None = None


@router.post("/plans/{pid}/confirm")
def confirm_plan(pid: str, body: VersionIn, db: Session = Depends(get_db), user: User = Depends(require("plan.confirm")), ctx: Ctx = Depends(get_ctx)):
    p = db.get(TreatmentPlan, pid)
    if not p:
        raise NotFound("方案不存在", code="plan_not_found")
    e = load_encounter_for(db, user, p.encounter_id, write=True)
    plan_svc.confirm_plan(db, actor_of(user, ctx), p, e, expected_version=body.expected_version)
    db.commit()
    return {"plan": S.plan_out(p, plan_svc.plan_staleness(db, p, e))}


@router.get("/plans/{pid}/safety-preview")
def plan_safety_preview(pid: str, db: Session = Depends(get_db), user: User = Depends(require("encounter.read"))):
    """Decision support: rule findings for the plan's drafted medication (allergy, pregnancy, interactions, dose range, 十八反/十九畏)."""
    p = db.get(TreatmentPlan, pid)
    if not p:
        raise NotFound("方案不存在", code="plan_not_found")
    e = load_encounter_for(db, user, p.encounter_id)
    return rx_svc.preview_plan_safety(db, p, e)


class RebaseIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    expected_version: int | None = None


@router.post("/plans/{pid}/rebase")
def rebase_plan(pid: str, body: RebaseIn, db: Session = Depends(get_db), user: User = Depends(require("plan.write")), ctx: Ctx = Depends(get_ctx)):
    """Doctor keeps this plan (and their edits) although diagnoses/facts changed. Needs a written reason; plan returns to draft."""
    p = db.get(TreatmentPlan, pid)
    if not p:
        raise NotFound("方案不存在", code="plan_not_found")
    e = load_encounter_for(db, user, p.encounter_id, write=True)
    plan_svc.rebase_plan(db, actor_of(user, ctx), p, e, reason=body.reason, expected_version=body.expected_version)
    db.commit()
    return {"plan": S.plan_out(p, plan_svc.plan_staleness(db, p, e))}


class TherapyActionIn(BaseModel):
    action: str = Field(pattern="^(accept|modify|reject|revert)$")
    edits: dict | None = None
    note: str | None = None


@router.post("/therapy-items/{tid}/review")
def review_therapy(tid: str, body: TherapyActionIn, db: Session = Depends(get_db), user: User = Depends(require("plan.write")), ctx: Ctx = Depends(get_ctx)):
    t = db.get(TherapyItem, tid)
    if not t:
        raise NotFound("治疗项目不存在")
    e = load_encounter_for(db, user, t.encounter_id, write=True)
    plan_svc.review_therapy_item(db, actor_of(user, ctx), t, action=body.action, edits=body.edits, note=body.note)
    db.commit()
    p = db.get(TreatmentPlan, t.plan_id)
    return {"therapy_item": S.therapy_out(t), "plan": S.plan_out(p, plan_svc.plan_staleness(db, p, e))}


class TherapyAddIn(BaseModel):
    category: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=128)
    acupoints: list[str] = []
    detail: dict = {}
    indications: str = ""
    contraindications: str = ""


@router.post("/plans/{pid}/therapy-items", status_code=201)
def add_therapy(pid: str, body: TherapyAddIn, db: Session = Depends(get_db), user: User = Depends(require("plan.write")), ctx: Ctx = Depends(get_ctx)):
    p = db.get(TreatmentPlan, pid)
    if not p:
        raise NotFound("方案不存在")
    e = load_encounter_for(db, user, p.encounter_id, write=True)
    if p.status in (PlanStatus.GENERATING.value, PlanStatus.SUPERSEDED.value, PlanStatus.FAILED.value):
        raise Conflict("当前状态下方案不可编辑", code="plan_not_editable")
    t = TherapyItem(plan_id=p.id, encounter_id=e.id, sort_order=len(p.therapy_items), category=body.category, name=body.name.strip(), source_type="doctor",
                    acupoints_json=body.acupoints, detail_json=body.detail, indications=body.indications, contraindications=body.contraindications,
                    status="accepted", reviewed_by=user.id, reviewed_at=utcnow())
    db.add(t)
    if p.status == PlanStatus.CONFIRMED.value:
        p.status, p.confirmed_at, p.confirmed_by, p.confirmed_content_hash = PlanStatus.DRAFT.value, None, None, ""
    p.row_version += 1
    db.flush()
    audit.record(db, actor_of(user, ctx), "therapy.add", entity_type="therapy_item", entity_id=t.id, encounter_id=e.id, patient_id=e.patient_id, summary=f"医师新增治疗项目：{t.name}")
    db.commit()
    db.refresh(p)
    return {"therapy_item": S.therapy_out(t), "plan": S.plan_out(p, plan_svc.plan_staleness(db, p, e))}


# ====================================================================== prescriptions (doctor)
class RxItemIn(BaseModel):
    name: str = ""
    drug_id: str | None = None
    spec: str = ""
    form: str = ""
    dose_text: str = ""
    frequency: str = ""
    route: str = ""
    days: int | None = None
    quantity: float | None = None
    quantity_unit: str = ""
    dose_g: float | None = None
    special: str = ""
    role: str = ""
    instructions: str = ""
    reason: str = ""
    in_catalog: bool = False
    flags: list[str] = []


class RxHeaderIn(BaseModel):
    doses_count: int | None = Field(default=None, ge=1, le=60)
    decoction: str = ""
    herbal_usage: str = ""


class RxCreateIn(BaseModel):
    rx_type: str = Field(pattern="^(western|patent|herbal)$")
    title: str = ""
    items: list[RxItemIn] = []
    header: RxHeaderIn | None = None
    plan_id: str | None = None
    notes: str = ""
    ai_generated: bool = False
    formula_type: str = ""
    formula_source: str = ""
    acknowledge_stale: str = ""


@router.get("/encounters/{eid}/prescriptions")
def list_encounter_rx(eid: str, include_voided: bool = False, db: Session = Depends(get_db), user: User = Depends(require("rx.read"))):
    load_encounter_for(db, user, eid)
    stmt = select(Prescription).where(Prescription.encounter_id == eid)
    if not include_voided:
        stmt = stmt.where(Prescription.status != "voided")
    return {"items": [S.rx_out(r, brief=False) for r in db.scalars(stmt.order_by(Prescription.created_at))]}


@router.post("/encounters/{eid}/prescriptions", status_code=201)
def create_rx(eid: str, body: RxCreateIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("rx.write")), ctx: Ctx = Depends(get_ctx),
              idem_key: str | None = Depends(idem_header)):
    e = load_encounter_for(db, user, eid, write=True)
    idem = Idempotent(db, request, actor_key=user.id, key=idem_key, body=body.model_dump())
    if idem.replay:
        return idem.replay[1]
    try:
        rx = rx_svc.create_prescription(db, actor_of(user, ctx), e, user, rx_type=body.rx_type, title=body.title, items=[i.model_dump() for i in body.items],
                                        plan_id=body.plan_id, header=body.header.model_dump() if body.header else None, notes=body.notes,
                                        ai_generated=body.ai_generated, formula_type=body.formula_type, formula_source=body.formula_source,
                                        acknowledge_stale=body.acknowledge_stale)
    except Exception:
        idem.abort()
        raise
    db.commit()
    out = {"prescription": S.rx_out(rx)}
    idem.complete(201, out)
    return out


class RxFromPlanIn(BaseModel):
    plan_id: str
    kinds: list[str] = ["western", "patent", "herbal"]
    acknowledge_stale: str = ""


@router.post("/encounters/{eid}/prescriptions/from-plan", status_code=201)
def rx_from_plan(eid: str, body: RxFromPlanIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("rx.write")), ctx: Ctx = Depends(get_ctx),
                 idem_key: str | None = Depends(idem_header)):
    """Turn the plan's medication/herbal sections into editable DRAFT prescriptions (never signed, never sent anywhere)."""
    e = load_encounter_for(db, user, eid, write=True)
    plan = db.get(TreatmentPlan, body.plan_id)
    if not plan or plan.encounter_id != eid:
        raise NotFound("治疗方案不存在", code="plan_not_found")
    if plan.status not in (PlanStatus.DRAFT.value, PlanStatus.CONFIRMED.value):
        raise Conflict("方案当前状态不能生成处方", code="plan_not_ready")
    idem = Idempotent(db, request, actor_key=user.id, key=idem_key, body=body.model_dump())
    if idem.replay:
        return idem.replay[1]
    acquire_write_lock(db)                       # two parallel "generate prescriptions" must not both think nothing exists yet
    existing = {(r.rx_type) for r in db.scalars(select(Prescription).where(Prescription.encounter_id == eid, Prescription.plan_id == plan.id,
                                                                            Prescription.status != "voided"))}
    c, made, skipped = plan.content_json or {}, [], []
    try:
        if "western" in body.kinds and (c.get("medication") or {}).get("western"):
            if "western" in existing:
                skipped.append("western")
            else:
                made.append(rx_svc.create_prescription(db, actor_of(user, ctx), e, user, rx_type="western", plan_id=plan.id, ai_generated=plan.ai_used,
                                                       items=[_med_item(m) for m in c["medication"]["western"]], acknowledge_stale=body.acknowledge_stale))
        if "patent" in body.kinds and (c.get("medication") or {}).get("patent"):
            if "patent" in existing:
                skipped.append("patent")
            else:
                made.append(rx_svc.create_prescription(db, actor_of(user, ctx), e, user, rx_type="patent", plan_id=plan.id, ai_generated=plan.ai_used,
                                                       items=[_med_item(m) for m in c["medication"]["patent"]], acknowledge_stale=body.acknowledge_stale))
        if "herbal" in body.kinds:
            if "herbal" in existing:
                skipped.append("herbal")
            else:
                for h in (c.get("herbal") or []):
                    made.append(rx_svc.create_prescription(
                        db, actor_of(user, ctx), e, user, rx_type="herbal", plan_id=plan.id, ai_generated=plan.ai_used, title=h.get("title", ""),
                        formula_type=h.get("formula_type", ""), formula_source=h.get("source_note", ""),
                        header={"doses_count": h.get("doses_count"), "decoction": h.get("decoction", ""), "herbal_usage": h.get("usage", "")},
                        items=[{"name": x["name"], "dose_g": x.get("dose_g"), "special": x.get("special", ""), "role": x.get("role", ""), "drug_id": x.get("drug_id"),
                                "in_catalog": x.get("in_catalog", False), "flags": x.get("flags", [])} for x in h.get("herbs", [])],
                        acknowledge_stale=body.acknowledge_stale, notes=h.get("rationale", "")))
    except Exception:
        idem.abort()
        raise
    if not made and not skipped:
        idem.abort()
        raise Unprocessable("方案中没有可转换为处方的药物内容", code="nothing_to_convert")
    db.commit()
    out = {"prescriptions": [S.rx_out(r) for r in made], "skipped_existing": skipped}
    idem.complete(201, out)
    return out


def _med_item(m: dict) -> dict:
    return {"name": m["name"], "drug_id": m.get("drug_id"), "dose_text": m.get("dose_text", ""), "frequency": m.get("frequency", ""), "route": m.get("route", ""),
            "days": m.get("days"), "spec": m.get("spec", ""), "form": m.get("form", ""), "reason": m.get("reason", ""), "in_catalog": m.get("in_catalog", False),
            "flags": m.get("flags", [])}


@router.get("/prescriptions/{rid}")
def get_rx(rid: str, db: Session = Depends(get_db), user: User = Depends(require("rx.read"))):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在", code="rx_not_found")
    _authorise_rx_read(db, user, r)
    evs = db.scalars(select(PrescriptionEvent).where(PrescriptionEvent.prescription_id == rid).order_by(PrescriptionEvent.id)).all()
    return {"prescription": S.rx_out(r), "events": [{"ts": iso(x.ts), "actor": x.actor_name, "from": x.from_status, "to": x.to_status, "action": x.action, "note": x.note} for x in evs]}


def _authorise_rx_read(db: Session, user: User, r: Prescription) -> None:
    if user.role == "PHARMACIST":
        if r.status in ("draft", "voided"):
            raise Forbidden("药房只能查看已签署的处方", code="rx_not_signed")
    elif user.role == "DOCTOR":
        load_encounter_for(db, user, r.encounter_id)


class RxPatchIn(BaseModel):
    items: list[RxItemIn] | None = None
    header: RxHeaderIn | None = None
    title: str | None = None
    notes: str | None = None
    expected_version: int | None = None


@router.patch("/prescriptions/{rid}")
def patch_rx(rid: str, body: RxPatchIn, db: Session = Depends(get_db), user: User = Depends(require("rx.write")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在", code="rx_not_found")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    if r.doctor_id != user.id:
        raise Forbidden("只能修改本人开具的处方", code="not_prescriber")
    rx_svc.update_prescription(db, actor_of(user, ctx), r, e, items=[i.model_dump() for i in body.items] if body.items is not None else None,
                               header=body.header.model_dump() if body.header else None, title=body.title, notes=body.notes, expected_version=body.expected_version)
    db.commit()
    return {"prescription": S.rx_out(r)}


@router.post("/prescriptions/{rid}/check")
def recheck_rx(rid: str, db: Session = Depends(get_db), user: User = Depends(require("rx.write")), ctx: Ctx = Depends(get_ctx)):
    """Re-run the safety rules now (e.g. after the patient's allergy list changed)."""
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    e = load_encounter_for(db, user, r.encounter_id)
    rx_svc.refresh_safety(db, r, e)
    db.commit()
    return {"prescription": S.rx_out(r)}


class AckIn(BaseModel):
    acks: dict[str, str]


@router.post("/prescriptions/{rid}/ack")
def ack_rx(rid: str, body: AckIn, db: Session = Depends(get_db), user: User = Depends(require("rx.write")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    if r.doctor_id != user.id:
        raise Forbidden("只能确认本人开具的处方", code="not_prescriber")
    rx_svc.acknowledge(db, actor_of(user, ctx), r, e, body.acks)
    db.commit()
    return {"prescription": S.rx_out(r)}


@router.post("/prescriptions/{rid}/sign")
def sign_rx(rid: str, body: VersionIn, db: Session = Depends(get_db), user: User = Depends(require("rx.sign")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在", code="rx_not_found")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    rx_svc.sign(db, actor_of(user, ctx), r, e, user, expected_version=body.expected_version)
    db.commit()
    return {"prescription": S.rx_out(r)}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=2, max_length=300)


@router.post("/prescriptions/{rid}/unsign")
def unsign_rx(rid: str, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("rx.sign")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    if r.doctor_id != user.id:
        raise Forbidden("只能撤回本人签署的处方", code="not_prescriber")
    rx_svc.unsign(db, actor_of(user, ctx), r, e, reason=body.reason)
    db.commit()
    return {"prescription": S.rx_out(r)}


@router.post("/prescriptions/{rid}/reopen")
def reopen_rx(rid: str, db: Session = Depends(get_db), user: User = Depends(require("rx.write")), ctx: Ctx = Depends(get_ctx)):
    """After a pharmacy return: move returned → draft so the doctor can edit and re-sign."""
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    if r.doctor_id != user.id:
        raise Forbidden("只能修改本人开具的处方", code="not_prescriber")
    if r.status != RxStatus.RETURNED.value:
        raise Conflict("只有被药房退回的处方可以重新编辑", code="bad_transition")
    r.signed_by = r.signed_at = None
    r.signature_hash = ""
    rx_svc._transition(db, r, actor_of(user, ctx), RxStatus.DRAFT.value, "reopen", "药房退回后重新编辑")
    audit.record(db, actor_of(user, ctx), "rx.reopen", entity_type="prescription", entity_id=r.id, encounter_id=e.id, patient_id=e.patient_id, summary=f"重新编辑被退回的处方 {r.rx_no}")
    db.commit()
    return {"prescription": S.rx_out(r)}


@router.post("/prescriptions/{rid}/void")
def void_rx(rid: str, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("rx.void")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    if r.doctor_id != user.id:
        raise Forbidden("只能作废本人开具的处方", code="not_prescriber")
    rx_svc.void(db, actor_of(user, ctx), r, e, reason=body.reason)
    db.commit()
    return {"prescription": S.rx_out(r)}


@router.post("/prescriptions/{rid}/submit-his")
def submit_his(rid: str, db: Session = Depends(get_db), user: User = Depends(require("rx.sign")), ctx: Ctx = Depends(get_ctx)):
    """SIMULATED hand-off to a mock HIS. Always labelled as simulated; no real HIS is contacted."""
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    e = load_encounter_for(db, user, r.encounter_id, write=True)
    rx_svc.submit_mock_his(db, actor_of(user, ctx), r, e)
    db.commit()
    return {"prescription": S.rx_out(r), "simulated": True, "notice": "这是模拟提交：系统尚未接入真实 HIS，未向任何外部系统发送医嘱。"}


@router.get("/prescriptions")
def search_rx(status: str | None = None, patient_id: str | None = None, q: str = "", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              db: Session = Depends(get_db), user: User = Depends(require("rx.read"))):
    stmt = select(Prescription)
    if user.role == "DOCTOR":
        stmt = stmt.where(Prescription.doctor_id == user.id)
    if user.role == "PHARMACIST":
        stmt = stmt.where(Prescription.status.notin_(["draft", "voided"]))
    if status:
        stmt = stmt.where(Prescription.status == status)
    if patient_id:
        stmt = stmt.where(Prescription.patient_id == patient_id)
    if q.strip():
        from server.db.models import Patient
        like = f"%{q.strip()}%"
        stmt = stmt.join(Patient, Patient.id == Prescription.patient_id).where(or_(Patient.name.like(like), Prescription.rx_no.like(like), Patient.mrn.like(like)))
    rows = db.scalars(stmt.order_by(Prescription.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [S.rx_out(r, brief=True) for r in rows]}


# ====================================================================== pharmacy
@router.get("/pharmacy/queue")
def pharmacy_queue(db: Session = Depends(get_db), user: User = Depends(require("pharmacy.queue"))):
    out = {}
    for st in ("signed", "pharmacy_review", "approved", "returned"):
        rows = db.scalars(select(Prescription).where(Prescription.status == st).order_by(Prescription.signed_at)).all()
        out[st] = [S.rx_out(r, brief=True) for r in rows]
    done = db.scalars(select(Prescription).where(Prescription.status == "dispensed").order_by(Prescription.dispensed_at.desc()).limit(20)).all()
    out["dispensed"] = [S.rx_out(r, brief=True) for r in done]
    return out


@router.post("/pharmacy/prescriptions/{rid}/start")
def ph_start(rid: str, db: Session = Depends(get_db), user: User = Depends(require("pharmacy.review")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    rx_svc.pharmacy_start(db, actor_of(user, ctx), r)
    db.commit()
    return {"prescription": S.rx_out(r)}


class PhDecideIn(BaseModel):
    approve: bool
    note: str = ""


@router.post("/pharmacy/prescriptions/{rid}/decide")
def ph_decide(rid: str, body: PhDecideIn, db: Session = Depends(get_db), user: User = Depends(require("pharmacy.review")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    rx_svc.pharmacy_decide(db, actor_of(user, ctx), r, approve=body.approve, note=body.note)
    db.commit()
    return {"prescription": S.rx_out(r)}


@router.post("/pharmacy/prescriptions/{rid}/dispense")
def ph_dispense(rid: str, db: Session = Depends(get_db), user: User = Depends(require("pharmacy.dispense")), ctx: Ctx = Depends(get_ctx)):
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在")
    rx_svc.pharmacy_dispense(db, actor_of(user, ctx), r)
    db.commit()
    return {"prescription": S.rx_out(r)}


# ====================================================================== catalog
def _drug_out(d: Drug) -> dict:
    return {"id": d.id, "kind": d.kind, "name": d.name, "generic_name": d.generic_name, "spec": d.spec, "form": d.form, "unit": d.unit, "default_dose": d.default_dose,
            "default_frequency": d.default_frequency, "default_route": d.default_route, "drug_class": d.drug_class, "special_method": d.special_method,
            "dose_min_g": d.dose_min_g, "dose_max_g": d.dose_max_g, "source": d.source, "pregnancy_note": d.pregnancy_note, "contraindication": d.contraindication,
            "his_code": d.his_code, "active": d.active}


@router.get("/catalog/drugs")
def search_drugs(q: str = "", kind: str | None = None, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(require("catalog.read"))):
    stmt = select(Drug).where(Drug.active.is_(True))
    if kind:
        stmt = stmt.where(Drug.kind == kind)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Drug.name.like(like), Drug.generic_name.like(like), Drug.his_code.like(like)))
    rows = db.scalars(stmt.order_by(func_len(Drug.name), Drug.name).limit(limit)).all()
    return {"items": [_drug_out(d) for d in rows]}


def func_len(col):
    from sqlalchemy import func
    return func.length(col)


class DrugIn(BaseModel):
    kind: str = Field(pattern="^(western|patent|herb)$")
    name: str = Field(min_length=1, max_length=128)
    generic_name: str = ""
    spec: str = ""
    form: str = ""
    unit: str = ""
    default_dose: str = ""
    default_frequency: str = ""
    drug_class: str = ""
    allergen_classes: list[str] = []
    pregnancy_note: str = ""
    contraindication: str = ""
    dose_min_g: float | None = None
    dose_max_g: float | None = None
    special_method: str = ""
    his_code: str = ""


@router.post("/catalog/drugs", status_code=201)
def create_drug(body: DrugIn, db: Session = Depends(get_db), user: User = Depends(require("catalog.write")), ctx: Ctx = Depends(get_ctx)):
    if db.scalar(select(Drug.id).where(Drug.kind == body.kind, Drug.name == body.name, Drug.spec == body.spec)):
        raise Conflict("该药品（同名同规格）已存在", code="drug_exists")
    d = Drug(kind=body.kind, name=body.name.strip(), generic_name=body.generic_name, spec=body.spec, form=body.form, unit=body.unit, default_dose=body.default_dose,
             default_frequency=body.default_frequency, drug_class=body.drug_class, allergen_classes_json=body.allergen_classes, pregnancy_note=body.pregnancy_note,
             contraindication=body.contraindication, dose_min_g=body.dose_min_g, dose_max_g=body.dose_max_g, special_method=body.special_method, his_code=body.his_code,
             source="hospital")
    db.add(d)
    db.flush()
    audit.record(db, actor_of(user, ctx), "catalog.drug.create", entity_type="drug", entity_id=d.id, summary=f"新增药品 {d.name}")
    db.commit()
    return {"drug": _drug_out(d)}


@router.patch("/catalog/drugs/{did}")
def update_drug(did: str, body: DrugIn, db: Session = Depends(get_db), user: User = Depends(require("catalog.write")), ctx: Ctx = Depends(get_ctx)):
    d = db.get(Drug, did)
    if not d:
        raise NotFound("药品不存在")
    for k, v in body.model_dump().items():
        setattr(d, "allergen_classes_json" if k == "allergen_classes" else k, v)
    if d.source in ("sample", "reference"):
        d.source = "hospital"                                       # a pharmacist edited it → now owned by the clinic
    audit.record(db, actor_of(user, ctx), "catalog.drug.update", entity_type="drug", entity_id=d.id, summary=f"修改药品 {d.name}")
    db.commit()
    return {"drug": _drug_out(d)}


@router.get("/catalog/formulas")
def list_formulas(q: str = "", db: Session = Depends(get_db), user: User = Depends(require("catalog.read"))):
    stmt = select(Formula).where(Formula.active.is_(True))
    if q.strip():
        stmt = stmt.where(Formula.name.like(f"%{q.strip()}%"))
    return {"items": [{"id": f.id, "name": f.name, "formula_type": f.formula_type, "indication": f.indication, "herbs": f.herbs_json, "source": f.source,
                       "decoction": f.decoction} for f in db.scalars(stmt.order_by(Formula.name))]}


@router.get("/catalog/therapy-programs")
def list_programs(db: Session = Depends(get_db), user: User = Depends(require("catalog.read"))):
    return {"items": [{"id": t.id, "name": t.name, "category": t.category, "applicable": t.applicable, "goal": t.goal, "frequency": t.frequency,
                       "contraindications": t.contraindications, "hospital_code": t.hospital_code, "source": t.source}
                      for t in db.scalars(select(TherapyProgram).where(TherapyProgram.active.is_(True)).order_by(TherapyProgram.category, TherapyProgram.name))]}
