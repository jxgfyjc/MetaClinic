"""Therapy programs repo + doctor profile repo (instruct_1.md §3/§4).

instruct_1.md §3: lightweight doctor personalization — dept, specialties,
syndrome/formula/herb/non-drug preferences, experience notes. No per-doctor model.
instruct_1.md §4: independent therapy-program data structure (推拿/针灸/理疗/康复),
distinct from the formula library. source_type hospital/knowledge; AI-generated
candidates are runtime only, never stored as catalog rows (no fabricated codes).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import DoctorProfile, TherapyProgram, get_sessionmaker


# ---------------------------------------------------------------------------
# Doctor profiles (instruct_1.md §3)
# ---------------------------------------------------------------------------
class DoctorProfileRepo:
    @staticmethod
    async def upsert(*, doctor_id: str, dept: str = "", specialties: list | None = None,
                     syndrome_preference: str = "", formula_preference: str = "",
                     herb_preference: str = "", non_drug_preference: str = "",
                     analysis_focus: str = "", experience_notes: list | None = None) -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(DoctorProfile, doctor_id)
            if p is None:
                p = DoctorProfile(doctor_id=doctor_id, dept=dept,
                                  specialties=specialties or [],
                                  syndrome_preference=syndrome_preference,
                                  formula_preference=formula_preference,
                                  herb_preference=herb_preference,
                                  non_drug_preference=non_drug_preference,
                                  analysis_focus=analysis_focus,
                                  experience_notes=experience_notes or [])
                s.add(p)
            else:
                if dept:
                    p.dept = dept
                if specialties is not None:
                    p.specialties = specialties
                if syndrome_preference:
                    p.syndrome_preference = syndrome_preference
                if formula_preference:
                    p.formula_preference = formula_preference
                if herb_preference:
                    p.herb_preference = herb_preference
                if non_drug_preference:
                    p.non_drug_preference = non_drug_preference
                if analysis_focus:
                    p.analysis_focus = analysis_focus
                if experience_notes is not None:
                    p.experience_notes = experience_notes
            await s.commit()
            return DoctorProfileRepo._to_dict(p)

    @staticmethod
    async def get(doctor_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            p = await s.get(DoctorProfile, doctor_id)
            if p is None:
                return None
            return DoctorProfileRepo._to_dict(p)

    @staticmethod
    def _to_dict(p: DoctorProfile) -> dict:
        return {
            "doctor_id": p.doctor_id, "dept": p.dept,
            "specialties": p.specialties or [],
            "syndrome_preference": p.syndrome_preference,
            "formula_preference": p.formula_preference,
            "herb_preference": p.herb_preference,
            "non_drug_preference": p.non_drug_preference,
            "analysis_focus": p.analysis_focus,
            "experience_notes": p.experience_notes or [],
        }


# ---------------------------------------------------------------------------
# Therapy programs catalog (instruct_1.md §4)
# ---------------------------------------------------------------------------
class TherapyProgramRepo:
    @staticmethod
    async def upsert(*, name: str, category: str, applicable_scenarios: str = "",
                     therapeutic_goal: str = "", frequency_duration: str = "",
                     contraindications: str = "", hospital_code: str = "",
                     source_type: str = "hospital") -> dict:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(
                select(TherapyProgram).filter_by(name=name, category=category))
            t = res.scalars().first()
            if t is None:
                t = TherapyProgram(name=name, category=category,
                                   applicable_scenarios=applicable_scenarios,
                                   therapeutic_goal=therapeutic_goal,
                                   frequency_duration=frequency_duration,
                                   contraindications=contraindications,
                                   hospital_code=hospital_code,
                                   source_type=source_type)
                s.add(t)
                await s.commit()
            return TherapyProgramRepo._to_dict(t)

    @staticmethod
    async def list_all(category: str = "") -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            q = select(TherapyProgram).filter_by(is_active=True)
            if category:
                q = q.filter_by(category=category)
            res = await s.execute(q.order_by(TherapyProgram.category, TherapyProgram.name))
            return [TherapyProgramRepo._to_dict(t) for t in res.scalars()]

    @staticmethod
    def _to_dict(t: TherapyProgram) -> dict:
        return {
            "id": t.id, "name": t.name, "category": t.category,
            "applicable_scenarios": t.applicable_scenarios,
            "therapeutic_goal": t.therapeutic_goal,
            "frequency_duration": t.frequency_duration,
            "contraindications": t.contraindications,
            "hospital_code": t.hospital_code,
            "source_type": t.source_type,
        }
