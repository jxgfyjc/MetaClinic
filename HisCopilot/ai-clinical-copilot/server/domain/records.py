"""Medical record: sectioned, versioned, merge-safe, tamper-evident.

Content model — `content_json = {"sections": {key: {"text", "origin", "edited_by", "edited_at", "auto_hash"}}}`
  origin: auto   machine-assembled from facts/diagnoses/plan (safe to refresh)
          patient text copied from the patient's own submission
          doctor  anything a human typed or touched

Merge rule (the "never overwrite the doctor" guarantee):
  refresh_auto_sections() may only replace a section whose origin is `auto` AND whose text still equals the
  `auto_hash` it was generated with. A section the doctor has typed in (origin=doctor, or auto text edited)
  is left alone; the freshly generated text is returned as a *suggestion* the UI can offer side-by-side.

Versions are append-only: draft saves create `draft` versions (consecutive identical saves are no-ops),
finalising snapshots a `final` version (referencing diagnoses / plan / prescriptions / labs by id + hash),
and post-final corrections create `amendment` versions that require a written reason — the previous final is
never edited. `final_version_id` always points at the latest final/amendment.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import (Diagnosis, Encounter, LabItem, MedicalRecord, Prescription, RecordVersion, TherapyItem,
                              TreatmentPlan)
from server.domain import plans as plan_svc
from server.domain.encounters import active_allergy_names, calc_age
from server.domain.enums import EncounterStatus, PlanStatus, RecordStatus, RxStatus
from server.domain.ids import hash_obj, iso, utcnow
from server.domain.numbering import next_record_no
from server.errors import Conflict, NotFound, Unprocessable
from server.security import audit
from server.security.audit import Actor

SECTIONS = [
    ("chief_complaint", "主诉"), ("hpi", "现病史"), ("past_history", "既往史"),
    ("personal_family_history", "个人史 / 家族史"), ("allergies", "过敏史"), ("current_meds", "当前用药"),
    ("physical_exam", "体格检查"), ("tcm_four", "中医四诊"), ("labs", "辅助检查"),
    ("diagnoses", "诊断"), ("treatment", "治疗意见 / 医嘱"), ("followup", "随访 / 复诊建议"),
]
SECTION_KEYS = [k for k, _ in SECTIONS]
SECTION_TITLES = dict(SECTIONS)


# --------------------------------------------------------------------------- auto assembly
def _lab_lines(db: Session, enc: Encounter) -> str:
    labs = db.scalars(select(LabItem).where(LabItem.encounter_id == enc.id, LabItem.deleted_at.is_(None))
                      .order_by(LabItem.sort_order, LabItem.id)).all()
    if not labs:
        return ""
    out = []
    for l in labs:
        flag = {"H": "↑", "L": "↓"}.get(l.flag, "")
        ref = f"（参考 {l.ref_text}）" if l.ref_text else ""
        mark = "" if l.verified else "【未核对】"
        out.append(f"{l.name} {l.value_text}{l.unit} {flag}{ref}{mark}".strip())
    return "；".join(out)


def _dx_lines(dxs: list[Diagnosis]) -> str:
    west = [d for d in dxs if d.module == "western"]
    tcm = [d for d in dxs if d.module == "tcm"]
    lines = []
    if west:
        lines.append("西医诊断：" + "；".join(f"{d.name}{'（主要）' if d.is_primary else ''}" for d in west))
    if tcm:
        lines.append("中医诊断/证型：" + "；".join(f"{d.name}{'（主要）' if d.is_primary else ''}" for d in tcm))
    return "\n".join(lines)


def _tcm_four(enc: Encounter) -> str:
    f = enc.tcm_four_json or {}
    parts = [f"{k}：{f[k]}" for k in ("望", "闻", "问", "切", "舌象", "脉象") if f.get(k)]
    return "；".join(parts)


def _treatment_text(db: Session, enc: Encounter) -> str:
    plan = plan_svc.live_plan(db, enc.id)
    lines: list[str] = []
    if plan and plan.status == PlanStatus.CONFIRMED.value:
        c = plan.content_json or {}
        if c.get("summary"):
            lines.append("方案概述：" + c["summary"])
        for t in plan.therapy_items:
            if t.status in ("accepted", "modified"):
                ap = "、".join(t.acupoints_json or [])
                d = t.detail_json or {}
                seg = [f"{t.category}·{t.name}"] + ([f"穴位 {ap}"] if ap else []) + \
                      [f"{d[k]}" for k in ("manipulation", "duration", "frequency", "course") if d.get(k)]
                lines.append("理疗/康复：" + "，".join(seg))
        for x in (c.get("lifestyle") or []):
            lines.append("生活调护：" + str(x))
        if c.get("followup_advice"):
            lines.append("复诊建议：" + c["followup_advice"])
    for rx in db.scalars(select(Prescription).where(Prescription.encounter_id == enc.id,
                         Prescription.status.in_([RxStatus.SIGNED.value, RxStatus.PHARMACY_REVIEW.value,
                                                  RxStatus.APPROVED.value, RxStatus.DISPENSED.value]))
                         .order_by(Prescription.created_at)):
        if rx.rx_type == "herbal":
            herbs = "、".join(f"{i.name}{i.dose_g:g}g{('(' + i.special + ')') if i.special else ''}" for i in rx.items if i.dose_g)
            lines.append(f"处方 {rx.rx_no}（中药饮片）{rx.title}：{herbs}；共 {rx.doses_count or '?'} 剂；{rx.decoction}")
        else:
            drugs = "；".join(f"{i.name} {i.dose_text} {i.frequency} {i.route} {i.days or ''}{'天' if i.days else ''}".strip()
                              for i in rx.items)
            lines.append(f"处方 {rx.rx_no}（{'中成药' if rx.rx_type == 'patent' else '西药'}）：{drugs}")
    return "\n".join(lines)


def assemble_sections(db: Session, enc: Encounter) -> dict[str, str]:
    p = enc.patient
    meds = "；".join(f"{m.get('name', '')} {m.get('dose', '')} {m.get('frequency', '')}".strip()
                    for m in (enc.current_meds_json or []) if m.get("name")) if isinstance(enc.current_meds_json, list) else ""
    return {
        "chief_complaint": enc.chief_complaint,
        "hpi": enc.hpi,
        "past_history": enc.past_history or ("、".join(p.chronic_conditions_json or [])),
        "personal_family_history": enc.personal_family_history,
        "allergies": "、".join(active_allergy_names(p)) or "未记录过敏史（请向患者核实）",
        "current_meds": meds,
        "physical_exam": enc.physical_exam,
        "tcm_four": _tcm_four(enc),
        "labs": _lab_lines(db, enc),
        "diagnoses": _dx_lines(plan_svc.final_diagnoses(db, enc.id)),
        "treatment": _treatment_text(db, enc),
        "followup": ((plan_svc.live_plan(db, enc.id).content_json or {}).get("followup_advice", "")
                     if plan_svc.live_plan(db, enc.id) else ""),
    }


def _sec(text: str, origin: str, by: str = "", auto_hash: str = "") -> dict:
    return {"text": text, "origin": origin, "edited_by": by, "edited_at": iso(utcnow()), "auto_hash": auto_hash}


# --------------------------------------------------------------------------- container
def get_or_create(db: Session, actor: Actor, enc: Encounter) -> MedicalRecord:
    rec = db.scalar(select(MedicalRecord).where(MedicalRecord.encounter_id == enc.id))
    if rec:
        return rec
    rec = MedicalRecord(record_no=next_record_no(db), encounter_id=enc.id, patient_id=enc.patient_id,
                        doctor_id=enc.doctor_id)
    db.add(rec)
    db.flush()
    auto = assemble_sections(db, enc)
    content = {"sections": {k: _sec(auto[k], "auto" if auto[k] else "auto", auto_hash=hash_obj(auto[k])) for k in SECTION_KEYS}}
    v = _append_version(db, rec, kind="draft", content=content, author_id="system", author_name="系统（自动汇总）",
                        note="就诊资料自动汇总")
    rec.working_version_id = v.id
    audit.record(db, actor, "record.create", entity_type="record", entity_id=rec.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"创建病历草稿 {rec.record_no}")
    return rec


def _append_version(db: Session, rec: MedicalRecord, *, kind: str, content: dict, author_id: str, author_name: str,
                    note: str = "", snapshot: dict | None = None, signature_hash: str = "") -> RecordVersion:
    last = db.scalar(select(RecordVersion).where(RecordVersion.record_id == rec.id).order_by(RecordVersion.version_no.desc()).limit(1))
    v = RecordVersion(record_id=rec.id, version_no=(last.version_no + 1) if last else 1, kind=kind,
                      parent_version_id=last.id if last else None, content_json=content, content_hash=hash_obj(content),
                      author_id=author_id, author_name=author_name, note=note[:400], snapshot_json=snapshot or {},
                      signature_hash=signature_hash)
    db.add(v)
    db.flush()
    rec.updated_at = utcnow()
    return v


def working_version(db: Session, rec: MedicalRecord) -> RecordVersion:
    return db.get(RecordVersion, rec.working_version_id or rec.final_version_id)


def save_draft(db: Session, actor: Actor, rec: MedicalRecord, enc: Encounter, sections: dict[str, str], *,
               author_name: str, base_version_no: int | None) -> tuple[RecordVersion, bool]:
    """Doctor saves edited sections. Returns (version, created_new). Never touches non-supplied sections."""
    if rec.status == RecordStatus.FINALIZED.value:
        raise Conflict("病历已定稿：如需修改请使用“更正”并填写更正原因", code="record_finalized")
    cur = working_version(db, rec)
    if base_version_no is not None and base_version_no != cur.version_no:
        raise Conflict("病历已在其他窗口被更新，请刷新后再保存", code="stale_version",
                       details={"current_version": cur.version_no, "expected_version": base_version_no})
    unknown = [k for k in sections if k not in SECTION_KEYS]
    if unknown:
        raise Unprocessable(f"未知病历段落：{unknown}", code="bad_section")
    content = {"sections": {k: dict(v) for k, v in cur.content_json["sections"].items()}}
    changed = []
    for k, text in sections.items():
        text = (text or "").rstrip()
        old = content["sections"].get(k, {})
        if old.get("text", "") != text:
            content["sections"][k] = _sec(text, "doctor", actor.id, old.get("auto_hash", ""))
            changed.append(k)
    if not changed:
        return cur, False
    v = _append_version(db, rec, kind="draft", content=content, author_id=actor.id, author_name=author_name,
                        note="编辑：" + "、".join(SECTION_TITLES[k] for k in changed))
    rec.working_version_id = v.id
    audit.record(db, actor, "record.save", entity_type="record", entity_id=rec.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"保存病历 v{v.version_no}", detail={"sections": changed})
    return v, True


def refresh_auto_sections(db: Session, actor: Actor, rec: MedicalRecord, enc: Encounter) -> dict:
    """Re-assemble from current facts. Auto, unedited sections are replaced; human-edited ones are preserved and
    the fresh text is returned as a suggestion. Returns {updated:[...], suggestions:{key:text}, version}."""
    if rec.status == RecordStatus.FINALIZED.value:
        raise Conflict("病历已定稿", code="record_finalized")
    cur = working_version(db, rec)
    auto = assemble_sections(db, enc)
    secs = {k: dict(v) for k, v in cur.content_json["sections"].items()}
    updated, suggestions = [], {}
    for k in SECTION_KEYS:
        new_text, old = auto[k], secs.get(k)
        new_hash = hash_obj(new_text)
        if old is None:
            secs[k] = _sec(new_text, "auto", auto_hash=new_hash)
            updated.append(k)
            continue
        pristine = old.get("origin") == "auto" and hash_obj(old.get("text", "")) == old.get("auto_hash")
        if pristine:
            if old["text"] != new_text:
                secs[k] = _sec(new_text, "auto", auto_hash=new_hash)
                updated.append(k)
        elif old.get("text", "") != new_text and new_text:
            suggestions[k] = new_text                        # doctor-owned: never overwrite, only suggest
            secs[k]["auto_hash"] = old.get("auto_hash", "")
    version = cur
    if updated:
        version = _append_version(db, rec, kind="draft", content={"sections": secs}, author_id="system",
                                  author_name="系统（自动汇总）", note="刷新自动汇总：" + "、".join(SECTION_TITLES[k] for k in updated))
        rec.working_version_id = version.id
        audit.record(db, actor, "record.refresh", entity_type="record", entity_id=rec.id, encounter_id=enc.id,
                     patient_id=enc.patient_id, summary="刷新病历自动汇总", detail={"updated": updated, "kept": list(suggestions)})
    return {"updated": updated, "suggestions": suggestions, "version": version}


def accept_suggestion(db: Session, actor: Actor, rec: MedicalRecord, enc: Encounter, key: str, *, mode: str,
                      author_name: str) -> RecordVersion:
    """mode=replace: take the freshly assembled text; mode=append: add it below the doctor's text."""
    if key not in SECTION_KEYS:
        raise Unprocessable("未知段落", code="bad_section")
    fresh = assemble_sections(db, enc)[key]
    cur = working_version(db, rec)
    old = cur.content_json["sections"].get(key, {}).get("text", "")
    text = fresh if mode == "replace" else (old.rstrip() + "\n" + fresh if old else fresh)
    v, _ = save_draft(db, actor, rec, enc, {key: text}, author_name=author_name, base_version_no=cur.version_no)
    return v


