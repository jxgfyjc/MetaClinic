"""Medical records (list, open, edit, finalise, amend, history, PDF) and prescription PDFs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from server.api import serializers as S
from server.db.base import get_db
from server.db.models import Encounter, MedicalRecord, Patient, Prescription, RecordVersion, User
from server.deps import Ctx, actor_of, get_ctx, load_encounter_for, require
from server.domain import encounters as enc_svc
from server.domain import plans as plan_svc
from server.domain import records as rec_svc
from server.domain.ids import iso
from server.errors import Conflict, Forbidden, NotFound
from server.security import audit
from server.pdf import render

router = APIRouter(tags=["records"])


def _load_rec(db: Session, user: User, rid: str, *, write: bool = False) -> tuple[MedicalRecord, Encounter]:
    rec = db.get(MedicalRecord, rid)
    if not rec:
        raise NotFound("病历不存在", code="record_not_found")
    enc = load_encounter_for(db, user, rec.encounter_id, write=write)
    return rec, enc


@router.get("/records")
def list_records(status: str | None = None, q: str = "", patient_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db), user: User = Depends(require("record.read"))):
    stmt = select(MedicalRecord).join(Encounter, Encounter.id == MedicalRecord.encounter_id)
    if user.role == "DOCTOR":
        stmt = stmt.where(or_(Encounter.doctor_id == user.id, Encounter.doctor_id.is_(None)))
    if status:
        stmt = stmt.where(MedicalRecord.status == status)
    if patient_id:
        stmt = stmt.where(MedicalRecord.patient_id == patient_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.join(Patient, Patient.id == MedicalRecord.patient_id).where(or_(Patient.name.like(like), MedicalRecord.record_no.like(like), Patient.mrn.like(like)))
    rows = db.scalars(stmt.order_by(MedicalRecord.updated_at.desc()).limit(limit).offset(offset)).all()
    items = []
    for r in rows:
        v = rec_svc.effective_version(db, r)
        dx = (v.snapshot_json or {}).get("diagnoses") if v and v.kind != "draft" else None
        items.append({"id": r.id, "record_no": r.record_no, "status": r.status, "encounter_id": r.encounter_id, "patient": {"id": r.patient.id, "name": r.patient.name, "mrn": r.patient.mrn},
                      "updated_at": iso(r.updated_at), "finalized_at": iso(r.finalized_at), "version_no": v.version_no if v else None,
                      "chief_complaint": ((v.content_json or {}).get("sections", {}).get("chief_complaint", {}).get("text", "") if v else "")[:60],
                      "diagnoses": [d["name"] for d in dx] if dx else []})
    return {"items": items}


@router.post("/encounters/{eid}/record", status_code=200)
def open_record(eid: str, db: Session = Depends(get_db), user: User = Depends(require("record.read")), ctx: Ctx = Depends(get_ctx)):
    """Return the encounter's record, creating the auto-assembled draft the first time (idempotent)."""
    e = load_encounter_for(db, user, eid)
    rec = db.scalar(select(MedicalRecord).where(MedicalRecord.encounter_id == eid))
    if rec is None:
        if user.role not in ("DOCTOR",):
            raise NotFound("该就诊尚无病历", code="record_not_found")
        rec = rec_svc.get_or_create(db, actor_of(user, ctx), e)
        db.commit()
    v = rec_svc.effective_version(db, rec)
    return {"record": S.record_out(rec, v), "finalize_blockers": rec_svc.finalize_blockers(db, e) if rec.status != "finalized" else [],
            "staleness": enc_svc.staleness(db, e)}


@router.get("/records/{rid}")
def get_record(rid: str, version_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(require("record.read"))):
    rec, e = _load_rec(db, user, rid)
    v = db.get(RecordVersion, version_id) if version_id else rec_svc.effective_version(db, rec)
    if v is None or v.record_id != rec.id:
        raise NotFound("版本不存在")
    versions = db.scalars(select(RecordVersion).where(RecordVersion.record_id == rid).order_by(RecordVersion.version_no.desc())).all()
    return {"record": S.record_out(rec, v, versions=versions), "snapshot": v.snapshot_json if v.kind != "draft" else None,
            "integrity_ok": rec_svc.verify_version(v), "finalize_blockers": rec_svc.finalize_blockers(db, e) if rec.status != "finalized" else []}


class RecordSaveIn(BaseModel):
    sections: dict[str, str]
    base_version_no: int | None = None


@router.put("/records/{rid}/draft")
def save_draft(rid: str, body: RecordSaveIn, db: Session = Depends(get_db), user: User = Depends(require("record.write")), ctx: Ctx = Depends(get_ctx)):
    rec, e = _load_rec(db, user, rid, write=True)
    v, created = rec_svc.save_draft(db, actor_of(user, ctx), rec, e, body.sections, author_name=user.display_name, base_version_no=body.base_version_no)
    db.commit()
    return {"record": S.record_out(rec, v), "created_new_version": created}


