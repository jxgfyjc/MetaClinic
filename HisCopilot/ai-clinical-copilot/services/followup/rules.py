"""Rule engine — instruct #33, #87.

Minimal hard rules only: high-risk followup keywords + allergy/contraindication checks.
LLM never decides high-risk alone; rules run in parallel + combine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# instruct #33 hard rules
HIGH_RISK_KEYWORDS = [
    "胸痛", "呼吸困难", "晕厥", "意识改变", "意识丧失",
    "严重出血", "大量出血", "严重过敏", "过敏性休克",
    "明显恶化", "急剧恶化",
    "严重低血糖",
    "血压过高", "血压过低", "心率过快", "心率过慢",
    "高热", "抽搐", "咯血", "呕血", "黑便",
    # 心血管/高血压急症相关高危表现（case enc-005 随访场景）
    "胸闷", "夜间不能平卧", "端坐呼吸", "视物模糊", "黑矇",
    "肢体无力", "言语不清", "剧烈头痛",
]

ALLERGY_TERMS = ["青霉素", "头孢", "磺胺", "阿司匹林", "布洛芬", "碘"]


@dataclass
class RuleHit:
    rule: str
    severity: str  # attention / high_risk
    detail: str = ""


def scan_followup_text(text: str) -> list[RuleHit]:
    hits: list[RuleHit] = []
    if not text:
        return hits
    for kw in HIGH_RISK_KEYWORDS:
        if kw in text:
            hits.append(RuleHit(rule=f"keyword:{kw}", severity="high_risk",
                                detail=f"匹配高危关键词: {kw}"))
    return hits


def check_allergy(*, candidate_drug: str, allergies: list[str]) -> RuleHit | None:
    """Return a hit if candidate drug name overlaps a known allergy."""
    if not candidate_drug or not allergies:
        return None
    for a in allergies:
        if a and a in candidate_drug:
            return RuleHit(rule="allergy", severity="attention",
                           detail=f"候选药物 {candidate_drug} 与过敏史 {a} 重叠")
    return None


def check_duplicate(*, candidate_drug: str, current_meds: list[str]) -> RuleHit | None:
    if not candidate_drug or not current_meds:
        return None
    for m in current_meds:
        if m and m in candidate_drug:
            return RuleHit(rule="duplicate_medication", severity="attention",
                           detail=f"候选药物 {candidate_drug} 与现有用药 {m} 重复")
    return None


def assess(*, followup_text: str = "", llm_signals: list[str] | None = None) -> dict:
    """Combine hard rules + LLM signals into a risk level. instruct #33."""
    hits = scan_followup_text(followup_text)
    if llm_signals:
        for s in llm_signals:
            hits.append(RuleHit(rule="llm_signal", severity="attention", detail=s))
    has_high = any(h.severity == "high_risk" for h in hits)
    has_attn = any(h.severity == "attention" for h in hits)
    level = "high_risk" if has_high else ("attention" if has_attn else "normal")
    # human-readable reason + recommended action
    high_hits = [h for h in hits if h.severity == "high_risk"]
    attn_hits = [h for h in hits if h.severity == "attention"]
    if high_hits:
        reason = "触发高危规则：" + "；".join(h.detail for h in high_hits)
        action = "建议立即急诊或联系主管医生评估（instruct #34 高危随访须人工介入）"
    elif attn_hits:
        reason = "提示需关注：" + "；".join(h.detail for h in attn_hits[:3])
        action = "建议 24 小时内复诊或电话随访"
    else:
        reason = "未触发任何风险规则"
        action = ""
    return {
        "risk_level": level,
        "triggered_rules": [h.rule for h in hits],
        "triggered_llm_signals": [h.detail for h in hits if h.rule == "llm_signal"],
        "escalation": has_high,
        "rule_version": "minimal-v1",
        "reason": reason,
        "recommended_action": action,
    }