# --------------------------------------------------------------------------- finalize / amend
def _freeze_snapshot(db: Session, enc: Encounter) -> dict:
    dxs = plan_svc.final_diagnoses(db, enc.id)
    plan = plan_svc.live_plan(db, enc.id)
    rxs = db.scalars(select(Prescription).where(Prescription.encounter_id == enc.id,
                     Prescription.status.in_([RxStatus.SIGNED.value, RxStatus.PHARMACY_REVIEW.value, RxStatus.APPROVED.value,
                                              RxStatus.DISPENSED.value])).order_by(Prescription.created_at)).all()
    labs = db.scalars(select(LabItem).where(LabItem.encounter_id == enc.id, LabItem.deleted_at.is_(None))).all()
    return {
        "patient": {"id": enc.patient.id, "mrn": enc.patient.mrn, "name": enc.patient.name, "gender": enc.patient.gender,
                    "age": calc_age(enc.patient), "phone": enc.patient.phone},
        "encounter": {"id": enc.id, "no": enc.encounter_no, "created_at": iso(enc.created_at), "visit_type": enc.visit_type,
                      "source": enc.source, "doctor": enc.doctor.display_name if enc.doctor else "",
                      "doctor_title": enc.doctor.title if enc.doctor else "", "license_no": enc.doctor.license_no if enc.doctor else ""},
        "diagnoses": [{"id": d.id, "module": d.module, "name": d.name, "status": d.status, "primary": d.is_primary,
                       "ai_original": d.original_name or None, "doctor_note": d.doctor_note} for d in dxs],
        "plan": ({"id": plan.id, "version": plan.version_no, "status": plan.status, "hash": plan.confirmed_content_hash,
                  "content": plan.content_json,
                  "therapy": [{"name": t.name, "category": t.category, "status": t.status, "acupoints": t.acupoints_json,
                               "detail": t.detail_json, "contraindications": t.contraindications}
                              for t in plan.therapy_items if t.status in ("accepted", "modified")]} if plan else None),
        "prescriptions": [{"id": r.id, "no": r.rx_no, "type": r.rx_type, "status": r.status, "title": r.title,
                           "content_hash": r.content_hash, "signature_hash": r.signature_hash,
                           "signed_by": r.doctor.display_name, "signed_at": iso(r.signed_at),
                           "items": [{"name": i.name, "dose_text": i.dose_text, "dose_g": i.dose_g, "frequency": i.frequency,
                                      "route": i.route, "days": i.days, "special": i.special, "spec": i.spec}
                                     for i in r.items],
                           "doses_count": r.doses_count, "decoction": r.decoction, "herbal_usage": r.herbal_usage,
                           "his": {"mode": r.his_mode, "status": r.his_status, "ref": r.his_ref}}
                          for r in rxs],
        "labs": [{"name": l.name, "value": l.value_text, "unit": l.unit, "flag": l.flag, "ref": l.ref_text,
                  "verified": l.verified} for l in labs],
        "allergies": active_allergy_names(enc.patient),
    }


