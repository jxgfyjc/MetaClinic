"""Idempotent Demo data init — demo.md 第四节.

黄金路径所需实体写入 SQLite ORM 表 + MockHIS 内存字典（双写，兼容现有读路径）。
幂等：重复运行不追加、不覆盖已编辑病历。固定 ID（p-001..p-005 / enc-001..enc-005 / dr-001..dr-003）。

AI run / 处方草稿 / 随访 **不预置** —— 演示时实时生成（真实模型调用）。

全部 DEMO ONLY，无真实 PII。instruct #51。
"""
from __future__ import annotations

import json
from typing import Any

from scripts.seed_demo import (
    DEMO_DOCTORS, DEMO_NURSE, DEMO_DRUGS, DEMO_FORMULAS,
    DEMO_PATIENTS, DEMO_ENCOUNTERS, DEMO_KNOWLEDGE,
)

# instruct_1.md §3: at least two distinct doctor profiles for comparison.
DEMO_DOCTOR_PROFILES = [
    {
        "doctor_id": "dr-001",
        "dept": "全科/中西医结合",
        "specialties": ["糖尿病", "中西医结合慢病管理"],
        "syndrome_preference": "重视气阴两虚证，优先益气养阴法",
        "formula_preference": "偏好经典经方（如生脉散、玉泉丸）加减，少用大方",
        "herb_preference": "组方精简，药味精简（8-12味），注重君臣佐使",
        "non_drug_preference": "重视生活方式干预，配合推拿疏通经络",
        "analysis_focus": "西医指标优先，中医辨证辅助；注重并发症筛查",
        "experience_notes": ["糖尿病气阴两虚证用生脉散加减效果稳定"],
    },
    {
        "doctor_id": "dr-002",
        "dept": "中医科",
        "specialties": ["中医内科", "针灸推拿"],
        "syndrome_preference": "注重阴虚燥热与瘀血并见证，活血化瘀贯穿全程",
        "formula_preference": "偏好滋阴润燥方（增液汤、白虎加人参汤）合活血方",
        "herb_preference": "常加丹参、川芎活血；组方12-16味，注重气血同治",
        "non_drug_preference": "首选针灸通络，配合艾灸足三里；推拿用于下肢麻木",
        "analysis_focus": "中医辨证为主，西医指标参考；注重经络评估",
        "experience_notes": ["糖尿病周围神经病变针灸取穴足三里、三阴交效果好"],
    },
]

# instruct_1.md §4: independent therapy catalog. source_type hospital = real catalog
# items with codes; knowledge = textbook-established. NO fabricated codes.
DEMO_THERAPY_PROGRAMS = [
    {
        "name": "糖尿病康复推拿",
        "category": "推拿",
        "applicable_scenarios": "糖尿病伴周围神经病变、下肢麻木乏力",
        "therapeutic_goal": "疏通经络、改善微循环、缓解下肢麻木",
        "frequency_duration": "每周2-3次，每次30分钟，疗程4周",
        "contraindications": "皮肤破损、严重感觉异常、糖尿病足溃疡禁用",
        "hospital_code": "TCM-TN-001",
        "source_type": "hospital",
    },
    {
        "name": "针灸通络治疗",
        "category": "针灸",
        "applicable_scenarios": "糖尿病周围神经病变、气滞血瘀型疼痛",
        "therapeutic_goal": "通经活络、行气活血、改善神经传导",
        "frequency_duration": "每周3次，每次留针20分钟，疗程6周",
        "contraindications": "出血倾向、局部感染、严重糖尿病足慎用",
        "hospital_code": "TCM-ZJ-002",
        "source_type": "hospital",
    },
    {
        "name": "足三里艾灸",
        "category": "艾灸",
        "applicable_scenarios": "气虚乏力、脾胃虚弱、免疫力低下",
        "therapeutic_goal": "温阳益气、健脾和胃、增强体质",
        "frequency_duration": "每周2次，每次15分钟，疗程4周",
        "contraindications": "阴虚火旺者慎用；皮肤过敏者禁用；糖尿病感觉异常者注意温度",
        "hospital_code": "TCM-AJ-003",
        "source_type": "hospital",
    },
    {
        "name": "中频理疗（糖尿病改善微循环）",
        "category": "理疗",
        "applicable_scenarios": "糖尿病微循环障碍、下肢麻木",
        "therapeutic_goal": "改善微循环、促进神经修复、缓解麻木",
        "frequency_duration": "每日1次，每次20分钟，疗程2周",
        "contraindications": "急性炎症、出血倾向、心区部位禁用",
        "hospital_code": "PT-ZP-004",
        "source_type": "hospital",
    },
    {
        "name": "康复训练（糖尿病运动处方）",
        "category": "康复训练",
        "applicable_scenarios": "2型糖尿病血糖控制、超重、心肺功能下降",
        "therapeutic_goal": "提高胰岛素敏感性、辅助降糖、改善心肺功能",
        "frequency_duration": "每周5次，每次30-45分钟有氧运动（快走/骑车）",
        "contraindications": "血糖>16.7mmol/L、酮症、严重视网膜病变、足部溃疡暂停",
        "hospital_code": "RT-YD-005",
        "source_type": "hospital",
    },
]


