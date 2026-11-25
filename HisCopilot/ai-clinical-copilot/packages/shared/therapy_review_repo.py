"""Therapy item review repo (updatev2_2 §5).

Per-item accept/edit/reject status for therapy candidates.
Each therapy candidate gets its own TherapyItemReview row, independent of
the prescription draft it belongs to.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .db import TherapyItemReview, get_sessionmaker


def _uid() -> str:
    return uuid.uuid4().hex[:24]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TherapyItemReviewRepo:
    @staticmethod
    async def create_for_draft(*, review_id: str, prescription_draft_id: str,
                               encounter_id: str, patient_id: str,
                               item_index: int,
                               original_content: dict,
                               doctor_id: str = "") -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = TherapyItemReview(
                id=review_id, prescription_draft_id=prescription_draft_id,
                encounter_id=encounter_id, patient_id=patient_id,
                item_index=item_index, status="AI_GENERATED",
                original_content=original_content,
                edited_content={}, doctor_id=doctor_id)
            s.add(rec)
            await s.commit()
            return TherapyItemReviewRepo._to_dict(rec)

    @staticmethod
    async def upsert_for_draft(*, prescription_draft_id: str, encounter_id: str,
                               patient_id: str, item_index: int,
                               original_content: dict,
                               doctor_id: str = "") -> dict:
        """Create if not exists, return existing if found."""
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(TherapyItemReview).where(
                TherapyItemReview.prescription_draft_id == prescription_draft_id,
                TherapyItemReview.item_index == item_index))
            existing = res.scalars().first()
            if existing:
                return TherapyItemReviewRepo._to_dict(existing)
            review_id = _uid()
            rec = TherapyItemReview(
                id=review_id, prescription_draft_id=prescription_draft_id,
                encounter_id=encounter_id, patient_id=patient_id,
                item_index=item_index, status="AI_GENERATED",
                original_content=original_content,
                edited_content={}, doctor_id=doctor_id)
            s.add(rec)
            await s.commit()
            return TherapyItemReviewRepo._to_dict(rec)

    @staticmethod
    async def update_status(review_id: str, *, status: str,
                            edited_content: dict | None = None,
                            doctor_id: str = "") -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(TherapyItemReview, review_id)
            if rec is None:
                return None
            rec.status = status
            if edited_content is not None:
                rec.edited_content = edited_content
                flag_modified(rec, "edited_content")
            if doctor_id:
                rec.doctor_id = doctor_id
            await s.commit()
            return TherapyItemReviewRepo._to_dict(rec)

    @staticmethod
    async def list_for_draft(prescription_draft_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(TherapyItemReview).where(
                TherapyItemReview.prescription_draft_id == prescription_draft_id
            ).order_by(TherapyItemReview.item_index))
            return [TherapyItemReviewRepo._to_dict(r) for r in res.scalars()]

    @staticmethod
    async def list_for_encounter(encounter_id: str) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(TherapyItemReview).where(
                TherapyItemReview.encounter_id == encounter_id
            ).order_by(TherapyItemReview.prescription_draft_id,
                       TherapyItemReview.item_index))
            return [TherapyItemReviewRepo._to_dict(r) for r in res.scalars()]

    @staticmethod
    def _to_dict(r: TherapyItemReview) -> dict:
        return {
            "id": r.id,
            "prescription_draft_id": r.prescription_draft_id,
            "encounter_id": r.encounter_id,
            "patient_id": r.patient_id,
            "item_index": r.item_index,
            "status": r.status,
            "original_content": r.original_content,
            "edited_content": r.edited_content,
            "doctor_id": r.doctor_id,
            "created_at": str(r.created_at) if r.created_at else "",
            "updated_at": str(r.updated_at) if r.updated_at else "",
        }