@router.post("/records/{rid}/refresh")
def refresh_record(rid: str, db: Session = Depends(get_db), user: User = Depends(require("record.write")), ctx: Ctx = Depends(get_ctx)):
    """Re-pull auto sections from current facts/diagnoses/plan. Doctor-edited sections are NEVER overwritten — the
    fresh text comes back as suggestions the doctor can accept (replace/append) one by one."""
    rec, e = _load_rec(db, user, rid, write=True)
    res = rec_svc.refresh_auto_sections(db, actor_of(user, ctx), rec, e)
    db.commit()
    return {"record": S.record_out(rec, res["version"]), "updated": res["updated"], "suggestions": res["suggestions"]}


class SuggestIn(BaseModel):
    mode: str = Field(pattern="^(replace|append)$")


@router.post("/records/{rid}/sections/{key}/accept-suggestion")
def accept_suggestion(rid: str, key: str, body: SuggestIn, db: Session = Depends(get_db), user: User = Depends(require("record.write")), ctx: Ctx = Depends(get_ctx)):
    rec, e = _load_rec(db, user, rid, write=True)
    v = rec_svc.accept_suggestion(db, actor_of(user, ctx), rec, e, key, mode=body.mode, author_name=user.display_name)
    db.commit()
    return {"record": S.record_out(rec, v)}


class FinalizeIn(BaseModel):
    ignore: list[str] = []


@router.post("/records/{rid}/finalize")
def finalize(rid: str, body: FinalizeIn, db: Session = Depends(get_db), user: User = Depends(require("record.finalize")), ctx: Ctx = Depends(get_ctx)):
    rec, e = _load_rec(db, user, rid, write=True)
    if e.doctor_id and e.doctor_id != user.id:
        raise Forbidden("只能定稿本人接诊的病历", code="not_your_encounter")
    v = rec_svc.finalize(db, actor_of(user, ctx), rec, e, user, ignore=body.ignore)
    db.commit()
    return {"record": S.record_out(rec, v)}


class AmendIn(BaseModel):
    sections: dict[str, str]
    reason: str = Field(min_length=5, max_length=300)


@router.post("/records/{rid}/amend")
def amend(rid: str, body: AmendIn, db: Session = Depends(get_db), user: User = Depends(require("record.amend")), ctx: Ctx = Depends(get_ctx)):
    rec, e = _load_rec(db, user, rid, write=True)
    v = rec_svc.amend(db, actor_of(user, ctx), rec, e, user, sections=body.sections, reason=body.reason)
    db.commit()
    return {"record": S.record_out(rec, v)}


@router.get("/records/{rid}/pdf")
def record_pdf(rid: str, version_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(require("record.print")), ctx: Ctx = Depends(get_ctx)):
    rec, e = _load_rec(db, user, rid)
    v = db.get(RecordVersion, version_id) if version_id else rec_svc.effective_version(db, rec)
    if v is None or v.record_id != rec.id:
        raise NotFound("版本不存在")
    snap = v.snapshot_json if v.kind != "draft" else _live_snapshot(db, e)
    doc = e.doctor
    pdf = render.build_record_pdf(rec.record_no, v.content_json, snap, status=rec.status, kind=v.kind, finalized_at=iso(v.created_at) if v.kind != "draft" else "",
                                  doctor_line=f"{v.author_name}" if v.kind != "draft" else (f"{doc.display_name}（草稿作者）" if doc else ""),
                                  version_no=v.version_no, signature_hash=v.signature_hash, content_hash=v.content_hash)
    rec.print_count += 1
    audit.record(db, actor_of(user, ctx), "record.pdf", entity_type="record", entity_id=rec.id, encounter_id=e.id, patient_id=e.patient_id, summary=f"导出病历 PDF v{v.version_no}")
    db.commit()
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{rec.record_no}_v{v.version_no}.pdf"'})


def _live_snapshot(db: Session, e: Encounter) -> dict:
    return rec_svc._freeze_snapshot(db, e)


@router.get("/prescriptions/{rid}/pdf")
def rx_pdf(rid: str, db: Session = Depends(get_db), user: User = Depends(require("rx.read")), ctx: Ctx = Depends(get_ctx)):
    from server.api.treatment import _authorise_rx_read
    r = db.get(Prescription, rid)
    if not r:
        raise NotFound("处方不存在", code="rx_not_found")
    _authorise_rx_read(db, user, r)
    e = db.get(Encounter, r.encounter_id)
    out = S.rx_out(r)
    patient = {"name": r.patient.name, "mrn": r.patient.mrn, "gender": r.patient.gender, "age": S.calc_age(r.patient),
               "allergies": enc_svc.active_allergy_names(r.patient)}
    doctor = {"name": r.doctor.display_name, "title": r.doctor.title, "license_no": r.doctor.license_no}
    pdf = render.build_rx_pdf(out, patient, doctor, e.encounter_no, [d["name"] for d in (r.dx_links_json or [])])
    r.print_count += 1
    audit.record(db, actor_of(user, ctx), "rx.pdf", entity_type="prescription", entity_id=r.id, encounter_id=r.encounter_id, patient_id=r.patient_id, summary=f"导出处方 PDF {r.rx_no}")
    db.commit()
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{r.rx_no}.pdf"'})
