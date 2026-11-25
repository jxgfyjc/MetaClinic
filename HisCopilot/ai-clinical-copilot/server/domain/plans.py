"""Diagnosis review + treatment plan lifecycle.

Invariants enforced here (and covered by tests):
  1. The *final diagnosis set* = diagnoses with status accepted|modified, ordered by sort_order. Its hash
     (`dx_set_hash`) covers id + module + name + status, so editing a name or flipping any status changes it.
  2. At most ONE live (draft|confirmed|generating) plan per encounter. Creating/regenerating supersedes the
     previous one (kept as history, never deleted).
  3. A plan whose dx_set_hash / inputs_hash differs from the encounter's current values is STALE. A stale plan
     can still be read (history / comparison) but cannot be confirmed, and prescriptions cannot be created from
     it without an explicit `acknowledge_stale` + reason.
  4. Confirming a plan freezes `confirmed_content_hash`; further edits revert it to draft (re-confirm needed).
  5. The plan is *intent*; prescriptions are documents created FROM it (plan_id link, one-way).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import Diagnosis, Encounter, TherapyItem, TreatmentPlan
from server.domain.enums import DxStatus, PlanStatus
from server.domain.ids import hash_obj, utcnow
from server.errors import Conflict, NotFound, Unprocessable
from server.security import audit
from server.security.audit import Actor

FINAL = (DxStatus.ACCEPTED.value, DxStatus.MODIFIED.value)


# --------------------------------------------------------------------------- diagnoses
def final_diagnoses(db: Session, encounter_id: str) -> list[Diagnosis]:
    return list(db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == encounter_id, Diagnosis.status.in_(FINAL))
                           .order_by(Diagnosis.sort_order, Diagnosis.created_at)))


def dx_set_hash(dxs: list[Diagnosis]) -> str:
    return hash_obj(sorted([[d.id, d.module, d.name, d.status] for d in dxs]))


def dx_snapshot(dxs: list[Diagnosis]) -> list[dict]:
    return [{"id": d.id, "module": d.module, "name": d.name, "status": d.status, "primary": d.is_primary} for d in dxs]


def review_diagnosis(db: Session, actor: Actor, dx: Diagnosis, *, action: str, name: str | None = None,
                     note: str | None = None, icd_code: str | None = None, primary: bool | None = None,
                     expected_version: int | None = None) -> Diagnosis:
    """action: accept | modify | reject | revert (back to pending) | withdraw (doctor-authored only)."""
    from server.deps import check_version
    check_version(dx.row_version, expected_version, "诊断", db=db, obj=dx)
    enc = db.get(Encounter, dx.encounter_id)
    if enc.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("就诊已结束，不能再修改诊断", code="encounter_closed")
    before = (dx.status, dx.name)
    if action == "accept":
        dx.status = DxStatus.ACCEPTED.value
    elif action == "modify":
        new = (name or "").strip()
        if not new:
            raise Unprocessable("修改诊断时必须填写新的诊断名称", code="name_required")
        if not dx.original_name:
            dx.original_name = dx.name
        dx.name, dx.status = new, DxStatus.MODIFIED.value
    elif action == "reject":
        dx.status = DxStatus.REJECTED.value
    elif action == "revert":
        dx.status = DxStatus.PENDING.value
    elif action == "withdraw":
        if dx.origin != "doctor":
            raise Unprocessable("只有医师自己添加的诊断可以撤销；AI 候选请使用“驳回”", code="not_doctor_dx")
        dx.status = DxStatus.WITHDRAWN.value
    else:
        raise Unprocessable(f"未知操作 {action}", code="bad_action")
    if note is not None:
        dx.doctor_note = note
    if icd_code is not None:
        dx.icd_code = icd_code.strip()
    if primary is not None:
        dx.is_primary = bool(primary) and dx.status in FINAL
        if dx.is_primary:                                   # only one primary at a time
            for o in db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == dx.encounter_id,
                                                        Diagnosis.id != dx.id, Diagnosis.is_primary.is_(True))):
                o.is_primary = False
    if dx.status not in FINAL:
        dx.is_primary = False
    dx.reviewed_by, dx.reviewed_at = actor.id, utcnow()
    dx.row_version += 1
    audit.record(db, actor, f"dx.{action}", entity_type="diagnosis", entity_id=dx.id, encounter_id=dx.encounter_id,
                 patient_id=dx.encounter_id and enc.patient_id, summary=f"{action}：{dx.name}",
                 detail={"from": list(before), "to": [dx.status, dx.name], "ai_original": dx.original_name or None})
    mark_plan_staleness(db, dx.encounter_id)
    return dx


def add_doctor_diagnosis(db: Session, actor: Actor, enc: Encounter, *, module: str, name: str, icd_code: str = "",
                         note: str = "", primary: bool = False) -> Diagnosis:
    name = name.strip()
    if not name:
        raise Unprocessable("诊断名称不能为空", code="name_required")
    if module not in ("western", "tcm"):
        raise Unprocessable("module 必须是 western 或 tcm", code="bad_module")
    dup = db.scalar(select(Diagnosis).where(Diagnosis.encounter_id == enc.id, Diagnosis.module == module,
                                            Diagnosis.name == name, Diagnosis.status.in_(FINAL)))
    if dup:
        raise Conflict("该诊断已在最终诊断列表中", code="dx_exists", details={"id": dup.id})
    nxt = (db.scalar(select(func.max(Diagnosis.sort_order)).where(Diagnosis.encounter_id == enc.id)) or 0) + 1
    dx = Diagnosis(encounter_id=enc.id, module=module, name=name, icd_code=icd_code.strip(), origin="doctor",
                   status=DxStatus.ACCEPTED.value, doctor_note=note, sort_order=nxt, reviewed_by=actor.id,
                   reviewed_at=utcnow(), rationale="医师自行添加")
    db.add(dx)
    db.flush()
    if primary:
        review_diagnosis(db, actor, dx, action="accept", primary=True)
    audit.record(db, actor, "dx.add", entity_type="diagnosis", entity_id=dx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"医师新增诊断：{name}（{module}）")
    mark_plan_staleness(db, enc.id)
    return dx


# --------------------------------------------------------------------------- plan lifecycle
def live_plan(db: Session, encounter_id: str) -> TreatmentPlan | None:
    return db.scalar(select(TreatmentPlan).where(
        TreatmentPlan.encounter_id == encounter_id,
        TreatmentPlan.status.in_([PlanStatus.GENERATING.value, PlanStatus.DRAFT.value, PlanStatus.CONFIRMED.value]))
        .order_by(TreatmentPlan.version_no.desc()).limit(1))


def plan_staleness(db: Session, plan: TreatmentPlan, enc: Encounter) -> dict:
    cur = dx_set_hash(final_diagnoses(db, enc.id))
    return {"dx_changed": plan.dx_set_hash != cur, "inputs_changed": plan.inputs_hash != enc.inputs_hash,
            "stale": plan.status != PlanStatus.GENERATING.value
                     and (plan.dx_set_hash != cur or plan.inputs_hash != enc.inputs_hash)}


def mark_plan_staleness(db: Session, encounter_id: str) -> None:
    """If a CONFIRMED plan no longer matches the diagnosis set, drop it back to draft so it cannot be used
    as-is (the doctor must review/regenerate and re-confirm). Drafts just report stale via plan_staleness."""
    plan = live_plan(db, encounter_id)
    if not plan or plan.status != PlanStatus.CONFIRMED.value:
        return
    if plan.dx_set_hash != dx_set_hash(final_diagnoses(db, encounter_id)):
        plan.status = PlanStatus.DRAFT.value
        plan.confirmed_at, plan.confirmed_by, plan.confirmed_content_hash = None, None, ""
        plan.row_version += 1


def new_plan_shell(db: Session, actor: Actor, enc: Encounter, *, sections: list[str], ai_used: bool,
                   reason: str = "") -> TreatmentPlan:
    dxs = final_diagnoses(db, enc.id)
    if not dxs:
        raise Unprocessable("还没有已确认的诊断：请至少接受或修改一个诊断，或手动添加诊断后再制定治疗方案",
                            code="no_final_diagnoses")
    prev = live_plan(db, enc.id)
    ver = (db.scalar(select(func.max(TreatmentPlan.version_no)).where(TreatmentPlan.encounter_id == enc.id)) or 0) + 1
    plan = TreatmentPlan(encounter_id=enc.id, version_no=ver, status=PlanStatus.GENERATING.value if ai_used
                         else PlanStatus.DRAFT.value, dx_set_hash=dx_set_hash(dxs), dx_snapshot_json=dx_snapshot(dxs),
                         inputs_version=enc.inputs_version, inputs_hash=enc.inputs_hash,
                         sections_requested_json=sections, ai_used=ai_used, created_by=actor.id,
                         content_json=empty_content())
    db.add(plan)
    db.flush()
    if prev:
        prev.status = PlanStatus.SUPERSEDED.value
        prev.superseded_by = plan.id
        prev.supersede_reason = reason or "重新生成/新建方案"
        prev.row_version += 1
    audit.record(db, actor, "plan.create", entity_type="plan", entity_id=plan.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"新建治疗方案 v{ver}（{'AI' if ai_used else '手工'}）",
                 detail={"sections": sections, "supersedes": prev.id if prev else None,
                         "dx_count": len(dxs)})
    return plan


def empty_content() -> dict:
    return {"summary": "", "coordination_notes": [], "medication": {"western": [], "patent": []},
            "herbal": [], "lifestyle": [], "monitoring": [], "followup_advice": "", "safety_notes": [],
            "missing_info": []}


def confirm_plan(db: Session, actor: Actor, plan: TreatmentPlan, enc: Encounter, *, expected_version: int | None) -> TreatmentPlan:
    from server.deps import check_version
    check_version(plan.row_version, expected_version, "治疗方案", db=db, obj=plan)
    if plan.status == PlanStatus.GENERATING.value:
        raise Conflict("方案仍在生成中", code="plan_generating")
    if plan.status in (PlanStatus.SUPERSEDED.value, PlanStatus.FAILED.value):
        raise Conflict("该方案已失效，不能确认", code="plan_inactive")
    st = plan_staleness(db, plan, enc)
    if st["stale"]:
        raise Conflict("方案与当前诊断/临床资料不一致（已过期）：请重新生成或手动更新后再确认", code="plan_stale", details=st)
    pending = [t for t in plan.therapy_items if t.status == "pending"]
    if pending:
        raise Conflict(f"还有 {len(pending)} 项治疗项目未审核（接受/修改/拒绝）", code="therapy_pending",
                       details={"items": [t.id for t in pending]})
    plan.status = PlanStatus.CONFIRMED.value
    plan.confirmed_by, plan.confirmed_at = actor.id, utcnow()
    plan.confirmed_content_hash = plan_content_hash(plan)
    plan.row_version += 1
    audit.record(db, actor, "plan.confirm", entity_type="plan", entity_id=plan.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"确认治疗方案 v{plan.version_no}",
                 detail={"content_hash": plan.confirmed_content_hash})
    return plan


def rebase_plan(db: Session, actor: Actor, plan: TreatmentPlan, enc: Encounter, *, reason: str,
                expected_version: int | None) -> TreatmentPlan:
    """The doctor states, in writing, that this (possibly hand-edited) plan still applies after the diagnosis set or
    clinical facts changed. Re-anchors the plan to the CURRENT hashes so it stops being reported stale, WITHOUT
    touching its content (so doctor edits are never lost to a regeneration) — but it always drops back to *draft*:
    a re-anchored plan must be re-confirmed. Fully audited with the previous anchor + the reason."""
    from server.deps import check_version
    check_version(plan.row_version, expected_version, "治疗方案", db=db, obj=plan)
    if plan.status in (PlanStatus.GENERATING.value, PlanStatus.SUPERSEDED.value, PlanStatus.FAILED.value):
        raise Conflict("当前状态下方案不可沿用", code="plan_not_editable")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise Unprocessable("请说明此方案为何仍适用于当前诊断/资料（至少 5 个字）", code="reason_required")
    dxs = final_diagnoses(db, enc.id)
    if not dxs:
        raise Unprocessable("还没有已确认的诊断", code="no_final_diagnoses")
    if not plan_staleness(db, plan, enc)["stale"]:
        return plan                                                        # idempotent: nothing to re-anchor
    before = {"dx_snapshot": plan.dx_snapshot_json, "dx_set_hash": plan.dx_set_hash, "inputs_version": plan.inputs_version}
    plan.dx_set_hash, plan.dx_snapshot_json = dx_set_hash(dxs), dx_snapshot(dxs)
    plan.inputs_version, plan.inputs_hash = enc.inputs_version, enc.inputs_hash
    plan.status = PlanStatus.DRAFT.value
    plan.confirmed_by = plan.confirmed_at = None
    plan.confirmed_content_hash = ""
    plan.row_version += 1
    audit.record(db, actor, "plan.rebase", entity_type="plan", entity_id=plan.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"医师确认沿用治疗方案 v{plan.version_no}（诊断/资料已变化）：{reason}",
                 detail={"reason": reason, "previous": before, "now": {"dx_snapshot": plan.dx_snapshot_json,
                                                                         "dx_set_hash": plan.dx_set_hash,
                                                                         "inputs_version": plan.inputs_version}})
    return plan


def plan_content_hash(plan: TreatmentPlan) -> str:
    return hash_obj({"c": plan.content_json, "t": [[t.name, t.status, t.acupoints_json, t.detail_json]
                                                   for t in plan.therapy_items if t.status != "rejected"]})


def edit_plan_content(db: Session, actor: Actor, plan: TreatmentPlan, enc: Encounter, content: dict, *,
                      expected_version: int | None) -> TreatmentPlan:
    from server.deps import check_version
    check_version(plan.row_version, expected_version, "治疗方案", db=db, obj=plan)
    if plan.status in (PlanStatus.GENERATING.value, PlanStatus.SUPERSEDED.value, PlanStatus.FAILED.value):
        raise Conflict("当前状态下方案不可编辑", code="plan_not_editable")
    merged = dict(plan.content_json or empty_content())
    for k, v in content.items():
        if k in empty_content():
            merged[k] = v
    plan.content_json = merged
    if plan.status == PlanStatus.CONFIRMED.value:
        plan.status = PlanStatus.DRAFT.value                # edits invalidate the confirmation
        plan.confirmed_by, plan.confirmed_at, plan.confirmed_content_hash = None, None, ""
    plan.row_version += 1
    audit.record(db, actor, "plan.edit", entity_type="plan", entity_id=plan.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary="编辑治疗方案", detail={"keys": sorted(content.keys())})
    return plan


def review_therapy_item(db: Session, actor: Actor, item: TherapyItem, *, action: str, edits: dict | None = None,
                        note: str | None = None) -> TherapyItem:
    if action not in ("accept", "modify", "reject", "revert"):
        raise Unprocessable("action 必须是 accept/modify/reject/revert", code="bad_action")
    if action == "modify":
        if not edits:
            raise Unprocessable("修改时需提供修改内容", code="edits_required")
        if not item.original_json:
            item.original_json = {"name": item.name, "category": item.category, "acupoints": item.acupoints_json,
                                  "detail": item.detail_json, "indications": item.indications,
                                  "contraindications": item.contraindications}
        for k in ("name", "category", "indications", "contraindications", "rationale"):
            if k in edits and isinstance(edits[k], str):
                setattr(item, k, edits[k].strip())
        if "acupoints" in edits:
            item.acupoints_json = [str(x).strip() for x in edits["acupoints"] if str(x).strip()]
        if "detail" in edits and isinstance(edits["detail"], dict):
            item.detail_json = {**(item.detail_json or {}), **edits["detail"]}
        item.status = "modified"
    elif action == "accept":
        item.status = "accepted"
    elif action == "reject":
        item.status = "rejected"
    else:
        item.status = "pending"
    if note is not None:
        item.doctor_note = note
    item.reviewed_by, item.reviewed_at = actor.id, utcnow()
    plan = db.get(TreatmentPlan, item.plan_id)
    if plan.status == PlanStatus.CONFIRMED.value:           # editing a reviewed item invalidates confirmation
        plan.status = PlanStatus.DRAFT.value
        plan.confirmed_by, plan.confirmed_at, plan.confirmed_content_hash = None, None, ""
        plan.row_version += 1
    audit.record(db, actor, f"therapy.{action}", entity_type="therapy_item", entity_id=item.id,
                 encounter_id=item.encounter_id, summary=f"{action}：{item.name}")
    return item
