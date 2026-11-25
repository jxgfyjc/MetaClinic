"""Repository layer for the golden-path entities (demo.md 第四节).

Only the entities on the end-to-end demo path get persistence here — not all
22 ORM tables — per demo.md #4 "无需本轮接入全部 22 张 ORM 表". Each function
opens its own short session via get_sessionmaker(); no long-lived transaction
across a multi-minute AI run.

instruct #66: server-controlled status transitions for prescription drafts.
demo.md #4: persist so records survive an API restart; idempotent init.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .db import (
    AIRun, AIOutput, AIFeedback, AuditLog, Encounter, FollowupPlan,
    FollowupResponse, FollowupRiskEvent, LabReport, LabResult, MedicalRecord,
    Patient, PrescriptionDraft, PrescriptionReview, User, get_sessionmaker,
    Allergy, Medication, Symptom, TongueImage, TongueObservation, AIEvidence,
    EPrescription, DiagnosisRecord, TherapyItemReview,
)


def _uid() -> str:
    return uuid.uuid4().hex[:24]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserRepo:
    @staticmethod
    async def upsert(user_id: str, *, name: str, role: str = "DOCTOR",
                     email: str = "", is_demo: bool = True,
                     password_hash: str = "") -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            u = await s.get(User, user_id)
            if u is None:
                u = User(id=user_id, email=email or f"{user_id}@demo.local",
                         name=name, role=role, is_active=True,
                         password_hash=password_hash)
                s.add(u)
            else:
                # update password_hash if provided non-empty
                if password_hash:
                    u.password_hash = password_hash
                if name:
                    u.name = name
                if role:
                    u.role = role
            await s.commit()
            return {"id": u.id, "name": u.name, "role": u.role}

    @staticmethod
    async def get(user_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            u = await s.get(User, user_id)
            if u is None:
                return None
            return {"id": u.id, "name": u.name, "role": u.role}


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
class PatientRepo:
    @staticmethod
    async def upsert(patient_id: str, *, name: str, gender: str = "",
                     age: int = 0, phone: str = "", is_demo: bool = True,
                     extra: dict | None = None) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(Patient, patient_id)
            if p is None:
                p = Patient(id=patient_id, name=name, gender=gender, age=age,
                            phone=phone, is_demo=is_demo,
                            extra_json=extra or {})
                s.add(p)
            else:
                # V1.0: persist extra into extra_json (merge if already exists)
                if extra:
                    merged = dict(p.extra_json or {})
                    merged.update(extra)
                    p.extra_json = merged
                    if name: p.name = name
                    if gender: p.gender = gender
                    if age: p.age = age
                    if phone: p.phone = phone
            await s.commit()
            return PatientRepo._to_dict(p)

    @staticmethod
    def _to_dict(p: Patient) -> dict:
        d = {"id": p.id, "name": p.name, "gender": p.gender,
             "age": p.age, "phone": p.phone, "is_demo": p.is_demo,
             "created_at": str(p.created_at) if p.created_at else ""}
        # V1.0: merge extra_json into top-level for backward compat
        extra = p.extra_json if isinstance(p.extra_json, dict) else {}
        d.update(extra)
        return d

    @staticmethod
    async def get(patient_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(Patient, patient_id)
            if p is None:
                return None
            return PatientRepo._to_dict(p)

    @staticmethod
    async def list_all() -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(Patient).order_by(Patient.id))
            return [PatientRepo._to_dict(p) for p in res.scalars()]

    @staticmethod
    async def search_by_phone(phone: str) -> dict | None:
        """V1.0: find patient by phone (for intake patient lookup)."""
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(Patient).where(Patient.phone == phone).limit(1))
            p = res.scalars().first()
            return PatientRepo._to_dict(p) if p else None

    @staticmethod
    async def delete(patient_id: str) -> bool:
        """updatev2_3 §2: delete a patient and ALL encounters + child records.
        Reuses EncounterRepo.delete for each encounter, then removes
        patient-level rows (allergies, medications, symptoms)."""
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(Patient, patient_id)
            if p is None:
                return False
            # Find all encounters for this patient and delete them (cascade)
            encs = await s.execute(select(Encounter).where(
                Encounter.patient_id == patient_id))
            enc_ids = [e.id for e in encs.scalars()]
        # Delete encounters outside this session (EncounterRepo opens its own)
        for eid in enc_ids:
            await EncounterRepo.delete(eid)
        # Now delete patient-level rows
        async with sm() as s:
            p = await s.get(Patient, patient_id)
            if p is None:
                return True
            for model in [Allergy, Medication, Symptom, LabResult,
                          LabReport, TongueImage, EPrescription,
                          PrescriptionDraft, MedicalRecord, FollowupPlan,
                          FollowupResponse, FollowupRiskEvent, AIRun,
                          DiagnosisRecord, TherapyItemReview]:
                try:
                    if hasattr(model, "patient_id"):
                        res = await s.execute(select(model).where(
                            model.patient_id == patient_id))
                        for obj in res.scalars():
                            await s.delete(obj)
                except Exception:
                    pass
            await s.delete(p)
            await s.commit()
            return True

    @staticmethod
    async def delete_many(patient_ids: list[str]) -> int:
        """Batch delete patients + their encounters + child records."""
        count = 0
        for pid in patient_ids:
            if await PatientRepo.delete(pid):
                count += 1
        return count


# ---------------------------------------------------------------------------
# Encounters
# ---------------------------------------------------------------------------
class EncounterRepo:
    @staticmethod
    async def upsert(encounter_id: str, *, patient_id: str, doctor_id: str = "",
                     chief_complaint: str = "", encounter_no: str = "",
                     status: str = "IN_PROGRESS", is_demo: bool = True,
                     extra: dict | None = None) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            e = await s.get(Encounter, encounter_id)
            if e is None:
                e = Encounter(id=encounter_id, patient_id=patient_id,
                              doctor_id=doctor_id, encounter_no=encounter_no,
                              chief_complaint=chief_complaint, status=status,
                              clinical_data=extra or {})
                s.add(e)
            else:
                # V1.0: persist extra into clinical_data (merge if exists)
                if extra:
                    merged = dict(e.clinical_data or {})
                    merged.update(extra)
                    e.clinical_data = merged
                if chief_complaint: e.chief_complaint = chief_complaint
                if status: e.status = status
                if doctor_id: e.doctor_id = doctor_id
            await s.commit()
            return EncounterRepo._to_dict(e)

    @staticmethod
    def _to_dict(e: Encounter) -> dict:
        d = {"id": e.id, "patient_id": e.patient_id, "doctor_id": e.doctor_id,
             "encounter_no": e.encounter_no, "chief_complaint": e.chief_complaint,
             "status": e.status, "created_at": str(e.created_at) if e.created_at else ""}
        # V1.0: merge clinical_data into top-level for backward compat
        cd = e.clinical_data if isinstance(e.clinical_data, dict) else {}
        d.update(cd)
        return d

    @staticmethod
    async def get(encounter_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            e = await s.get(Encounter, encounter_id)
            if e is None:
                return None
            return EncounterRepo._to_dict(e)

    @staticmethod
    async def list_all() -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(Encounter).order_by(Encounter.id))
            return [EncounterRepo._to_dict(e) for e in res.scalars()]

    @staticmethod
    async def update_clinical_data(encounter_id: str, data: dict) -> dict | None:
        """V1.0: merge new clinical data into existing encounter."""
        sm = get_sessionmaker()
        async with sm() as s:
            e = await s.get(Encounter, encounter_id)
            if e is None:
                return None
            merged = dict(e.clinical_data or {})
            merged.update(data)
            e.clinical_data = merged
            if data.get("chief_complaint"):
                e.chief_complaint = data["chief_complaint"]
            if data.get("status"):
                e.status = data["status"]
            if data.get("doctor_id"):
                e.doctor_id = data["doctor_id"]
            await s.commit()
            return EncounterRepo._to_dict(e)

    @staticmethod
    async def delete(encounter_id: str) -> bool:
        """updatev2_3 §2: delete an encounter and ALL its child records.
        SQLite has no FK CASCADE, so we manually delete dependents in
        dependency order to avoid orphaned rows. Returns True if deleted."""
        sm = get_sessionmaker()
        async with sm() as s:
            e = await s.get(Encounter, encounter_id)
            if e is None:
                return False
            # Collect child rows by encounter_id and delete them
            # Order: leaf tables first, then parent encounter last.
            child_models = [
                TherapyItemReview, EPrescription, PrescriptionDraft,
                PrescriptionReview, DiagnosisRecord, FollowupPlan,
                FollowupResponse, FollowupRiskEvent, MedicalRecord,
                LabResult, LabReport, TongueObservation, TongueImage,
                AIOutput, AIRun, AIEvidence,
            ]
            for model in child_models:
                try:
                    if hasattr(model, "encounter_id"):
                        res = await s.execute(select(model).where(
                            model.encounter_id == encounter_id))
                        for obj in res.scalars():
                            await s.delete(obj)
                    elif model is TongueObservation:
                        # TongueObservation links via tongue_image_id, not encounter_id
                        imgs = await s.execute(select(TongueImage).where(
                            TongueImage.encounter_id == encounter_id))
                        for img in imgs.scalars():
                            obs = await s.execute(select(TongueObservation).where(
                                TongueObservation.tongue_image_id == img.id))
                            for o in obs.scalars():
                                await s.delete(o)
                    elif model is AIEvidence:
                        # AIEvidence links via ai_output_id
                        outs = await s.execute(select(AIOutput).where(
                            AIOutput.ai_run_id.in_(
                                select(AIRun.id).where(
                                    AIRun.encounter_id == encounter_id))))
                        for out in outs.scalars():
                            ev = await s.execute(select(AIEvidence).where(
                                AIEvidence.ai_output_id == out.id))
                            for e2 in ev.scalars():
                                await s.delete(e2)
                except Exception:
                    pass  # table may not exist or column missing — skip
            # FollowupResponse/RiskEvent link via followup_plan_id, not encounter_id
            fups = await s.execute(select(FollowupPlan).where(
                FollowupPlan.encounter_id == encounter_id))
            for fup in fups.scalars():
                for m in [FollowupResponse, FollowupRiskEvent]:
                    try:
                        if hasattr(m, "followup_plan_id"):
                            res = await s.execute(select(m).where(
                                m.followup_plan_id == fup.id))
                            for obj in res.scalars():
                                await s.delete(obj)
                    except Exception:
                        pass
                await s.delete(fup)
            # AIOutput links via ai_run_id
            runs = await s.execute(select(AIRun).where(
                AIRun.encounter_id == encounter_id))
            for run in runs.scalars():
                outs = await s.execute(select(AIOutput).where(
                    AIOutput.ai_run_id == run.id))
                for out in outs.scalars():
                    fbs = await s.execute(select(AIFeedback).where(
                        AIFeedback.ai_output_id == out.id))
                    for fb in fbs.scalars():
                        await s.delete(fb)
                    await s.delete(out)
                await s.delete(run)
            await s.delete(e)
            await s.commit()
            return True

    @staticmethod
    async def delete_many(encounter_ids: list[str]) -> int:
        """Batch delete encounters + their child records."""
        count = 0
        for eid in encounter_ids:
            if await EncounterRepo.delete(eid):
                count += 1
        return count


# ---------------------------------------------------------------------------
# Medical records (病历编辑器) — idempotent, won't overwrite doctor-edited
# ---------------------------------------------------------------------------
class MedicalRecordRepo:
    @staticmethod
    async def upsert_for_encounter(*, encounter_id: str, patient_id: str,
                                   content: dict, raw_text: str = "",
                                   doctor_id: str = "") -> dict:
        """Idempotent upsert keyed on encounter_id.

        If a record exists for this encounter, the doctor's edit is preserved
        only when the incoming content differs — we append a new version row
        rather than clobber, so edits survive re-runs of init_demo (demo.md #4).
        For the MVP, the latest row by created_at is the current record.
        """
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(MedicalRecord).filter_by(encounter_id=encounter_id)
                .order_by(MedicalRecord.created_at.desc()))
            existing = res.scalars().first()
            content_json = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else content
            # if existing has identical content, no-op
            if existing is not None:
                prev = json.dumps(existing.content_json, ensure_ascii=False) \
                    if isinstance(existing.content_json, dict) else str(existing.content_json)
                if prev == content_json:
                    return {"id": existing.id, "encounter_id": encounter_id,
                            "updated": False}
            rec = MedicalRecord(id=_uid(), encounter_id=encounter_id,
                                patient_id=patient_id,
                                content_json=content if isinstance(content, dict)
                                else json.loads(content_json),
                                raw_text=raw_text,
                                doctor_id=doctor_id)
            s.add(rec)
            await s.commit()
            return {"id": rec.id, "encounter_id": encounter_id, "updated": True}

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(MedicalRecord).filter_by(encounter_id=encounter_id)
                .order_by(MedicalRecord.created_at.desc()))
            rec = res.scalars().first()
            if rec is None:
                return None
            return {"id": rec.id, "encounter_id": encounter_id,
                    "patient_id": rec.patient_id,
                    "content": rec.content_json, "raw_text": rec.raw_text,
                    "status": getattr(rec, "status", None) or "draft",
                    "finalized_at": str(rec.finalized_at) if getattr(rec, "finalized_at", None) else "",
                    "doctor_id": getattr(rec, "doctor_id", "") or "",
                    "signature_id": getattr(rec, "signature_id", "") or "",
                    "followup_plan_id": getattr(rec, "followup_plan_id", "") or "",
                    "updated_at": str(rec.updated_at) if getattr(rec, "updated_at", None) else "",
                    "created_at": str(rec.created_at)}

    # Backwards-compat alias — app.py get_encounter still calls get_by_encounter.
    get_by_encounter = staticmethod(list_for_encounter)

    @staticmethod
    async def delete(medical_record_id: str) -> bool:
        """Hard-delete a medical record. Leaf table — no child FKs, so a
        straight delete is safe (caller may have already detached it from
        encounter/followup references)."""
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(MedicalRecord).filter_by(id=medical_record_id))
            rec = res.scalars().first()
            if rec is None:
                return False
            await s.delete(rec)
            await s.commit()
            return True

    @staticmethod
    async def delete_many(medical_record_ids: list[str]) -> int:
        n = 0
        for mid in medical_record_ids:
            if await MedicalRecordRepo.delete(mid):
                n += 1
        return n


# ---------------------------------------------------------------------------
# Lab results
# ---------------------------------------------------------------------------
class LabResultRepo:
    @staticmethod
    async def upsert_for_encounter(*, encounter_id: str, patient_id: str,
                                   labs: list[dict]) -> list[str]:
        sm = get_sessionmaker()
        ids: list[str] = []
        async with sm() as s:
            # one LabReport per encounter (idempotent: reuse if exists)
            res = await s.execute(
                select(LabReport).filter_by(encounter_id=encounter_id))
            rep = res.scalars().first()
            if rep is None:
                rep = LabReport(id=_uid(), encounter_id=encounter_id,
                                patient_id=patient_id)
                s.add(rep)
                await s.flush()
            # clear old results for this report (idempotent re-seed)
            await s.execute(delete(LabResult).where(
                LabResult.lab_report_id == rep.id))
            for lab in labs:
                lid = _uid()
                lr = LabResult(id=lid, lab_report_id=rep.id,
                               patient_id=patient_id, encounter_id=encounter_id,
                               test_name=lab.get("item", lab.get("test_name", "")),
                               canonical_name=lab.get("canonical_name", ""),
                               value=str(lab.get("value", "")),
                               unit=lab.get("unit", ""),
                               reference_low=lab.get("ref", "").split("-")[0] if "-" in str(lab.get("ref", "")) else "",
                               reference_high=lab.get("ref", ""),
                               abnormal_flag=lab.get("flag", ""),
                               test_time=lab.get("test_time", ""))
                s.add(lr)
                ids.append(lid)
            await s.commit()
        return ids

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(LabResult).filter_by(
                encounter_id=encounter_id))
            return [{"item": lr.test_name, "value": lr.value, "unit": lr.unit,
                     "ref": lr.reference_high, "flag": lr.abnormal_flag}
                    for lr in res.scalars()]

    @staticmethod
    async def list_for_patient(patient_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(LabResult).filter_by(
                patient_id=patient_id).order_by(LabResult.test_time))
            return [{"id": lr.id, "item": lr.test_name, "value": lr.value,
                     "unit": lr.unit, "ref": lr.reference_high,
                     "flag": lr.abnormal_flag, "encounter_id": lr.encounter_id}
                    for lr in res.scalars()]


# ---------------------------------------------------------------------------
# AI runs + outputs (orchestrator progress persistence)
# ---------------------------------------------------------------------------
class AIRunRepo:
    @staticmethod
    async def create(*, run_id: str, patient_id: str, encounter_id: str,
                     model_name: str, input_hash: str,
                     prompt_version: str = "", knowledge_version: str = "",
                     actor: str = "system") -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            r = AIRun(id=run_id, patient_id=patient_id, encounter_id=encounter_id,
                      model_name=model_name, input_hash=input_hash,
                      prompt_version=prompt_version, knowledge_version=knowledge_version,
                      status="PENDING")
            s.add(r)
            await s.commit()
        from . import audit
        await audit.audit_log(actor=actor, action="ai.run.create",
                               target=encounter_id, meta={"run_id": run_id})
        return {"run_id": run_id, "status": "PENDING"}

    @staticmethod
    async def set_status(run_id: str, status: str, *,
                         latency_ms: int = 0, output_hash: str = "") -> None:
        sm = get_sessionmaker()
        async with sm() as s:
            r = await s.get(AIRun, run_id)
            if r is not None:
                r.status = status
                if latency_ms:
                    r.latency_ms = latency_ms
                if output_hash:
                    r.output_hash = output_hash
                await s.commit()

    @staticmethod
    async def append_output(*, run_id: str, output_type: str,
                            structured_json: dict, display_text: str = "") -> str:
        """Insert an AIOutput row for one module. Returns the output id."""
        sm = get_sessionmaker()
        oid = _uid()
        async with sm() as s:
            o = AIOutput(id=oid, ai_run_id=run_id, output_type=output_type,
                         structured_json=structured_json, display_text=display_text)
            s.add(o)
            await s.commit()
        return oid

    @staticmethod
    async def get(run_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            r = await s.get(AIRun, run_id)
            if r is None:
                return None
            res = await s.execute(select(AIOutput).filter_by(
                ai_run_id=run_id))
            outputs = [{"id": o.id, "output_type": o.output_type,
                        "structured_json": o.structured_json,
                        "display_text": o.display_text}
                       for o in res.scalars()]
            return {"run_id": r.id, "status": r.status,
                    "latency_ms": r.latency_ms, "input_hash": r.input_hash,
                    "encounter_id": r.encounter_id, "patient_id": r.patient_id,
                    "module_timings": r.module_timings_json if hasattr(r, "module_timings_json") and r.module_timings_json else {},
                    "outputs": outputs}

    @staticmethod
    async def update_module_timings(run_id: str, timings: dict) -> None:
        """V1.2 §7: persist per-module timing data."""
        sm = get_sessionmaker()
        async with sm() as s:
            r = await s.get(AIRun, run_id)
            if r is not None:
                try:
                    r.module_timings_json = timings
                    await s.commit()
                except Exception:
                    pass  # column may not exist on older DBs

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(AIRun).filter_by(
                encounter_id=encounter_id).order_by(AIRun.created_at.desc()))
            return [{"run_id": r.id, "status": r.status,
                     "latency_ms": r.latency_ms, "created_at": str(r.created_at)}
                    for r in res.scalars()]


# ---------------------------------------------------------------------------
# AI feedback (Accept / Modify / Reject)
# ---------------------------------------------------------------------------
class FeedbackRepo:
    @staticmethod
    async def create(*, ai_output_id: str, doctor_id: str, action: str,
                     original_content: str = "", edited_content: str = "",
                     reason: str = "") -> dict:
        sm = get_sessionmaker()
        fid = _uid()
        async with sm() as s:
            fb = AIFeedback(id=fid, ai_output_id=ai_output_id,
                            doctor_id=doctor_id, action=action,
                            original_content=original_content,
                            edited_content=edited_content, reason=reason)
            s.add(fb)
            await s.commit()
        return {"id": fid, "ai_output_id": ai_output_id, "action": action}

    @staticmethod
    async def list_for_output(ai_output_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(AIFeedback).filter_by(
                ai_output_id=ai_output_id).order_by(AIFeedback.created_at.desc()))
            return [{"id": f.id, "action": f.action, "doctor_id": f.doctor_id,
                     "original_content": f.original_content,
                     "edited_content": f.edited_content, "reason": f.reason,
                     "created_at": str(f.created_at)} for f in res.scalars()]


# ---------------------------------------------------------------------------
# Prescription drafts (server-controlled status, demo.md #7)
# ---------------------------------------------------------------------------
class PrescriptionDraftRepo:
    # demo.md 第七节 doctor review flow:
    # AI_GENERATED -> DOCTOR_EDITING <-> DOCTOR_CONFIRMED -> SIGNED -> SUBMITTED_TO_HIS
    _ALLOWED = {
        "AI_GENERATED": {"DOCTOR_EDITING", "DOCTOR_CONFIRMED", "SIGNED"},
        "DOCTOR_EDITING": {"DOCTOR_CONFIRMED", "AI_GENERATED", "SIGNED"},
        "DOCTOR_CONFIRMED": {"SIGNED", "DOCTOR_EDITING"},
        "SIGNED": {"SUBMITTED_TO_HIS"},
        "SUBMITTED_TO_HIS": set(),
    }

    @staticmethod
    async def create(*, prescription_id: str, encounter_id: str,
                     patient_id: str, doctor_id: str, ai_run_id: str = "",
                     structured_json: dict,
                     target_diagnosis_id: str = "") -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            existing = await s.get(PrescriptionDraft, prescription_id)
            if existing is not None:
                # idempotent: return existing if same encounter
                return {"id": existing.id, "status": existing.status,
                        "encounter_id": existing.encounter_id}
            d = PrescriptionDraft(id=prescription_id, encounter_id=encounter_id,
                                   patient_id=patient_id, doctor_id=doctor_id,
                                   ai_run_id=ai_run_id,
                                   structured_json=structured_json,
                                   status="AI_GENERATED")
            # V1.2 §2: link to specific diagnosis if provided
            if target_diagnosis_id and hasattr(d, "target_diagnosis_id"):
                d.target_diagnosis_id = target_diagnosis_id
            s.add(d)
            await s.commit()
        return {"id": prescription_id, "status": "AI_GENERATED"}

    @staticmethod
    async def get(prescription_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            d = await s.get(PrescriptionDraft, prescription_id)
            if d is None:
                return None
            return {"id": d.id, "encounter_id": d.encounter_id,
                    "patient_id": d.patient_id, "doctor_id": d.doctor_id,
                    "ai_run_id": d.ai_run_id, "structured_json": d.structured_json,
                    "status": d.status, "created_at": str(d.created_at),
                    "target_diagnosis_id": getattr(d, "target_diagnosis_id", "") or "",
                    "is_stale": bool(getattr(d, "is_stale", False)),
                    "diagnosis_snapshot_hash": getattr(d, "diagnosis_snapshot_hash", "") or ""}

    @staticmethod
    async def update_structured(prescription_id: str, structured_json: dict,
                                *, new_status: str = "DOCTOR_EDITING",
                                doctor_id: str = "",
                                is_stale: bool | None = None,
                                diagnosis_snapshot_hash: str | None = None) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            d = await s.get(PrescriptionDraft, prescription_id)
            if d is None:
                return {"error": "not found"}
            # server-controlled transition check (demo.md #7, instruct #66).
            # Same-status re-save (edit within current state) is allowed.
            allowed = PrescriptionDraftRepo._ALLOWED.get(d.status, set())
            if new_status not in allowed and new_status != d.status:
                return {"error": f"transition {d.status} -> {new_status} not allowed"}
            # Only overwrite structured_json if a non-empty dict is provided.
            # confirm/sign/submit pass {} which should NOT wipe existing data.
            if structured_json:
                d.structured_json = structured_json
            if new_status != d.status:
                d.status = new_status
            if doctor_id:
                d.doctor_id = doctor_id
            # updatev2_3 §1c: stale flag + diagnosis snapshot hash
            if is_stale is not None and hasattr(d, "is_stale"):
                d.is_stale = is_stale
            if diagnosis_snapshot_hash is not None and hasattr(d, "diagnosis_snapshot_hash"):
                d.diagnosis_snapshot_hash = diagnosis_snapshot_hash
            await s.commit()
            # review trail
            rev = PrescriptionReview(id=_uid(), prescription_id=prescription_id,
                                     doctor_id=doctor_id or d.doctor_id,
                                     action=new_status)
            s2 = sm()
            async with s2 as s2s:
                s2s.add(rev)
                await s2s.commit()
            return {"id": d.id, "status": d.status}

    @staticmethod
    async def sign(prescription_id: str, *, doctor_id: str) -> dict:
        return await PrescriptionDraftRepo.update_structured(
            prescription_id, {}, new_status="SIGNED", doctor_id=doctor_id)

    @staticmethod
    async def submit_to_his(prescription_id: str, *, doctor_id: str,
                            signature_id: str) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            d = await s.get(PrescriptionDraft, prescription_id)
            if d is None:
                return {"error": "not found"}
            # updatev2_3 §5: idempotent submit — already submitted is success,
            # not an error, so repeated submits persist/re-fill e-prescriptions
            # instead of 409-ing.
            if d.status == "SUBMITTED_TO_HIS":
                return {"id": prescription_id, "status": "SUBMITTED_TO_HIS",
                        "doctor_id": doctor_id, "signature_id": signature_id,
                        "idempotent": True}
            if d.status != "SIGNED":
                return {"error": f"must be SIGNED to submit (now {d.status})"}
            d.status = "SUBMITTED_TO_HIS"
            await s.commit()
        return {"id": prescription_id, "status": "SUBMITTED_TO_HIS",
                "doctor_id": doctor_id, "signature_id": signature_id}

    @staticmethod
    async def set_final_prescription(encounter_id: str,
                                       prescription_id: str) -> None:
        """updatev2_3 §2: link encounter to its final (signed/submitted)
        e-prescription via final_prescription_id."""
        sm = get_sessionmaker()
        async with sm() as s:
            e = await s.get(Encounter, encounter_id)
            if e is not None and hasattr(e, "final_prescription_id"):
                e.final_prescription_id = prescription_id
                await s.commit()

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(PrescriptionDraft).filter_by(
                encounter_id=encounter_id).order_by(
                PrescriptionDraft.created_at.desc()))
            return [{"id": d.id, "status": d.status,
                      "structured_json": d.structured_json,
                      "target_diagnosis_id": getattr(d, "target_diagnosis_id", "") or "",
                      "is_stale": bool(getattr(d, "is_stale", False)),
                      "diagnosis_snapshot_hash": getattr(d, "diagnosis_snapshot_hash", "") or "",
                      "created_at": str(d.created_at)}
                    for d in res.scalars()]

    @staticmethod
    async def mark_stale_for_encounter(encounter_id: str,
                                        except_id: str = "") -> int:
        """updatev2_3 §1c: mark all non-stale, non-terminal encounter drafts
        as stale when diagnoses change. Returns count marked. Skips the draft
        being freshly generated (except_id) and already-final (SUBMITTED)
        drafts so submitted records stay intact."""
        sm = get_sessionmaker()
        count = 0
        async with sm() as s:
            res = await s.execute(select(PrescriptionDraft).where(
                PrescriptionDraft.encounter_id == encounter_id))
            for d in res.scalars():
                if d.id == except_id:
                    continue
                if d.status in ("SUBMITTED_TO_HIS", "SIGNED"):
                    continue  # don't invalidate signed/submitted records
                if not getattr(d, "is_stale", False):
                    d.is_stale = True
                    count += 1
            await s.commit()
        return count

    @staticmethod
    async def get_unified_draft(encounter_id: str) -> dict | None:
        """updatev2_3 §1: get the latest encounter-scoped (target_diagnosis_id='')
        draft that is NOT stale and not in a terminal state."""
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(PrescriptionDraft).where(
                PrescriptionDraft.encounter_id == encounter_id
            ).order_by(PrescriptionDraft.created_at.desc()))
            for d in res.scalars():
                tdi = getattr(d, "target_diagnosis_id", "") or ""
                is_stale = bool(getattr(d, "is_stale", False))
                if tdi == "" and not is_stale and d.status != "SUBMITTED_TO_HIS":
                    return {"id": d.id, "status": d.status,
                            "structured_json": d.structured_json,
                            "target_diagnosis_id": tdi,
                            "is_stale": is_stale,
                            "diagnosis_snapshot_hash": getattr(d, "diagnosis_snapshot_hash", "") or "",
                            "created_at": str(d.created_at)}
            return None


# ---------------------------------------------------------------------------
# Followup (demo.md #8)
# ---------------------------------------------------------------------------
class FollowupPlanRepo:
    @staticmethod
    async def create(*, plan_id: str, patient_id: str, encounter_id: str,
                     template: str = "custom", days: int = 14,
                     questions: list | None = None,
                     doctor_id: str = "") -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            existing = await s.get(FollowupPlan, plan_id)
            if existing is not None:
                return {"id": existing.id, "status": existing.status}
            p = FollowupPlan(id=plan_id, patient_id=patient_id,
                             encounter_id=encounter_id, template=template,
                             days=days,
                             questions_json=questions or [],
                             status="ACTIVE")
            s.add(p)
            await s.commit()
        return {"id": plan_id, "status": "ACTIVE", "days": days}

    @staticmethod
    async def list_all() -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(FollowupPlan).order_by(
                FollowupPlan.created_at.desc()))
            return [{"id": p.id, "patient_id": p.patient_id,
                      "encounter_id": p.encounter_id, "days": p.days,
                      "template": p.template, "status": p.status,
                      "questions": p.questions_json,
                      "created_at": str(p.created_at)}
                    for p in res.scalars()]

    @staticmethod
    async def get(plan_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(FollowupPlan, plan_id)
            if p is None:
                return None
            return {"id": p.id, "patient_id": p.patient_id,
                    "encounter_id": p.encounter_id, "days": p.days,
                    "template": p.template, "status": p.status,
                    "questions": p.questions_json}

    @staticmethod
    async def delete(plan_id: str) -> bool:
        """Delete a followup plan and its child responses (+ their risk events).
        SQLite has no FK CASCADE, so we clean children manually in dependency
        order: risk_events → responses → plan."""
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(FollowupPlan, plan_id)
            if p is None:
                return False
            # collect response ids for this plan
            res = await s.execute(select(FollowupResponse).filter_by(
                followup_plan_id=plan_id))
            resp_ids = [r.id for r in res.scalars()]
            if resp_ids:
                await s.execute(delete(FollowupRiskEvent).where(
                    FollowupRiskEvent.followup_response_id.in_(resp_ids)))
                await s.execute(delete(FollowupResponse).where(
                    FollowupResponse.id.in_(resp_ids)))
            await s.delete(p)
            await s.commit()
            return True

    @staticmethod
    async def delete_many(plan_ids: list[str]) -> int:
        n = 0
        for fid in plan_ids:
            if await FollowupPlanRepo.delete(fid):
                n += 1
        return n


class FollowupResponseRepo:
    @staticmethod
    async def create(*, plan_id: str, patient_id: str, answers: dict,
                     ai_summary: dict | None = None,
                     risk_level: str = "normal") -> dict:
        sm = get_sessionmaker()
        rid = _uid()
        async with sm() as s:
            r = FollowupResponse(id=rid, followup_plan_id=plan_id,
                                 patient_id=patient_id, answers_json=answers,
                                 ai_summary_json=ai_summary or {},
                                 risk_level=risk_level)
            s.add(r)
            await s.commit()
        return {"id": rid, "risk_level": risk_level}

    @staticmethod
    async def list_for_plan(plan_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(FollowupResponse).filter_by(
                followup_plan_id=plan_id).order_by(FollowupResponse.created_at.desc()))
            return [{"id": r.id, "risk_level": r.risk_level,
                      "answers": r.answers_json, "ai_summary": r.ai_summary_json,
                      "created_at": str(r.created_at)}
                    for r in res.scalars()]


class FollowupRiskEventRepo:
    @staticmethod
    async def create(*, response_id: str, patient_id: str,
                     rule_triggered: str, risk_level: str,
                     escalated: bool = False) -> str:
        sm = get_sessionmaker()
        eid = _uid()
        async with sm() as s:
            ev = FollowupRiskEvent(id=eid, followup_response_id=response_id,
                                   patient_id=patient_id, rule_triggered=rule_triggered,
                                   risk_level=risk_level, escalated=escalated)
            s.add(ev)
            await s.commit()
        return eid

    @staticmethod
    async def list_alerts() -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(FollowupRiskEvent).where(
                FollowupRiskEvent.risk_level.in_(["high_risk", "attention"])
                ).order_by(FollowupRiskEvent.created_at.desc()))
            return [{"id": e.id, "patient_id": e.patient_id,
                      "response_id": e.followup_response_id,
                      "rule": e.rule_triggered, "risk_level": e.risk_level,
                      "escalated": e.escalated, "created_at": str(e.created_at)}
                    for e in res.scalars()]
