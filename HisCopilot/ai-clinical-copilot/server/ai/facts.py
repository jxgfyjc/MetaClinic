"""Fact sheet: turns an encounter into NUMBERED, source-labelled facts that prompts and evidence refer to.

Why numbered facts: the model must cite `F3` instead of free-text "患者有头痛"; the UI then renders the cited
fact (with its provenance: patient wording / doctor entry / voice transcript (unverified) / lab image (OCR))
next to every claim. A citation to a fact id that does not exist is dropped as fabricated. Nothing the model
says can introduce a "fact" that is not in this sheet.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Artifact, Encounter, LabItem
from server.domain.encounters import active_allergy_names, calc_age
from server.domain.enums import ArtifactKind, ProcStatus


@dataclass
class Fact:
    id: str
    label: str
    text: str
    source: str           # patient | doctor | voice | ocr | tongue | system
    reliability: str = "" # note shown in UI, e.g. "语音转写，未经核对"

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "text": self.text, "source": self.source, "reliability": self.reliability}


def build_facts(db: Session, enc: Encounter) -> list[Fact]:
    facts: list[Fact] = []
    n = 0

    def add(label: str, text, source: str, reliability: str = "") -> None:
        nonlocal n
        t = (text if isinstance(text, str) else "；".join(map(str, text))).strip()
        if not t:
            return
        n += 1
        facts.append(Fact(f"F{n}", label, t, source, reliability))

    p = enc.patient
    src = "patient" if enc.source == "patient_intake" else "doctor"
    add("基本信息", f"{p.gender or '性别未填写'}，{calc_age(p) if calc_age(p) is not None else '年龄未填写'}岁", "doctor")
    add("主诉", enc.chief_complaint, src)
    add("现病史", enc.hpi, src)
    add("伴随症状", enc.symptoms_json, src)
    add("既往史/慢性病", enc.past_history or p.chronic_conditions_json, src)
    add("个人史/家族史", enc.personal_family_history, "doctor")
    add("过敏史", active_allergy_names(p), "patient",
        "" if any(a.confirmed for a in p.allergies) else "患者自述，医师未核实")
    meds = [f"{m.get('name', '')} {m.get('dose', '')} {m.get('frequency', '')}".strip()
            for m in (enc.current_meds_json or []) if isinstance(m, dict) and m.get("name")]
    add("当前用药", meds, src)
    v = enc.vitals_json or {}
    add("生命体征", "；".join(f"{k}:{v[k]}" for k in v if v[k] not in ("", None)), "doctor")
    add("体格检查", enc.physical_exam, "doctor")
    spec = enc.special_status_json or {}
    flags = []
    if spec.get("pregnant") is True:
        flags.append("妊娠")
    if spec.get("lactating") is True:
        flags.append("哺乳期")
    if spec.get("renal"):
        flags.append("肾功能不全")
    if spec.get("hepatic"):
        flags.append("肝功能不全")
    add("特殊状态", flags, "doctor")
    f4 = enc.tcm_four_json or {}
    add("中医四诊（医师记录）", "；".join(f"{k}:{f4[k]}" for k in f4 if f4[k]), "doctor")

    for a in db.scalars(select(Artifact).where(Artifact.encounter_id == enc.id, Artifact.deleted_at.is_(None))
                        .order_by(Artifact.created_at)):
        if a.kind == ArtifactKind.AUDIO.value and a.proc_status == ProcStatus.SUCCEEDED.value:
            txt = a.edited_text or a.result_text
            add(f"语音记录（{a.purpose or '病史'}）", txt, "voice",
                "已由人员核对" if a.confirmed_at else "语音转写，未经核对，可能有误")

    labs = db.scalars(select(LabItem).where(LabItem.encounter_id == enc.id, LabItem.deleted_at.is_(None))
                      .order_by(LabItem.sort_order, LabItem.id)).all()
    for l in labs:
        flag = {"H": "偏高↑", "L": "偏低↓"}.get(l.flag, "")
        ref = f"（参考 {l.ref_text}）" if l.ref_text else ""
        add(f"检验·{l.name}", f"{l.value_text}{l.unit}{ref}{(' ' + flag) if flag else ''}", "ocr" if l.source == "ocr" else "doctor",
            "" if l.verified else "OCR 识别，尚未核对原图")
    return facts


def facts_prompt_block(facts: list[Fact]) -> str:
    if not facts:
        return "（本次就诊暂无任何临床资料）"
    lines = []
    for f in facts:
        rel = f"［{f.reliability}］" if f.reliability else ""
        lines.append(f"{f.id} | {f.label} | {f.text} {rel}".rstrip())
    return "\n".join(lines)


def valid_ids(facts: list[Fact]) -> set[str]:
    return {f.id for f in facts}