class TherapyProgramRepo_static:
    """Static wrapper so init_demo can call upsert without circular import."""
    @staticmethod
    async def upsert(**kwargs):
        from packages.shared.therapy_repo import TherapyProgramRepo
        return await TherapyProgramRepo.upsert(**kwargs)


async def init_demo_data(his) -> dict:
    """Idempotent: upsert patients/encounters/users/drugs/formulas/labs into ORM
    + MockHIS memory; seed knowledge corpus. Safe to run on every API startup."""
    from packages.shared.db import get_sessionmaker
    from packages.shared.repo import (
        EncounterRepo, LabResultRepo, PatientRepo, UserRepo,
    )
    from sqlalchemy import select

    counts: dict[str, int] = {}

    # -- users (doctors) ---------------------------------------------------
    # updatev2_2 §1d: seed default password (demo123) for demo doctors/nurse
    from packages.shared.auth import hash_password
    DEFAULT_PW_HASH = hash_password("demo123")
    for d in DEMO_DOCTORS:
        await UserRepo.upsert(d["id"], name=d["name"], role=d.get("role", "DOCTOR"),
                              password_hash=DEFAULT_PW_HASH)
    await UserRepo.upsert(DEMO_NURSE["id"], name=DEMO_NURSE["name"],
                          role=DEMO_NURSE.get("role", "NURSE"),
                          password_hash=DEFAULT_PW_HASH)
    counts["users"] = len(DEMO_DOCTORS) + 1

    # -- patients (upsert by fixed id) ------------------------------------
    for p in DEMO_PATIENTS:
        await PatientRepo.upsert(
            p["id"], name=p["name"], gender=p.get("sex", p.get("gender", "")),
            age=p.get("age", 0), phone=p.get("phone", ""),
            extra={"insurance": p.get("insurance", ""),
                   "allergies": p.get("allergies", [])})
    counts["patients"] = len(DEMO_PATIENTS)

    # -- encounters (upsert by fixed id) + labs ---------------------------
    for e in DEMO_ENCOUNTERS:
        await EncounterRepo.upsert(
            e["id"], patient_id=e["patient_id"], doctor_id=e.get("doctor_id", ""),
            chief_complaint=e.get("chief_complaint", ""),
            encounter_no=e["id"], status=e.get("status", "IN_PROGRESS"),
            extra={"case_type": e.get("case_type", ""),
                   "raw_text": e.get("raw_text", ""),
                   "history": e.get("history", {}),
                   "medications": e.get("medications", []),
                   "allergies": _patient_allergies(e["patient_id"]),
                   "labs": e.get("labs", []),
                   "ts": e.get("ts", ""),
                   "demo_only": True})
        # persist labs to LabResult table (idempotent per encounter)
        if e.get("labs"):
            await LabResultRepo.upsert_for_encounter(
                encounter_id=e["id"], patient_id=e["patient_id"],
                labs=e["labs"])
    counts["encounters"] = len(DEMO_ENCOUNTERS)

    # -- doctor profiles (instruct_1.md §3) -------------------------------
    from packages.shared.therapy_repo import DoctorProfileRepo
    for dp in DEMO_DOCTOR_PROFILES:
        await DoctorProfileRepo.upsert(
            doctor_id=dp["doctor_id"], dept=dp.get("dept", ""),
            specialties=dp.get("specialties", []),
            syndrome_preference=dp.get("syndrome_preference", ""),
            formula_preference=dp.get("formula_preference", ""),
            herb_preference=dp.get("herb_preference", ""),
            non_drug_preference=dp.get("non_drug_preference", ""),
            analysis_focus=dp.get("analysis_focus", ""),
            experience_notes=dp.get("experience_notes", []))
    counts["doctor_profiles"] = len(DEMO_DOCTOR_PROFILES)

    # -- therapy programs catalog (instruct_1.md §4) ----------------------
    for tp in DEMO_THERAPY_PROGRAMS:
        await TherapyProgramRepo_static.upsert(
            name=tp["name"], category=tp["category"],
            applicable_scenarios=tp.get("applicable_scenarios", ""),
            therapeutic_goal=tp.get("therapeutic_goal", ""),
            frequency_duration=tp.get("frequency_duration", ""),
            contraindications=tp.get("contraindications", ""),
            hospital_code=tp.get("hospital_code", ""),
            source_type=tp.get("source_type", "hospital"))
    counts["therapy_programs"] = len(DEMO_THERAPY_PROGRAMS)

    # -- MockHIS memory (compat with existing read paths) -----------------
    if not (his._patients and his._drugs and his._formulas):
        for p in DEMO_PATIENTS:
            his.seed_patient(p)
        for e in DEMO_ENCOUNTERS:
            his.seed_encounter(e)
        his.seed_drugs(DEMO_DRUGS)
        his.seed_formulas(DEMO_FORMULAS)
    counts["drugs"] = len(DEMO_DRUGS)
    counts["formulas"] = len(DEMO_FORMULAS)

    # -- knowledge corpus (idempotent: skip if already seeded) ------------
    try:
        r_kn = await _seed_knowledge_idempotent()
        counts["knowledge_chunks"] = r_kn.get("chunks", 0)
    except Exception as e:
        counts["knowledge_error"] = str(e)[:120]

    return counts