SOFT_BLOCKERS = {"plan_unconfirmed", "labs_unverified"}


def finalize_blockers(db: Session, enc: Encounter) -> list[dict]:
    out = []
    if not plan_svc.final_diagnoses(db, enc.id):
        out.append({"code": "no_diagnosis", "message": "尚无最终诊断"})
    pend = db.scalar(select(Diagnosis.id).where(Diagnosis.encounter_id == enc.id, Diagnosis.status == "pending").limit(1))
    if pend:
        out.append({"code": "pending_diagnoses", "message": "仍有 AI 诊断候选未审核（接受/修改/驳回）"})
    plan = plan_svc.live_plan(db, enc.id)
    if plan:
        st = plan_svc.plan_staleness(db, plan, enc)
        if plan.status == PlanStatus.GENERATING.value:
            out.append({"code": "plan_generating", "message": "治疗方案仍在生成"})
        elif st["stale"]:
            out.append({"code": "plan_stale", "message": "治疗方案已过期（诊断或临床资料有变更），请重新生成或确认"})
        elif plan.status == PlanStatus.DRAFT.value:
            out.append({"code": "plan_unconfirmed", "message": "治疗方案尚未确认"})
    for rx in db.scalars(select(Prescription).where(Prescription.encounter_id == enc.id, Prescription.status.in_(
            [RxStatus.DRAFT.value, RxStatus.RETURNED.value]))):
        out.append({"code": "rx_unsigned", "message": f"处方 {rx.rx_no}（{rx.title}）尚未签署；如不需要请作废"})
    unverified = db.scalar(select(LabItem.id).where(LabItem.encounter_id == enc.id, LabItem.deleted_at.is_(None),
                                                    LabItem.source == "ocr", LabItem.verified.is_(False)).limit(1))
    if unverified:
        out.append({"code": "labs_unverified", "message": "存在尚未核对的 OCR 检验结果（对照原图核对或删除）"})
    return out


