"""Prescription documents: structured editing, safety binding, signing, pharmacy workflow, mock-HIS.

Safety binding
  Every mutation recomputes `content_hash` over everything that matters clinically (items, herbal header,
  type, patient allergies/status the rules used). `safety_json` stores the issues + the hash they were computed
  for. Signing re-evaluates from scratch (never trusts stored issues) and requires:
      block    → none remaining
      critical → written reason (≥5 chars) per issue
      warning  → acknowledgement per issue
  Acknowledgements are keyed by the issue's stable `key`; if an edit changes/removes an issue its ack is dropped.

State machine (server enforced):
  draft ──sign──▶ signed ──(pharmacy picks up)──▶ pharmacy_review ──approve──▶ approved ──dispense──▶ dispensed
    ▲                │ ▲                              │ return
    └──── unsign ────┘ └──────────── returned ◀───────┘     (returned → doctor edits → draft → sign again)
  draft|signed|returned|pharmacy_review|approved ──void(reason)──▶ voided
  A dispensed prescription is immutable (only printing / read).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import (Drug, Encounter, Patient, Prescription, PrescriptionEvent, PrescriptionItem, TreatmentPlan, User)
from server.domain import plans as plan_svc
from server.domain.encounters import active_allergy_names, calc_age
from server.domain.enums import PlanStatus, RxStatus, RxType, Severity
from server.domain.ids import hash_obj, utcnow
from server.domain.numbering import next_rx_no
from server.errors import Conflict, Forbidden, NotFound, Unprocessable
from server.safety import engine
from server.safety.engine import Context, Item, Issue
from server.safety.data import reference as R
from server.security import audit
from server.security.audit import Actor

EDITABLE = (RxStatus.DRAFT.value, RxStatus.RETURNED.value)
LIVE = (RxStatus.DRAFT.value, RxStatus.SIGNED.value, RxStatus.PHARMACY_REVIEW.value, RxStatus.APPROVED.value,
        RxStatus.RETURNED.value, RxStatus.DISPENSED.value)

_TRANSITIONS: dict[str, set[str]] = {
    RxStatus.DRAFT.value: {RxStatus.SIGNED.value, RxStatus.VOIDED.value},
    RxStatus.SIGNED.value: {RxStatus.PHARMACY_REVIEW.value, RxStatus.DRAFT.value, RxStatus.VOIDED.value},
    RxStatus.PHARMACY_REVIEW.value: {RxStatus.APPROVED.value, RxStatus.RETURNED.value, RxStatus.VOIDED.value},
    RxStatus.APPROVED.value: {RxStatus.DISPENSED.value, RxStatus.RETURNED.value, RxStatus.VOIDED.value},
    RxStatus.RETURNED.value: {RxStatus.DRAFT.value, RxStatus.VOIDED.value},
    RxStatus.DISPENSED.value: set(),
    RxStatus.VOIDED.value: set(),
}


# --------------------------------------------------------------------------- catalog helpers
def catalog_index(db: Session) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for d in db.scalars(select(Drug).where(Drug.active.is_(True))):
        entry = {"id": d.id, "kind": d.kind, "name": d.name, "drug_class": d.drug_class,
                 "allergen_classes": d.allergen_classes_json or [], "pregnancy_note": d.pregnancy_note,
                 "dose_min_g": d.dose_min_g, "dose_max_g": d.dose_max_g, "special_method": d.special_method,
                 "unit": d.unit, "spec": d.spec, "form": d.form}
        idx.setdefault(d.name.lower(), entry)
        if d.generic_name:
            idx.setdefault(d.generic_name.lower(), entry)
        for a in d.aliases_json or []:
            idx.setdefault(str(a).lower(), entry)
    return idx


def to_engine_items(rx: Prescription, rx_type: str | None = None) -> list[Item]:
    return [Item(index=i.line_no - 1, name=i.name, kind=rx.rx_type, dose_text=i.dose_text, dose_g=i.dose_g,
                 frequency=i.frequency, days=i.days, route=i.route, special=i.special, quantity=i.quantity,
                 rx_id=rx.id) for i in rx.items]


def build_context(db: Session, rx: Prescription, enc: Encounter) -> Context:
    p: Patient = enc.patient
    spec = enc.special_status_json or {}
    others: list[Item] = []
    for o in db.scalars(select(Prescription).where(Prescription.encounter_id == enc.id, Prescription.id != rx.id,
                                                   Prescription.status.in_(LIVE))):
        others += to_engine_items(o)
    cur = [m.get("name", "") if isinstance(m, dict) else str(m) for m in (enc.current_meds_json or [])]
    return Context(allergies=active_allergy_names(p), pregnant=spec.get("pregnant"), lactating=spec.get("lactating"),
                   renal_impairment=spec.get("renal"), hepatic_impairment=spec.get("hepatic"), gender=p.gender,
                   age=calc_age(p), current_meds=[c for c in cur if c], other_items=others,
                   catalog=catalog_index(db), diagnoses=[d["name"] for d in (enc.patient_summary_json or {}).get("dx", [])])


def preview_plan_safety(db: Session, plan: TreatmentPlan, enc: Encounter) -> dict:
    """Run the SAME rule engine over the plan's drafted medication, before any prescription exists, so the doctor
    is warned while reviewing the plan (not only at the last click). Pure read: nothing is stored or acknowledged.
    Prescriptions already created FROM this plan are excluded from the "other prescriptions" context (they would
    otherwise duplicate the very items being previewed)."""
    p: Patient = enc.patient
    spec = enc.special_status_json or {}
    others: list[Item] = []
    for o in db.scalars(select(Prescription).where(Prescription.encounter_id == enc.id, Prescription.status.in_(LIVE))):
        if o.plan_id != plan.id:
            others += to_engine_items(o)
    cur = [m.get("name", "") if isinstance(m, dict) else str(m) for m in (enc.current_meds_json or [])]
    ctx = Context(allergies=active_allergy_names(p), pregnant=spec.get("pregnant"), lactating=spec.get("lactating"),
                  renal_impairment=spec.get("renal"), hepatic_impairment=spec.get("hepatic"), gender=p.gender, age=calc_age(p),
                  current_meds=[c for c in cur if c], other_items=others, catalog=catalog_index(db))
    c = plan.content_json or {}
    out: dict[str, Any] = {"rules_version": R.RULES_VERSION, "groups": []}

    def add(label: str, rx_type: str, items: list[Item], meta: dict | None = None, ref: str = "") -> None:
        if not items:
            return
        issues = engine.evaluate(rx_type, items, ctx, meta or {})
        out["groups"].append({"label": label, "rx_type": rx_type, "ref": ref, "issues": [i.as_dict() for i in issues],
                              "summary": engine.summarize(issues)})

    def med_items(rows: list[dict], kind: str) -> list[Item]:
        return [Item(index=n, name=str(m.get("name", "")), kind=kind, dose_text=str(m.get("dose_text", "") or ""), frequency=str(m.get("frequency", "") or ""),
                     route=str(m.get("route", "") or ""), days=_i(m.get("days"))) for n, m in enumerate(rows)]

    add("西药", RxType.WESTERN.value, med_items((c.get("medication") or {}).get("western", []), "western"), ref="western")
    add("中成药", RxType.PATENT.value, med_items((c.get("medication") or {}).get("patent", []), "patent"), ref="patent")
    for n, h in enumerate(c.get("herbal") or []):
        items = [Item(index=k, name=str(x.get("name", "")), kind="herb", dose_g=_f(x.get("dose_g")), special=str(x.get("special", "") or ""))
                 for k, x in enumerate(h.get("herbs", []))]
        add(f"中药方：{h.get('title') or '中药方'}", RxType.HERBAL.value, items,
            {"doses_count": h.get("doses_count"), "decoction": h.get("decoction", ""), "herbal_usage": h.get("usage", "")}, ref=f"herbal:{n}")
    tot = {sv: 0 for sv in Severity.values()}
    for g in out["groups"]:
        for k, v in g["summary"].items():
            tot[k] = tot.get(k, 0) + v
    out["summary"] = tot
    return out


def rx_meta(rx: Prescription) -> dict:
    return {"doses_count": rx.doses_count, "decoction": rx.decoction, "herbal_usage": rx.herbal_usage}


def compute_content_hash(rx: Prescription, enc: Encounter) -> str:
    return hash_obj({
        "t": rx.rx_type, "meta": rx_meta(rx), "title": rx.title,
        "items": [[i.name, i.dose_text, i.dose_g, i.frequency, i.days, i.route, i.special, i.quantity,
                   i.spec, i.instructions] for i in rx.items],
        "ctx": [sorted(active_allergy_names(enc.patient)), enc.special_status_json, enc.current_meds_json,
                enc.patient.gender, calc_age(enc.patient)],
        "rules": R.RULES_VERSION,
    })


def evaluate(db: Session, rx: Prescription, enc: Encounter) -> list[Issue]:
    return engine.evaluate(rx.rx_type, to_engine_items(rx), build_context(db, rx, enc), rx_meta(rx))


def refresh_safety(db: Session, rx: Prescription, enc: Encounter) -> list[Issue]:
    """Re-evaluate and store; keep acknowledgements only for issues that still exist unchanged."""
    issues = evaluate(db, rx, enc)
    old = (rx.safety_json or {}).get("acks", {}) or {}
    keys = {i.key for i in issues}
    rx.content_hash = compute_content_hash(rx, enc)
    rx.safety_json = {"rules_version": R.RULES_VERSION, "content_hash": rx.content_hash, "evaluated_at": utcnow().isoformat(),
                      "summary": engine.summarize(issues), "issues": [i.as_dict() for i in issues],
                      "acks": {k: v for k, v in old.items() if k in keys}}
    return issues


# --------------------------------------------------------------------------- CRUD
def _apply_items(rx: Prescription, items: list[dict]) -> None:
    from sqlalchemy.orm import object_session
    rx.items.clear()
    sess = object_session(rx)
    if sess is not None:
        # Flush the DELETE of the replaced rows BEFORE inserting the new ones, otherwise SQLAlchemy emits the
        # INSERTs first and trips UNIQUE(prescription_id, line_no) on every edit.
        sess.flush()
    for n, it in enumerate(items, start=1):
        name = str(it.get("name", "")).strip()
        dose_g = _f(it.get("dose_g"))
        rx.items.append(PrescriptionItem(
            line_no=n, drug_id=it.get("drug_id") or None, name=name, spec=str(it.get("spec", "") or ""),
            form=str(it.get("form", "") or ""), dose_text=str(it.get("dose_text", "") or "").strip(),
            frequency=str(it.get("frequency", "") or "").strip(), route=str(it.get("route", "") or "").strip(),
            days=_i(it.get("days")), quantity=_f(it.get("quantity")), quantity_unit=str(it.get("quantity_unit", "") or ""),
            dose_g=dose_g, special=str(it.get("special", "") or "").strip(), role=str(it.get("role", "") or "")[:4],
            instructions=str(it.get("instructions", "") or "")[:200], reason=str(it.get("reason", "") or "")[:200],
            in_catalog=bool(it.get("in_catalog", False)), flags_json=list(it.get("flags", []) or [])))


def _f(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        raise Unprocessable(f"数值格式不正确：{x!r}", code="bad_number")


def _i(x) -> int | None:
    v = _f(x)
    return None if v is None else int(v)


def create_prescription(db: Session, actor: Actor, enc: Encounter, doctor: User, *, rx_type: str, title: str = "",
                        items: list[dict] | None = None, plan_id: str | None = None, header: dict | None = None,
                        notes: str = "", ai_generated: bool = False, formula_type: str = "", formula_source: str = "",
                        acknowledge_stale: str = "") -> Prescription:
    if rx_type not in RxType.values():
        raise Unprocessable("处方类型必须是 western / patent / herbal", code="bad_rx_type")
    if enc.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("就诊已结束，不能再创建处方", code="encounter_closed")
    plan = None
    if plan_id:
        plan = db.get(TreatmentPlan, plan_id)
        if plan is None or plan.encounter_id != enc.id:
            raise NotFound("治疗方案不存在", code="plan_not_found")
        if plan.status == PlanStatus.SUPERSEDED.value:
            raise Conflict("所选治疗方案已被新版本取代", code="plan_inactive")
        st = plan_svc.plan_staleness(db, plan, enc)
        if st["stale"] and len(acknowledge_stale.strip()) < 5:
            raise Conflict("所选治疗方案已过期（诊断或临床资料已变更）。请重新生成方案，或填写理由后继续",
                           code="plan_stale", details=st)
    dxs = plan_svc.final_diagnoses(db, enc.id)
    h = header or {}
    rx = Prescription(rx_no=next_rx_no(db), encounter_id=enc.id, patient_id=enc.patient_id, doctor_id=doctor.id,
                      plan_id=plan.id if plan else None, rx_type=rx_type, title=title or _default_title(rx_type),
                      formula_type=formula_type, formula_source=formula_source,
                      dx_links_json=[{"id": d.id, "name": d.name, "module": d.module} for d in dxs],
                      doses_count=_i(h.get("doses_count")), decoction=str(h.get("decoction", "") or ""),
                      herbal_usage=str(h.get("herbal_usage", "") or ""), notes=notes, ai_generated=ai_generated,
                      created_by=actor.id)
    db.add(rx)
    db.flush()
    _apply_items(rx, items or [])
    db.flush()
    db.refresh(rx)
    refresh_safety(db, rx, enc)
    _event(db, rx, actor, "", RxStatus.DRAFT.value, "create", "新建处方草稿" + ("（AI 草拟）" if ai_generated else ""))
    audit.record(db, actor, "rx.create", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"新建{_default_title(rx_type)} {rx.rx_no}",
                 detail={"plan_id": plan_id, "items": len(rx.items), "ai": ai_generated,
                         "stale_ack": acknowledge_stale or None})
    return rx


def _default_title(t: str) -> str:
    return {"western": "西药处方", "patent": "中成药处方", "herbal": "中药饮片处方"}.get(t, "处方")


def update_prescription(db: Session, actor: Actor, rx: Prescription, enc: Encounter, *, items: list[dict] | None,
                        header: dict | None, title: str | None, notes: str | None, expected_version: int | None) -> Prescription:
    from server.deps import check_version
    check_version(rx.row_version, expected_version, "处方", db=db, obj=rx)
    if rx.status not in EDITABLE:
        raise Conflict(f"处方当前状态为「{rx.status}」，不可编辑（已签署的处方需先撤回签署）", code="rx_not_editable")
    if items is not None:
        _apply_items(rx, items)
    if header:
        if "doses_count" in header:
            rx.doses_count = _i(header["doses_count"])
        for k in ("decoction", "herbal_usage"):
            if k in header:
                setattr(rx, k, str(header[k] or ""))
    if title is not None:
        rx.title = title.strip()[:128]
    if notes is not None:
        rx.notes = notes
    db.flush()
    db.refresh(rx)
    refresh_safety(db, rx, enc)
    rx.row_version += 1
    audit.record(db, actor, "rx.edit", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"编辑处方 {rx.rx_no}", detail={"items": len(rx.items)})
    return rx


def acknowledge(db: Session, actor: Actor, rx: Prescription, enc: Encounter, acks: dict[str, str]) -> Prescription:
    if rx.status not in EDITABLE:
        raise Conflict("处方不可编辑，无法确认提示", code="rx_not_editable")
    cur = {i["key"] for i in (rx.safety_json or {}).get("issues", [])}
    unknown = [k for k in acks if k not in cur]
    if unknown:
        raise Unprocessable("提示项已变化，请刷新后重试", code="stale_issue_keys", details={"unknown": unknown})
    sj = dict(rx.safety_json)
    sj["acks"] = {**sj.get("acks", {}), **{k: (v or "").strip() for k, v in acks.items()}}
    rx.safety_json = sj
    rx.row_version += 1
    audit.record(db, actor, "rx.ack", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"确认 {len(acks)} 项用药提示", detail={"keys": list(acks)})
    return rx


def _transition(db: Session, rx: Prescription, actor: Actor, to: str, action: str, note: str = "") -> None:
    """Atomic status change.

    The UPDATE carries `WHERE id=? AND status=<status we read> AND row_version=<version we read>`; if another
    request committed first, 0 rows match and we raise Conflict instead of both "winning". This is what makes
    double-clicks and concurrent clients unable to produce two signatures / two dispenses for one prescription.
    """
    from sqlalchemy import update
    if to not in _TRANSITIONS.get(rx.status, set()):
        raise Conflict(f"处方不能从「{rx.status}」变更为「{to}」", code="bad_transition",
                       details={"from": rx.status, "to": to})
    frm, ver = rx.status, rx.row_version
    # flush pending attribute changes first, then CAS on the persisted (status, row_version)
    db.flush()
    res = db.execute(update(Prescription).where(Prescription.id == rx.id, Prescription.status == frm,
                                                 Prescription.row_version == ver)
                     .values(status=to, row_version=ver + 1).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise Conflict("该处方刚刚被其他操作修改（可能是重复点击或他人同时操作），请刷新后重试", code="stale_version",
                       details={"expected_status": frm})
    db.refresh(rx)
    _event(db, rx, actor, frm, to, action, note)


def _event(db: Session, rx: Prescription, actor: Actor, frm: str, to: str, action: str, note: str = "") -> None:
    db.add(PrescriptionEvent(prescription_id=rx.id, actor_id=actor.id, actor_name=actor.name, from_status=frm,
                             to_status=to, action=action, note=note[:400]))


# --------------------------------------------------------------------------- sign / unsign
def sign(db: Session, actor: Actor, rx: Prescription, enc: Encounter, doctor: User, *, expected_version: int | None) -> Prescription:
    from server.deps import check_version
    check_version(rx.row_version, expected_version, "处方", db=db, obj=rx)
    if rx.doctor_id != doctor.id:
        raise Forbidden("只能签署本人开具的处方", code="not_prescriber")
    if rx.status not in EDITABLE:
        raise Conflict("处方不在可签署状态", code="bad_transition")
    if enc.status == "CANCELLED":
        raise Conflict("就诊已取消", code="encounter_closed")
    if enc.status == "COMPLETED" and not rx.review_by:
        # A finalised visit takes no NEW signatures — except for a prescription the pharmacy RETURNED (review_by is set by the
        # pharmacist): the doctor must be able to correct and re-sign it, otherwise the return would be a dead end (void only).
        raise Conflict("就诊已结束", code="encounter_closed")
    issues = refresh_safety(db, rx, enc)                       # always re-evaluate, never trust stored results
    acks = (rx.safety_json or {}).get("acks", {})
    left = engine.signing_blockers(issues, acks)
    if left:
        raise Conflict("处方存在未处理的安全提示，无法签署", code="safety_unresolved", details={"blockers": left})
    if rx.plan_id:
        plan = db.get(TreatmentPlan, rx.plan_id)
        if plan and plan.status == PlanStatus.SUPERSEDED.value:
            raise Conflict("处方关联的治疗方案已被新版本取代，请重新核对后再签署", code="plan_inactive")
    now = utcnow()
    rx.signed_by, rx.signed_at = doctor.id, now
    rx.signature_hash = hash_obj([rx.content_hash, doctor.id, now.isoformat()])
    rx.override_reasons_json = [{"key": i.key, "rule_id": i.rule_id, "severity": i.severity, "message": i.message,
                                 "reason": acks.get(i.key, "")} for i in issues if i.severity in ("critical", "warning")]
    _transition(db, rx, actor, RxStatus.SIGNED.value, "sign", f"{len(rx.override_reasons_json)} 项提示已确认")
    audit.record(db, actor, "rx.sign", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"签署处方 {rx.rx_no}",
                 detail={"signature_hash": rx.signature_hash, "content_hash": rx.content_hash,
                         "overrides": rx.override_reasons_json, "rules_version": R.RULES_VERSION})
    return rx


def unsign(db: Session, actor: Actor, rx: Prescription, enc: Encounter, *, reason: str) -> Prescription:
    if rx.status != RxStatus.SIGNED.value:
        raise Conflict("只有“已签署、药房尚未接单”的处方可以撤回签署", code="bad_transition")
    if len(reason.strip()) < 2:
        raise Unprocessable("撤回签署需填写原因", code="reason_required")
    rx.signed_by = rx.signed_at = None
    rx.signature_hash = ""
    _transition(db, rx, actor, RxStatus.DRAFT.value, "unsign", reason)
    audit.record(db, actor, "rx.unsign", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"撤回签署 {rx.rx_no}：{reason}")
    return rx


def void(db: Session, actor: Actor, rx: Prescription, enc: Encounter, *, reason: str) -> Prescription:
    if len(reason.strip()) < 2:
        raise Unprocessable("作废需填写原因", code="reason_required")
    rx.voided_by, rx.voided_at, rx.void_reason = actor.id, utcnow(), reason.strip()[:300]
    _transition(db, rx, actor, RxStatus.VOIDED.value, "void", reason)
    audit.record(db, actor, "rx.void", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"作废处方 {rx.rx_no}：{reason}")
    return rx


# --------------------------------------------------------------------------- pharmacy
def pharmacy_start(db: Session, actor: Actor, rx: Prescription) -> Prescription:
    if rx.status == RxStatus.PHARMACY_REVIEW.value and rx.review_by == actor.id:
        return rx                                                # idempotent re-click
    if rx.status != RxStatus.SIGNED.value:
        raise Conflict("只有已签署的处方可以开始审核", code="bad_transition")
    rx.review_by = actor.id
    _transition(db, rx, actor, RxStatus.PHARMACY_REVIEW.value, "pharmacy_start")
    audit.record(db, actor, "rx.pharmacy.start", entity_type="prescription", entity_id=rx.id, encounter_id=rx.encounter_id,
                 patient_id=rx.patient_id, summary=f"药房开始审核 {rx.rx_no}")
    return rx


def pharmacy_decide(db: Session, actor: Actor, rx: Prescription, *, approve: bool, note: str = "") -> Prescription:
    if rx.status != RxStatus.PHARMACY_REVIEW.value:
        raise Conflict("处方不在药房审核中", code="bad_transition")
    if not approve and len(note.strip()) < 2:
        raise Unprocessable("退回处方必须说明原因", code="reason_required")
    rx.review_by, rx.review_at, rx.review_note = actor.id, utcnow(), note.strip()[:1000]
    _transition(db, rx, actor, RxStatus.APPROVED.value if approve else RxStatus.RETURNED.value,
                "approve" if approve else "return", note)
    audit.record(db, actor, "rx.pharmacy.approve" if approve else "rx.pharmacy.return", entity_type="prescription",
                 entity_id=rx.id, encounter_id=rx.encounter_id, patient_id=rx.patient_id,
                 summary=("药房审核通过 " if approve else "药房退回 ") + rx.rx_no + (f"：{note}" if note else ""))
    return rx


def pharmacy_dispense(db: Session, actor: Actor, rx: Prescription) -> Prescription:
    if rx.status == RxStatus.DISPENSED.value:
        return rx                                                # idempotent
    if rx.status != RxStatus.APPROVED.value:
        raise Conflict("只有审核通过的处方可以发药", code="bad_transition")
    rx.dispensed_by, rx.dispensed_at = actor.id, utcnow()
    _transition(db, rx, actor, RxStatus.DISPENSED.value, "dispense")
    audit.record(db, actor, "rx.dispense", entity_type="prescription", entity_id=rx.id, encounter_id=rx.encounter_id,
                 patient_id=rx.patient_id, summary=f"发药 {rx.rx_no}")
    return rx


# --------------------------------------------------------------------------- mock HIS (clearly simulated)
def submit_mock_his(db: Session, actor: Actor, rx: Prescription, enc: Encounter) -> Prescription:
    """Simulated HIS hand-off. Never claims real synchronisation: his_mode='mock', his_status='mock_submitted'."""
    if rx.status not in (RxStatus.SIGNED.value, RxStatus.PHARMACY_REVIEW.value, RxStatus.APPROVED.value, RxStatus.DISPENSED.value):
        raise Conflict("只有已签署的处方才能提交（模拟）HIS", code="not_signed")
    if rx.his_status == "mock_submitted":
        return rx                                                # idempotent
    rx.his_mode, rx.his_status = "mock", "mock_submitted"
    rx.his_ref = "MOCK-" + hash_obj([rx.id, rx.signature_hash])[:10].upper()
    rx.his_submitted_at = utcnow()
    audit.record(db, actor, "rx.his.mock_submit", entity_type="prescription", entity_id=rx.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"模拟提交 HIS（未连接真实 HIS）{rx.rx_no}",
                 detail={"his_ref": rx.his_ref, "simulated": True})
    return rx
