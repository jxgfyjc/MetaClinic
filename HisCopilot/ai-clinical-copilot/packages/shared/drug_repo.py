"""V1.2 §3: Drug library repository — DB-backed CRUD + search + CSV import.

instruct_1_2.md §3: drug library with hundreds of entries, search/filter/
select/CSV import. Fields: generic_name, spec, form, unit, usage, source,
hospital code. Doctor can add/modify/delete drugs manually.

DrugRepo opens its own short session via get_sessionmaker() per call.
"""
from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy import select, or_, func, delete

from .db import Drug, get_sessionmaker


def _uid() -> str:
    return uuid.uuid4().hex[:24]


class DrugRepo:
    @staticmethod
    async def create(*, name: str, generic_name: str = "", spec: str = "",
                     unit: str = "", form: str = "", usage: str = "",
                     source: str = "hospital", category: str = "western",
                     his_code: str = "", insurance_code: str = "",
                     contraindication: str = "", note: str = "") -> dict:
        sm = get_sessionmaker()
        drug_id = _uid()
        async with sm() as s:
            d = Drug(id=drug_id, name=name, generic_name=generic_name,
                     spec=spec, unit=unit, form=form, usage=usage,
                     source=source, category=category, his_code=his_code,
                     insurance_code=insurance_code,
                     contraindication=contraindication, note=note)
            s.add(d)
            await s.commit()
            return DrugRepo._to_dict(d)

    @staticmethod
    async def update(drug_id: str, **fields) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            d = await s.get(Drug, drug_id)
            if d is None:
                return None
            for k, v in fields.items():
                if hasattr(d, k) and v is not None:
                    setattr(d, k, v)
            await s.commit()
            return DrugRepo._to_dict(d)

    @staticmethod
    async def delete(drug_id: str) -> bool:
        sm = get_sessionmaker()
        async with sm() as s:
            d = await s.get(Drug, drug_id)
            if d is None:
                return False
            await s.delete(d)
            await s.commit()
            return True

    @staticmethod
    async def get(drug_id: str) -> dict | None:
        sm = get_sessionmaker()
        async with sm() as s:
            d = await s.get(Drug, drug_id)
            return DrugRepo._to_dict(d) if d else None

    @staticmethod
    async def search(q: str = "", category: str = "", form: str = "",
                     limit: int = 500) -> list[dict]:
        """DB-first search by name/generic_name LIKE. Filter by category/form."""
        sm = get_sessionmaker()
        async with sm() as s:
            stmt = select(Drug)
            if q:
                pat = f"%{q}%"
                stmt = stmt.where(
                    or_(Drug.name.ilike(pat),
                        Drug.generic_name.ilike(pat)))
            if category:
                stmt = stmt.where(Drug.category == category)
            if form:
                stmt = stmt.where(Drug.form == form)
            stmt = stmt.limit(limit)
            res = await s.execute(stmt)
            rows = res.scalars().all()
            return [DrugRepo._to_dict(d) for d in rows]

    @staticmethod
    async def list_all(limit: int = 500) -> list[dict]:
        sm = get_sessionmaker()
        async with sm() as s:
            res = await s.execute(select(Drug).limit(limit))
            rows = res.scalars().all()
            return [DrugRepo._to_dict(d) for d in rows]

    @staticmethod
    async def import_batch(rows: list[dict]) -> dict:
        """CSV import — each row has keys matching Drug fields.
        Returns {imported, skipped}."""
        sm = get_sessionmaker()
        imported = 0
        skipped = 0
        async with sm() as s:
            for row in rows:
                name = (row.get("name") or "").strip()
                if not name:
                    skipped += 1
                    continue
                d = Drug(
                    id=_uid(), name=name,
                    generic_name=(row.get("generic_name") or "").strip(),
                    spec=(row.get("spec") or row.get("strength") or "").strip(),
                    unit=(row.get("unit") or "").strip(),
                    form=(row.get("form") or "").strip(),
                    usage=(row.get("usage") or "").strip(),
                    source=(row.get("source") or "imported").strip(),
                    category=(row.get("category") or "western").strip(),
                    his_code=(row.get("his_code") or "").strip(),
                    insurance_code=(row.get("insurance_code") or "").strip(),
                    contraindication=(row.get("contraindication") or "").strip(),
                    note=(row.get("note") or "").strip(),
                )
                s.add(d)
                imported += 1
            await s.commit()
        return {"imported": imported, "skipped": skipped}

    @staticmethod
    def parse_csv(csv_text: str) -> list[dict]:
        """Parse CSV text into list of dicts. Handles BOM and various delimiters."""
        # Strip BOM if present
        if csv_text.startswith("﻿"):
            csv_text = csv_text[1:]
        reader = csv.DictReader(io.StringIO(csv_text))
        return [dict(row) for row in reader]

    @staticmethod
    def _to_dict(d: Drug) -> dict:
        return {
            "id": d.id, "name": d.name, "generic_name": d.generic_name,
            "spec": d.spec, "unit": d.unit, "form": d.form,
            "usage": d.usage, "source": d.source, "category": d.category,
            "his_code": d.his_code, "insurance_code": d.insurance_code,
            "contraindication": d.contraindication, "note": d.note,
        }