def _patient_allergies(pid: str) -> list[dict]:
    for p in DEMO_PATIENTS:
        if p["id"] == pid:
            return p.get("allergies", [])
    return []


async def _seed_knowledge_idempotent() -> dict:
    """Seed the RAG corpus only if the knowledge_chunks table is empty."""
    from services.knowledge.rag import KnowledgeStore, chunk_text, metadata_for
    from packages.shared.providers import EmbeddingProvider, AIUnavailable
    import aiosqlite
    from packages.shared.config import get_settings, REPO_ROOT

    url = get_settings().database_url
    path = url.replace("sqlite+aiosqlite:///", "")
    if not path.startswith("/"):
        path = str(REPO_ROOT / path)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("SELECT count(*) FROM knowledge_chunks")
        existing = (await cur.fetchone())[0]
    if existing > 0:
        return {"chunks": existing, "skipped": "already seeded"}

    chunk_dicts: list[dict] = []
    for doc in DEMO_KNOWLEDGE:
        chunks = chunk_text(doc["text"])
        for i, c in enumerate(chunks):
            chunk_dicts.append({
                "document_id": "", "source_id": doc["title"],
                "text": c, "chunk_index": i,
                "metadata": metadata_for(
                    source_type=doc["source_type"], domain=doc["domain"],
                    subdomain=doc.get("subdomain", ""), title=doc["title"],
                    trust_tier=doc.get("trust_tier", "E"),
                    version=doc.get("version", "")),
            })
    if not chunk_dicts:
        return {"chunks": 0}
    try:
        emb = EmbeddingProvider("embedding")
        vecs = await emb.embed([c["text"] for c in chunk_dicts])
    except AIUnavailable as e:
        return {"chunks": len(chunk_dicts), "seeded": 0, "error": str(e)}
    ids = await KnowledgeStore().add_chunks(chunk_dicts, vecs)
    return {"chunks": len(ids), "documents": len(DEMO_KNOWLEDGE)}


def main():
    import asyncio
    from packages.shared.db import init_engine
    from packages.shared.his_adapter import get_his_adapter

    async def _run():
        await init_engine()
        his = get_his_adapter()
        r = await init_demo_data(his)
        print(json.dumps(r, ensure_ascii=False, indent=2))

    asyncio.run(_run())


if __name__ == "__main__":
    main()
