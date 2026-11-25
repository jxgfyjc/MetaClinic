"""updatev2_3 §2: E-Prescription repository.

Formal, persisted prescription documents. Unlike MockHISAdapter's in-memory
list, these survive API restart. One encounter can have multiple
e-prescriptions (western_drugs, herbal_decoction, patent_medicine, therapy_orders).

Each repo function opens its own short session via get_sessionmaker().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .db import EPrescription, get_sessionmaker


def _uid() -> str:
    return uuid.uuid4().hex[:24]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EPrescriptionRepo:
    @staticmethod
    async def create(*, encounter_id: str, patient_id: str, doctor_id: str = "",
                     prescription_draft_id: str = "", doc_type: str = "western_drugs",
                     diagnosis_summary: str = "", prescription_json: dict | None = None,
                     signature_id: str = "", doctor_name: str = "",
                     his_status: str = "ACCEPTED", request_id: str = "") -> dict:
        """Create a formal e-prescription record. Idempotent on
        encounter_id + prescription_draft_id + doc_type + signature_id."""
        sm = get_sessionmaker()
        async with sm() as s:
            # Idempotency: same encounter + draft + doc_type + signature
            res = await s.execute(select(EPrescription).where(
                EPrescription.encounter_id == encounter_id,
                EPrescription.prescription_draft_id == prescription_draft_id,
                EPrescription.doc_type == doc_type,
                EPrescription.signature_id == signature_id))
            existing = res.scalars().first()
            if existing is not None:
                return EPrescriptionRepo._to_dict(existing)
            ep = EPrescription(
                id=_uid(), encounter_id=encounter_id, patient_id=patient_id,
                doctor_id=doctor_id,
                prescription_draft_id=prescription_draft_id,
                doc_type=doc_type, diagnosis_summary=diagnosis_summary,
                prescription_json=prescription_json or {},
                signature_id=signature_id, doctor_name=doctor_name,
                his_status=his_status, request_id=request_id)
            s.add(ep)
            await s.commit()
            return EPrescriptionRepo._to_dict(ep)

    @staticmethod
    def _to_dict(ep: EPrescription) -> dict:
        return {
            "id": ep.id, "encounter_id": ep.encounter_id,
            "patient_id": ep.patient_id, "doctor_id": ep.doctor_id,
            "prescription_draft_id": ep.prescription_draft_id,
            "doc_type": ep.doc_type, "diagnosis_summary": ep.diagnosis_summary,
            "prescription_json": ep.prescription_json,
            "signature_id": ep.signature_id, "doctor_name": ep.doctor_name,
            "his_status": ep.his_status, "request_id": ep.request_id,
            "created_at": str(ep.created_at) if ep.created_at else "",
        }

    @staticmethod
    async def get(ep_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            ep = await s.get(EPrescription, ep_id)
            if ep is None:
                return None
            return EPrescriptionRepo._to_dict(ep)

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(EPrescription).where(
                EPrescription.encounter_id == encounter_id
            ).order_by(EPrescription.created_at.desc()))
            return [EPrescriptionRepo._to_dict(ep) for ep in res.scalars()]

    @staticmethod
    async def list_for_patient(patient_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(EPrescription).where(
                EPrescription.patient_id == patient_id
            ).order_by(EPrescription.created_at.desc()))
            return [EPrescriptionRepo._to_dict(ep) for ep in res.scalars()]
