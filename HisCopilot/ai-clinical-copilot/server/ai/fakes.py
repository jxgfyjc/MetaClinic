"""Deterministic stand-ins for the model servers — USED ONLY when XY_AI_MODE=fake (automated tests).

Every payload is marked `"_fake": true` and never reaches production paths (the config default is `live`).
They exercise the real parsing / validation / persistence code, including deliberately malformed outputs that
the pipeline must reject (fabricated fact ids, invalid JSON, empty ASR) — controlled via magic substrings in the
input so tests can trigger each failure mode without touching a model.
"""
from __future__ import annotations

import json
import re

from server.ai.client import LLMResult, ModelError


def _last_user(messages: list[dict]) -> str:
    """All user text in the conversation (markers must survive the pipeline's retry re-prompts)."""
    parts = []
    for m in messages:
        if m["role"] == "user":
            c = m["content"]
            parts.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if p.get("type") == "text"))
    return "\n".join(parts)


def _sys(messages: list[dict]) -> str:
    return next((m["content"] for m in messages if m["role"] == "system"), "")


def chat(messages: list[dict], *, role: str) -> LLMResult:
    system, user = _sys(messages), _last_user(messages)
    if "FAKE_FAIL_LLM" in user:
        raise ModelError("model_unreachable", "（测试）模型服务不可用")
    if "FAKE_BAD_JSON" in user:
        return LLMResult("this is not json at all", "fake", 1)
    ids = re.findall(r"^(F\d+) \|", user, re.M)
    first = ids[0] if ids else "F1"
    ghost = "F99" if "FAKE_GHOST_REF" in user else first
    if "中医病例信息抽取助手" in system:
        # style-profile extraction: deterministic, VERBATIM-only stand-in (the real pipeline re-verifies every field against the case text anyway)
        from server.style.extract import rule_extract
        txt = user.split("病例文本：", 1)[-1]
        out = {"_fake": True, **rule_extract(txt)}
    elif "治疗方案起草" in system:
        out = _plan(user)
        if "STYLE_REFERENCES" in user:
            out["style"] = _plan_style(user)
    elif "differentials" in system and "FAKE_NO_DIFFERENTIALS" in user:
        out = {"_fake": True, "summary": "测试：合法 JSON 但没有候选数组", "note": "no differentials here"}
    elif "differentials" in system and "FAKE_VARIANT_KEYS" in user:
        out = {"_fake": True, "summary": "测试：键名变体", "differential_diagnoses": [
            {"diagnosis": "原发性高血压", "confidence": "medium", "rationale": "测试", "supporting": [{"text": "头痛", "refs": [first]}]},
            {"diagnosis_name": "偏头痛", "confidence": "low", "rationale": "测试", "supporting": [{"text": "头痛", "refs": [first]}]}]}
    elif "differentials" in system:
        out = {"_fake": True, "summary": "测试用西医摘要", "problem_list": ["头痛"],
               "differentials": [
                   {"name": "原发性高血压", "confidence": "medium", "rationale": "测试", "supporting": [{"text": "头痛", "refs": [ghost]}],
                    "contradicting": [], "missing_information": ["家庭血压记录"], "questions_to_ask": ["是否测过血压？"]},
                   {"name": "偏头痛", "confidence": "low", "rationale": "测试", "supporting": [{"text": "头痛", "refs": [first]}],
                    "contradicting": [], "missing_information": [], "questions_to_ask": []}],
               "red_flags": [], "suggested_examinations": ["血压监测"], "uncertainties": [], "missing_information": ["舌脉"]}
    elif "syndromes" in system:
        out = {"_fake": True, "summary": "测试用中医摘要", "four_diagnoses": {"wang": [], "wen": [], "wen_ask": [], "qie": []},
               "missing_four_diagnoses": ["舌象", "脉象"],
               "syndromes": [{"name": "肝阳上亢证", "confidence": "medium", "rationale": "测试",
                              "supporting": [{"text": "头痛", "refs": [first]}], "contradicting": [], "missing_information": [], "questions_to_ask": []}],
               "treatment_principles": ["平肝潜阳"], "formula_directions": [{"name": "天麻钩藤饮方向", "type": "classic", "note": ""}],
               "uncertainties": [], "missing_information": []}
    elif "headline" in system:
        out = {"_fake": True, "headline": "测试综合结论", "key_decisions": ["确认诊断"], "confirm_first": ["确认过敏史"],
               "risk_alerts": [], "conflicts": [], "suggested_diagnosis_set": [], "coordination_notes": []}
    elif "舌诊" in system or "tongue_body" in system:
        out = {"_fake": True, "image_quality": {"usable": True, "score": 0.8, "issues": []},
               "tongue_body": {"color": "淡红", "shape": "胖大", "teeth_marks": "yes", "cracks": "no", "ecchymosis": "no"},
               "coating": {"color": "白", "thickness": "薄", "moisture": "润", "greasy": "no", "peeled": "no"},
               "sublingual": "", "observations": ["舌淡红胖大有齿痕"], "not_observed": ["裂纹"], "uncertain": []}
    else:
        out = {"_fake": True}
    return LLMResult(json.dumps(out, ensure_ascii=False), "fake-llm", 5, "stop")


