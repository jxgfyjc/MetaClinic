"""V1.2 repositories: DiagnosisRecord, DoctorAccessToken, CaseImport,
ExperiencePattern, LabReport, TongueImage.

Each repo opens its own short session via get_sessionmaker() per call.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update

from .db import (
    CaseImport, DiagnosisRecord, DoctorAccessToken, ExperiencePattern,
    LabReport, LabResult, TongueImage, get_sessionmaker,
)


def _uid() -> str:
    return uuid.uuid4().hex[:24]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# V1.2 §2: DiagnosisRecord
# ---------------------------------------------------------------------------
class DiagnosisRecordRepo:
    @staticmethod
    async def create(*, encounter_id: str, patient_id: str,
                     doctor_id: str = "", ai_output_id: str = "",
                     module: str = "western",
                     western_diagnosis: str = "", tcm_syndrome: str = "",
                     main_evidence: str = "", treatment_goal: str = "",
                     review_status: str = "pending", source: str = "ai") -> dict:
        sm = get_sessionmaker()
        did = _uid()
        async with sm() as s:
            rec = DiagnosisRecord(
                id=did, encounter_id=encounter_id, patient_id=patient_id,
                doctor_id=doctor_id, ai_output_id=ai_output_id, module=module,
                western_diagnosis=western_diagnosis, tcm_syndrome=tcm_syndrome,
                main_evidence=main_evidence, treatment_goal=treatment_goal,
                review_status=review_status, source=source)
            s.add(rec)
            await s.commit()
            return DiagnosisRecordRepo._to_dict(rec)

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(DiagnosisRecord)
                .where(DiagnosisRecord.encounter_id == encounter_id)
                .order_by(DiagnosisRecord.created_at))
            return [DiagnosisRecordRepo._to_dict(r) for r in res.scalars().all()]

    @staticmethod
    async def update(did: str, **fields) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(DiagnosisRecord, did)
            if rec is None:
                return None
            for k, v in fields.items():
                if hasattr(rec, k) and v is not None:
                    setattr(rec, k, v)
            await s.commit()
            return DiagnosisRecordRepo._to_dict(rec)

    @staticmethod
    def _to_dict(r: DiagnosisRecord) -> dict:
        return {
            "id": r.id, "encounter_id": r.encounter_id,
            "patient_id": r.patient_id, "doctor_id": r.doctor_id,
            "ai_output_id": r.ai_output_id, "module": r.module,
            "western_diagnosis": r.western_diagnosis,
            "tcm_syndrome": r.tcm_syndrome, "main_evidence": r.main_evidence,
            "treatment_goal": r.treatment_goal,
            "review_status": r.review_status, "doctor_notes": r.doctor_notes,
            "source": r.source, "created_at": str(r.created_at),
        }


# ---------------------------------------------------------------------------
# V1.2 §6: DoctorAccessToken
# ---------------------------------------------------------------------------
class DoctorTokenRepo:
    @staticmethod
    async def create_token(doctor_id: str, ttl_hours: int = 24) -> dict:
        sm = get_sessionmaker()
        token = uuid.uuid4().hex
        now = _now()
        expires = now + timedelta(hours=ttl_hours)
        async with sm() as s:
            rec = DoctorAccessToken(token=token, doctor_id=doctor_id,
                                    created_at=now, expires_at=expires)
            s.add(rec)
            await s.commit()
            return {"token": token, "doctor_id": doctor_id,
                    "expires_at": str(expires)}

    @staticmethod
    async def validate(token: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(DoctorAccessToken, token)
            if rec is None:
                return None
            # SQLite stores datetimes as naive strings; normalize both sides
            # to offset-aware UTC before comparing to avoid TZ mismatch errors.
            def _to_aware(dt: datetime) -> datetime:
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt
            if _to_aware(rec.expires_at) < _now():
                return None
            # increment used_count
            rec.used_count += 1
            await s.commit()
            return {"token": token, "doctor_id": rec.doctor_id,
                    "used_count": rec.used_count}


# ---------------------------------------------------------------------------
# V1.2 §5: CaseImport + ExperiencePattern
# ---------------------------------------------------------------------------
class CaseImportRepo:
    @staticmethod
    async def create(*, doctor_id: str, total_count: int,
                     cases_json: dict) -> dict:
        sm = get_sessionmaker()
        cid = _uid()
        async with sm() as s:
            rec = CaseImport(id=cid, doctor_id=doctor_id,
                             status="pending", total_count=total_count,
                             processed_count=0,
                             cases_json=cases_json)
            s.add(rec)
            await s.commit()
            return CaseImportRepo._to_dict(rec)

    @staticmethod
    async def update_progress(cid: str, *, processed_count: int | None = None,
                              status: str | None = None,
                              structured_json: dict | None = None,
                              patterns_json: dict | None = None,
                              error: str | None = None) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(CaseImport, cid)
            if rec is None:
                return None
            if processed_count is not None:
                rec.processed_count = processed_count
            if status is not None:
                rec.status = status
            if structured_json is not None:
                rec.structured_json = structured_json
            if patterns_json is not None:
                rec.patterns_json = patterns_json
            if error is not None:
                rec.error = error
            await s.commit()
            return CaseImportRepo._to_dict(rec)

    @staticmethod
    async def get(cid: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(CaseImport, cid)
            return CaseImportRepo._to_dict(rec) if rec else None

    @staticmethod
    async def list_for_doctor(doctor_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(CaseImport)
                .where(CaseImport.doctor_id == doctor_id)
                .order_by(CaseImport.created_at.desc()))
            return [CaseImportRepo._to_dict(r) for r in res.scalars().all()]

    @staticmethod
    def _to_dict(r: CaseImport) -> dict:
        return {
            "id": r.id, "doctor_id": r.doctor_id, "status": r.status,
            "total_count": r.total_count, "processed_count": r.processed_count,
            "cases_json": r.cases_json, "structured_json": r.structured_json,
            "patterns_json": r.patterns_json, "error": r.error,
            "created_at": str(r.created_at),
        }


class ExperiencePatternRepo:
    @staticmethod
    async def create(*, doctor_id: str, case_import_id: str = "",
                     category: str = "syndrome", pattern_text: str = "",
                     source_cases: list | None = None,
                     sample_size: int = 0, conditions: str = "",
                     exceptions: str = "", confidence: float = 0.0) -> dict:
        sm = get_sessionmaker()
        pid = _uid()
        async with sm() as s:
            rec = ExperiencePattern(
                id=pid, doctor_id=doctor_id,
                case_import_id=case_import_id, category=category,
                pattern_text=pattern_text,
                source_cases=source_cases or [], sample_size=sample_size,
                conditions=conditions, exceptions=exceptions,
                confidence=confidence, confirmed=False)
            s.add(rec)
            await s.commit()
            return ExperiencePatternRepo._to_dict(rec)

    @staticmethod
    async def list_for_doctor(doctor_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(ExperiencePattern)
                .where(ExperiencePattern.doctor_id == doctor_id)
                .order_by(ExperiencePattern.created_at.desc()))
            return [ExperiencePatternRepo._to_dict(r) for r in res.scalars().all()]

    @staticmethod
    async def confirm(pid: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(ExperiencePattern, pid)
            if rec is None:
                return None
            rec.confirmed = True
            await s.commit()
            return ExperiencePatternRepo._to_dict(rec)

    @staticmethod
    def _to_dict(r: ExperiencePattern) -> dict:
        return {
            "id": r.id, "doctor_id": r.doctor_id,
            "case_import_id": r.case_import_id, "category": r.category,
            "pattern_text": r.pattern_text, "source_cases": r.source_cases,
            "sample_size": r.sample_size, "conditions": r.conditions,
            "exceptions": r.exceptions, "confidence": r.confidence,
            "confirmed": r.confirmed, "created_at": str(r.created_at),
        }


# ---------------------------------------------------------------------------
# V1.2 §1: LabReport persistence from OCR + TongueImage
# ---------------------------------------------------------------------------
class LabReportRepo:
    @staticmethod
    async def create_from_ocr(*, encounter_id: str, patient_id: str,
                              ocr_text: str, image_key: str = "",
                              items: list[dict] | None = None) -> dict:
        """Create LabReport + LabResult rows from OCR output.

        items: [{test_name, value, unit, reference_low, reference_high,
                 abnormal_flag}]
        """
        sm = get_sessionmaker()
        report_id = _uid()
        async with sm() as s:
            rec = LabReport(
                id=report_id, encounter_id=encounter_id,
                patient_id=patient_id, source_image_id=image_key,
                ocr_text=ocr_text[:5000], ocr_confidence=0.9)
            s.add(rec)
            # persist structured lab results
            for item in (items or []):
                lr = LabResult(
                    id=_uid(), lab_report_id=report_id,
                    patient_id=patient_id, encounter_id=encounter_id,
                    test_name=item.get("test_name", item.get("item", "")),
                    canonical_name=item.get("canonical_name", ""),
                    value=str(item.get("value", "")),
                    unit=item.get("unit", ""),
                    reference_low=item.get("reference_low",
                                           item.get("ref_low", "")),
                    reference_high=item.get("reference_high",
                                            item.get("ref_high", "")),
                    abnormal_flag=item.get("abnormal_flag",
                                           item.get("flag", "")),
                    source_image_id=image_key)
                s.add(lr)
            await s.commit()
            return {"id": report_id, "encounter_id": encounter_id,
                    "item_count": len(items or []),
                    "ocr_text_length": len(ocr_text)}

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(LabReport)
                .where(LabReport.encounter_id == encounter_id)
                .order_by(LabReport.created_at))
            return [{
                "id": r.id, "encounter_id": r.encounter_id,
                "patient_id": r.patient_id, "source_image_id": r.source_image_id,
                "ocr_text": r.ocr_text, "ocr_confidence": r.ocr_confidence,
                "created_at": str(r.created_at),
            } for r in res.scalars().all()]


class TongueImageRepo:
    @staticmethod
    async def create(*, encounter_id: str, patient_id: str,
                     object_key: str, compressed_key: str = "") -> dict:
        sm = get_sessionmaker()
        tid = _uid()
        async with sm() as s:
            rec = TongueImage(
                id=tid, encounter_id=encounter_id, patient_id=patient_id,
                object_key=object_key)
            s.add(rec)
            await s.commit()
            return {"id": tid, "encounter_id": encounter_id,
                    "patient_id": patient_id, "object_key": object_key,
                    "compressed_key": compressed_key}

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(TongueImage)
                .where(TongueImage.encounter_id == encounter_id)
                .order_by(TongueImage.created_at))
            return [{
                "id": r.id, "encounter_id": r.encounter_id,
                "patient_id": r.patient_id, "object_key": r.object_key,
                "created_at": str(r.created_at),
            } for r in res.scalars().all()]