def finalize(db: Session, actor: Actor, rec: MedicalRecord, enc: Encounter, doctor, *, ignore: list[str] | None = None) -> RecordVersion:
    if rec.status == RecordStatus.FINALIZED.value:
        return db.get(RecordVersion, rec.final_version_id)          # idempotent
    # Only SOFT blockers can be knowingly waived by the doctor (and the waiver is audited). HARD blockers (no final
    # diagnosis, unreviewed candidates, plan generating/stale, unsigned prescriptions) can never be bypassed via `ignore`.
    ignore = set(ignore or []) & SOFT_BLOCKERS
    blockers = [b for b in finalize_blockers(db, enc) if b["code"] not in ignore]
    if blockers:
        hard = [b for b in blockers if b["code"] not in SOFT_BLOCKERS]
        raise Conflict("病历暂不能定稿" if hard else "病历定稿前请先处理以下事项", code="finalize_blocked", details={"blockers": blockers})
    waived = [b["code"] for b in finalize_blockers(db, enc) if b["code"] in ignore]
    # The auto-assembled sections may predate the signed prescriptions / confirmed plan / verified labs. Refresh the
    # PRISTINE auto sections so the frozen record reflects what was actually signed. Doctor-edited sections are
    # untouched (refresh never overwrites them); where the doctor's text and the live facts differ, the final
    # version keeps the doctor's wording and the snapshot (below) holds the authoritative structured data.
    refresh_auto_sections(db, actor, rec, enc)
    cur = working_version(db, rec)
    snap = _freeze_snapshot(db, enc)
    sig = hash_obj([cur.content_hash, hash_obj(snap), doctor.id, iso(utcnow())])
    v = _append_version(db, rec, kind="final", content=cur.content_json, author_id=doctor.id,
                        author_name=doctor.display_name, note="医师定稿", snapshot=snap, signature_hash=sig)
    rec.status, rec.final_version_id, rec.finalized_at, rec.doctor_id = RecordStatus.FINALIZED.value, v.id, utcnow(), doctor.id
    enc.status, enc.completed_at = EncounterStatus.COMPLETED.value, utcnow()
    enc.row_version += 1
    audit.record(db, actor, "record.finalize", entity_type="record", entity_id=rec.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"病历定稿 {rec.record_no} v{v.version_no}",
                 detail={"signature_hash": sig, "content_hash": v.content_hash, "version": v.version_no, "waived_blockers": waived})
    return v


