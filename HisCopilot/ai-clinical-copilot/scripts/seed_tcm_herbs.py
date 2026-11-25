"""Seed TCM herbs from traditional_medicine.md into the Drug table.

Parses the ~500 herb names organized in 10 categories (root/rhizome, fruit/seed,
flower, whole-herb, vine/wood, animal, leaf/bark, fungi/algae, mineral, other)
and inserts each as a Drug row with category="tcm-herb".

Idempotent: skips herbs that already exist in the tcm-herb category.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


HERB_MD = Path("/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/instruct/traditional_medicine.md")


def parse_herbs() -> list[dict]:
    """Parse pipe-separated herb names from traditional_medicine.md.

    Lines with "类中药材" are category headers; the following line has
    pipe-separated herb names. We capture the category in the note field.
    """
    rows: list[dict] = []
    current_category = ""
    lines = HERB_MD.read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "类中药材" in line:
            current_category = line.replace("类中药材", "").strip()
            continue
        for herb in line.split("|"):
            herb = herb.strip()
            if herb:
                rows.append({
                    "name": herb,
                    "generic_name": herb,
                    "category": "tcm-herb",
                    "form": "饮片",
                    "unit": "g",
                    "source": "knowledge",
                    "his_code": "",
                    "note": current_category,
                })
    return rows


async def seed():
    """Insert herbs into the Drug table. Idempotent — skips existing."""
    from packages.shared.db import init_engine
    from packages.shared.drug_repo import DrugRepo

    await init_engine()

    # Check if already seeded
    existing = await DrugRepo.search(q="", category="tcm-herb", limit=1)
    if existing:
        count = len(await DrugRepo.search(q="", category="tcm-herb", limit=10000))
        print(f"[seed_tcm_herbs] already seeded ({count} tcm-herb drugs). Skipping.")
        return 0, 0

    rows = parse_herbs()
    print(f"[seed_tcm_herbs] parsed {len(rows)} herbs from {HERB_MD.name}")

    # Use import_batch for bulk insert
    result = await DrugRepo.import_batch(rows)
    print(f"[seed_tcm_herbs] imported {result['imported']} herbs, skipped {result['skipped']}")
    return result["imported"], result["skipped"]


if __name__ == "__main__":
    imported, skipped = asyncio.run(seed())
    print(f"Done: {imported} imported, {skipped} skipped")
