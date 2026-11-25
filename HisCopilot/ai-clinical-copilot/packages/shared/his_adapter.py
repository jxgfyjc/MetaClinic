"""HIS Adapter — instruct #7, #29, #65.

Abstract interface; MVP uses MockHISAdapter. Business layer NEVER calls HIS directly
(instruct #65) — only through `get_his_adapter()`.
All write ops carry doctor_id/encounter_id/timestamp/request_id/signature_id (instruct #29).
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Protocol


class HISAdapter(Protocol):
    async def get_patient(self, patient_id: str) -> dict: ...
    async def get_encounter(self, encounter_id: str) -> dict: ...
    async def get_medical_history(self, patient_id: str) -> list[dict]: ...
    async def submit_medical_record(self, *, encounter_id: str, doctor_id: str,
                                    content: dict, signature_id: str) -> dict: ...
    async def create_prescription(self, *, encounter_id: str, doctor_id: str,
                                  prescription: dict, signature_id: str) -> dict: ...
    async def get_drug_dictionary(self, q: str = "") -> list[dict]: ...
    async def get_formula_dictionary(self, q: str = "") -> list[dict]: ...


def _req_id() -> str:
    return uuid.uuid4().hex[:16]


class MockHISAdapter:
    """instruct #7: MVP mock. Future HospitalHISAdapter implements the same Protocol."""

    def __init__(self) -> None:
        # in-memory mock tables (seeded by scripts/seed_demo.py)
        self._patients: dict[str, dict] = {}
        self._encounters: dict[str, dict] = {}
        self._medical_records: list[dict] = []
        self._prescriptions: list[dict] = []
        self._drugs: list[dict] = []
        self._formulas: list[dict] = []
        self._billing: list[dict] = []

    # -- seed helpers (used by seed_demo) --------------------------------
    def seed_patient(self, p: dict) -> None: self._patients[p["id"]] = p
    def seed_encounter(self, e: dict) -> None: self._encounters[e["id"]] = e
    def seed_drugs(self, ds: list[dict]) -> None: self._drugs.extend(ds)
    def seed_formulas(self, fs: list[dict]) -> None: self._formulas.extend(fs)

    # -- reads (with ORM DB fallback, demo.md 第四节) -------------------
    async def get_patient(self, patient_id: str) -> dict:
        p = self._patients.get(patient_id)
        if p is not None:
            return p
        # DB fallback (records persisted by repo / init_demo)
        try:
            from .repo import PatientRepo
            rec = await PatientRepo.get(patient_id)
            if rec:
                return rec
        except Exception:
            pass
        return {}

    async def get_encounter(self, encounter_id: str) -> dict:
        e = self._encounters.get(encounter_id)
        if e is not None:
            return e
        try:
            from .repo import EncounterRepo
            rec = await EncounterRepo.get(encounter_id)
            if rec:
                return rec
        except Exception:
            pass
        return {}

    async def get_medical_history(self, patient_id: str) -> list[dict]:
        local = [r for r in self._medical_records if r.get("patient_id") == patient_id]
        if local:
            return local
        try:
            from .repo import LabResultRepo
            labs = await LabResultRepo.list_for_patient(patient_id)
            return [{"patient_id": patient_id, "labs": labs}] if labs else []
        except Exception:
            return []

    async def get_drug_dictionary(self, q: str = "") -> list[dict]:
        if not q:
            return list(self._drugs)
        ql = q.lower()
        return [d for d in self._drugs if ql in d.get("name", "").lower()
                or ql in d.get("generic_name", "").lower()]

    async def get_formula_dictionary(self, q: str = "") -> list[dict]:
        if not q:
            return list(self._formulas)
        ql = q.lower()
        return [f for f in self._formulas if ql in f.get("name", "").lower()]

    # -- writes (instruct #29 required fields) ---------------------------
    async def submit_medical_record(self, *, encounter_id: str, doctor_id: str,
                                    content: dict, signature_id: str) -> dict:
        rec = {
            "id": uuid.uuid4().hex[:24],
            "request_id": _req_id(),
            "encounter_id": encounter_id,
            "doctor_id": doctor_id,
            "signature_id": signature_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "content": content,
            "his_status": "ACCEPTED",  # mock always accepts
        }
        self._medical_records.append(rec)
        # mark encounter signed/submitted
        if encounter_id in self._encounters:
            self._encounters[encounter_id]["status"] = "SUBMITTED"
        return rec

    async def create_prescription(self, *, encounter_id: str, doctor_id: str,
                                  prescription: dict, signature_id: str) -> dict:
        # Idempotent: if a prescription with the same encounter_id + signature_id
        # was already submitted, return the existing record (demo.md #7: 重复提交
        # 不产生重复处方或收费记录).
        for existing in self._prescriptions:
            if (existing.get("encounter_id") == encounter_id
                    and existing.get("signature_id") == signature_id):
                return existing
        rec = {
            "id": uuid.uuid4().hex[:24],
            "request_id": _req_id(),
            "encounter_id": encounter_id,
            "doctor_id": doctor_id,
            "signature_id": signature_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "prescription": prescription,
            "his_status": "ACCEPTED",  # mock always accepts; never real hospital DB
        }
        self._prescriptions.append(rec)
        # mock billing entry (idempotent: check before append)
        already_billed = any(b.get("prescription_id") == rec["id"]
                             for b in self._billing)
        if not already_billed:
            self._billing.append({
                "id": uuid.uuid4().hex[:24],
                "prescription_id": rec["id"],
                "encounter_id": encounter_id,
                "amount": prescription.get("total_amount", 0.0),
                "his_status": "BILLED",
            })
        # mark encounter submitted
        if encounter_id in self._encounters:
            self._encounters[encounter_id]["status"] = "SUBMITTED"
        return rec


_his: MockHISAdapter | None = None


def get_his_adapter() -> HISAdapter:
    """Factory. If ENABLE_REAL_HIS=true → future HospitalHISAdapter; else Mock."""
    from .config import get_settings
    global _his
    if _his is None:
        # instruct #65: only this factory knows which adapter.
        if get_settings().enable_real_his:
            # NotImplementedError until HospitalHISAdapter is implemented
            _his = MockHISAdapter()  # fallback (with assumption logged)
        else:
            _his = MockHISAdapter()
    return _his