def _plan(user: str) -> dict:
    herb_name = "甘草+甘遂" if "FAKE_UNSAFE_PLAN" in user else "normal"
    herbs = [{"name": n, "dose_g": g, "special": "", "role": r} for n, g, r in
             [("天麻", 10, "君"), ("钩藤", 12, "君"), ("石决明", 20, "臣"), ("栀子", 6, "佐"), ("黄芩", 6, "佐"),
              ("牛膝", 10, "佐"), ("杜仲", 9, "佐"), ("桑寄生", 12, "佐"), ("夜交藤", 12, "佐"), ("茯神", 12, "佐"), ("益母草", 9, "使")]]
    if herb_name != "normal":
        herbs = herbs[:3] + [{"name": "甘草", "dose_g": 6, "special": "", "role": "使"}, {"name": "甘遂", "dose_g": 1, "special": "", "role": "使"}]
    out = {"_fake": True, "summary": "测试统筹方案", "coordination_notes": ["中西药无重复"],
           "medication": {"western": [{"name": "氨氯地平", "dose": "5mg", "frequency": "qd", "route": "口服", "days": 14,
                                       "reason": "原发性高血压", "considerations": ["监测血压"]}], "patent": []},
           "herbal": [{"title": "天麻钩藤饮加减", "formula_type": "classic", "source_note": "化裁自《杂病证治新义》天麻钩藤饮",
                       "principle": "平肝息风", "herbs": herbs, "doses_count": 7, "decoction": "水煎400ml", "usage": "每日1剂，分早晚温服",
                       "rationale": "测试", "cautions": []}],
           "therapy": [{"category": "针灸", "name": "平肝潜阳针刺", "source_type": "knowledge", "acupoints": ["太冲", "风池", "百会"],
                        "detail": {"location_basis": "常规取穴", "manipulation": "平补平泻", "stimulation": "中等", "duration": "留针20分钟",
                                   "frequency": "隔日1次", "course": "10次为一疗程"},
                        "indications": "肝阳上亢型头痛", "contraindications": "凝血功能障碍、局部感染", "needs_confirmation": ["是否有出血倾向"],
                        "rationale": "测试"}],
           "lifestyle": ["低盐饮食", "规律作息"], "monitoring": ["每日早晚测量血压"], "followup_advice": "1周后复诊",
           "safety_notes": [], "missing_info": []}
    return out


def _plan_style(user: str) -> dict:
    """Deterministic 4-column answer for the fake model. Magic markers let tests trigger the validation paths."""
    refs = re.findall(r"^(S\d+) \|", user, re.M)
    facts = re.findall(r"^(F\d+) \|", user, re.M)
    f1 = facts[0] if facts else "F1"
    app = [{"ref": r, "verdict": "match" if i == 0 else "partial", "reason": "测试：患者当前症状与该经验适用条件相符", "fact_refs": [f1, "F99"]} for i, r in enumerate(refs)]
    cmp_ = [{"ref": r, "item": "治法", "stance": "adopted", "note": "测试：沿用平肝潜阳思路"} for r in refs]
    if "FAKE_STYLE_GHOST_REF" in user:
        app.append({"ref": "S99", "verdict": "match", "reason": "伪造的编号", "fact_refs": []})
    if "FAKE_STYLE_WRONG_DOSE" in user and refs:
        cmp_.append({"ref": refs[0], "item": "剂量", "stance": "adopted", "note": "该医师习惯天麻90g"})          # 与条目记录不符
    return {"summary": "测试：参考了所选经验，沿用其治法并按本患者调整", "applicability": app, "comparison": cmp_}


def _flag(name: str) -> bool:
    """Test switch: while the file `<data_dir>/fake_flags/<name>` exists the fake service is "down" — lets an end-to-end test fail a
    service, then HEAL it and prove that "retry" recovers (marker bytes in a payload can only ever fail deterministically)."""
    from server.config import get_settings
    return (get_settings().data_dir / "fake_flags" / name).exists()


def ocr(image_bytes: bytes) -> tuple[str, str, int]:
    if _flag("ocr_fail") or b"FAKE_OCR_FAIL" in image_bytes[:200000]:
        raise ModelError("ocr_unreachable", "OCR：（测试）服务不可用")
    text = "项目 结果 单位 参考范围\nALT 丙氨酸氨基转移酶 19.0 U/L 0-40\nTC 总胆固醇 6.20 mmol/L 0-5.17 ↑\nGLU 葡萄糖 4.88 mmol/L 3.89-6.10\nHDL 高密度脂蛋白 1.40 mmol/L 1.04-1.66"
    return text, "fake-ocr", 3


def asr(audio_bytes: bytes) -> tuple[str, dict, int]:
    if _flag("asr_fail") or b"FAKE_ASR_FAIL" in audio_bytes[:200000]:
        raise ModelError("asr_model_unavailable", "语音识别：（测试）服务不可用")
    if b"FAKE_ASR_SILENT" in audio_bytes[:200000]:
        raise ModelError("asr_no_speech", "未识别到语音内容", retryable=False)
    return "我头痛三天了，还有点头晕。", {"duration_s": 3.0}, 4