def amend(db: Session, actor: Actor, rec: MedicalRecord, enc: Encounter, doctor, *, sections: dict[str, str],
          reason: str) -> RecordVersion:
    if rec.status != RecordStatus.FINALIZED.value:
        raise Conflict("病历尚未定稿，请直接保存草稿", code="record_not_final")
    if len(reason.strip()) < 5:
        raise Unprocessable("更正病历必须填写更正原因（至少 5 个字）", code="reason_required")
    unknown = [k for k in sections if k not in SECTION_KEYS]
    if unknown:
        raise Unprocessable(f"未知病历段落：{unknown}", code="bad_section")
    base = db.get(RecordVersion, rec.final_version_id)
    content = {"sections": {k: dict(v) for k, v in base.content_json["sections"].items()}}
    changed = []
    for k, t in sections.items():
        if content["sections"].get(k, {}).get("text", "") != t.rstrip():
            content["sections"][k] = _sec(t.rstrip(), "doctor", doctor.id)
            changed.append(k)
    if not changed:
        raise Unprocessable("没有任何修改", code="no_changes")
    v = _append_version(db, rec, kind="amendment", content=content, author_id=doctor.id, author_name=doctor.display_name,
                        note=f"更正（{'、'.join(SECTION_TITLES[k] for k in changed)}）：{reason.strip()}",
                        snapshot=base.snapshot_json,
                        signature_hash=hash_obj([hash_obj(content), doctor.id, iso(utcnow())]))
    rec.final_version_id = v.id
    audit.record(db, actor, "record.amend", entity_type="record", entity_id=rec.id, encounter_id=enc.id,
                 patient_id=enc.patient_id, summary=f"更正病历 {rec.record_no} → v{v.version_no}",
                 detail={"sections": changed, "reason": reason.strip(), "previous_version": base.version_no})
    return v


def effective_version(db: Session, rec: MedicalRecord) -> RecordVersion:
    return db.get(RecordVersion, rec.final_version_id or rec.working_version_id)


def verify_version(v: RecordVersion) -> bool:
    return v.content_hash == hash_obj(v.content_json)
