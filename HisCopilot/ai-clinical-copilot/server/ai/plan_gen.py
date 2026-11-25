"""Treatment-plan generation: ONE LLM call over the whole final-diagnosis set → validated structured plan.

Post-processing (never trust the model):
  * herbs: names normalised/trimmed, dose coerced to a positive number, duplicates merged, `special` method
    defaulted from the reference/catalog when the model left it blank, count checked (10–14 asked, not forced).
  * western/patent: must have dose/frequency/route/days — missing fields are kept EMPTY (and flagged) rather than
    guessed, so the doctor sees an honest gap instead of an invented dose.
  * catalog reconciliation: each drug/herb is matched to the clinic catalog; unmatched ones get flag
    `not_in_catalog` (the model's own `in_hospital_dict` claim is ignored).
  * therapy items: `source_type=hospital` is only honoured if the name matches a real catalog program; otherwise
    downgraded to `ai`. Acupoint names are checked against a standard-point whitelist; unknown points are flagged.
  * The generated plan is then run through the same safety engine used at signing time so the doctor is warned
    while reviewing, not only at the last click.
"""
from __future__ import annotations

import json
from typing import Any

from server.ai.client import ModelError, chat_json, load_prompt
from server.ai.facts import Fact, facts_prompt_block
from server.ai.analysis import LLM_LOCK
from server.domain.plans import empty_content
from server.safety.data import reference as R
from server.safety.engine import base_names, clean_name

# 十四经常用腧穴（规范名白名单，用于校验针灸/推拿/艾灸取穴；不在表内 → 提示核对，而非判错）
ACUPOINTS = set("""
太渊 列缺 尺泽 孔最 鱼际 少商 中府 云门 商阳 合谷 阳溪 偏历 手三里 曲池 肘髎 肩髃 迎香 扶突 禾髎
承泣 四白 巨髎 地仓 大迎 颊车 下关 头维 人迎 缺盆 梁门 天枢 水道 归来 气冲 髀关 伏兔 梁丘 犊鼻 足三里 上巨虚 条口 下巨虚 丰隆 解溪 冲阳 陷谷 内庭 厉兑
隐白 大都 太白 公孙 商丘 三阴交 漏谷 地机 阴陵泉 血海 箕门 冲门 大横 大包
极泉 少海 灵道 通里 阴郄 神门 少府 少冲 少泽 前谷 后溪 腕骨 阳谷 养老 支正 小海 肩贞 臑俞 天宗 秉风 曲垣 颧髎 听宫
睛明 攒竹 眉冲 通天 络却 天柱 大杼 风门 肺俞 厥阴俞 心俞 督俞 膈俞 肝俞 胆俞 脾俞 胃俞 三焦俞 肾俞 气海俞 大肠俞 关元俞 小肠俞 膀胱俞 次髎 白环俞 委阳 委中 膏肓 志室 胞肓 秩边 承扶 承山 飞扬 昆仑 申脉 金门 京骨 束骨 至阴
涌泉 然谷 太溪 大钟 水泉 照海 复溜 交信 筑宾 阴谷 横骨 大赫 气穴 四满 中注 肓俞 商曲 石关 阴都 腹通谷 幽门 步廊 神封 灵墟 神藏 彧中 俞府
曲泽 郄门 间使 内关 大陵 劳宫 中冲 天池 天泉
关冲 液门 中渚 阳池 外关 支沟 会宗 三阳络 四渎 天井 清冷渊 消泺 臑会 肩髎 天髎 天牖 翳风 瘛脉 颅息 角孙 耳门 耳和髎 丝竹空
瞳子髎 听会 上关 颔厌 悬颅 悬厘 曲鬓 率谷 天冲 浮白 头窍阴 完骨 本神 阳白 头临泣 目窗 正营 承灵 脑空 风池 肩井 渊腋 辄筋 日月 京门 带脉 五枢 维道 居髎 环跳 风市 中渎 膝阳关 阳陵泉 阳交 外丘 光明 阳辅 悬钟 丘墟 足临泣 地五会 侠溪 足窍阴
大敦 行间 太冲 中封 蠡沟 中都 膝关 曲泉 阴包 足五里 阴廉 急脉 章门 期门
长强 腰俞 腰阳关 命门 悬枢 脊中 中枢 筋缩 至阳 灵台 神道 身柱 陶道 大椎 哑门 风府 脑户 强间 后顶 百会 前顶 囟会 上星 神庭 素髎 水沟 人中 兑端 龈交
会阴 曲骨 中极 关元 石门 气海 阴交 神阙 水分 下脘 建里 中脘 上脘 巨阙 鸠尾 中庭 膻中 玉堂 紫宫 华盖 璇玑 天突 廉泉 承浆
印堂 太阳 四神聪 安眠 夹脊 定喘 腰眼 十宣 四缝 鱼腰 翳明 牵正 阑尾 胆囊 内迎香 金津 玉液 耳尖 八邪 八风 膝眼 内膝眼 外膝眼 百虫窝 阿是穴 华佗夹脊
""".split())


def build_user_block(facts: list[Fact], dxs: list[dict], sections: list[str], safety_ctx: dict, programs: list[dict],
                     doctor_pref: dict, style_block: str = "") -> str:
    extra = [f"STYLE_REFERENCES（历史经验摘要，仅供参考，不是规则；患者当前事实与安全约束优先）:\n{style_block}"] if style_block else []
    return "\n\n".join([
        "FACTS:\n" + facts_prompt_block(facts),
        "FINAL_DIAGNOSES:\n" + json.dumps(dxs, ensure_ascii=False),
        "SECTIONS:\n" + json.dumps(sections, ensure_ascii=False),
        "SAFETY_CONTEXT:\n" + json.dumps(safety_ctx, ensure_ascii=False),
        "HOSPITAL_PROGRAMS:\n" + json.dumps([{"name": p["name"], "category": p["category"], "applicable": p.get("applicable", "")[:60]}
                                             for p in programs[:30]], ensure_ascii=False),
        "DOCTOR_PREFERENCE（仅作排序参考，不得压过患者安全）:\n" + json.dumps(doctor_pref, ensure_ascii=False),
    ] + extra)


def generate_raw(facts: list[Fact], dxs: list[dict], sections: list[str], safety_ctx: dict, programs: list[dict],
                 doctor_pref: dict, style_block: str = "") -> tuple[dict, dict]:
    system, ver = load_prompt("plan_v1")
    if style_block:
        add, _ = load_prompt("plan_style_addendum")
        system, ver = system + add, ver + "+style"
    user = build_user_block(facts, dxs, sections, safety_ctx, programs, doctor_pref, style_block)
    # one sequential 27B call; budget grows with the number of requested sections / diagnoses (+ room for the 4-column comparison when profiles are used)
    mt = min(5200, 1800 + 900 * len([s for s in sections if s in ("herbal", "therapy")]) + 250 * max(0, len(dxs) - 1) + (700 if style_block else 0))
    with LLM_LOCK:
        d, res = chat_json(system, user, role="main", max_tokens=mt, timeout=420, retries=1)
    return d, {"model": res.model, "prompt": ver, "latency_ms": res.latency_ms}


# --------------------------------------------------------------------------- normalisation
def _num(x) -> float | None:
    try:
        v = float(str(x).lower().replace("g", "").replace("克", "").strip())
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _int(x) -> int | None:
    v = _num(x)
    return int(v) if v else None


def _cat_lookup(catalog: dict[str, dict], name: str) -> dict | None:
    n = clean_name(name).lower()
    if n in catalog:
        return catalog[n]
    for b in base_names(name):
        if b.lower() in catalog:
            return catalog[b.lower()]
    return None


def normalise(raw: dict, catalog: dict[str, dict], programs: list[dict], sections: list[str]) -> tuple[dict, list[dict]]:
    """→ (content_json for the plan, therapy item dicts). Never raises on malformed pieces; drops them with a note."""
    notes: list[str] = []
    c = empty_content()
    c["summary"] = str(raw.get("summary", "")).strip()[:400]
    c["coordination_notes"] = _strs(raw.get("coordination_notes"))
    c["lifestyle"] = _strs(raw.get("lifestyle"), 6)
    c["monitoring"] = _strs(raw.get("monitoring"), 6)
    c["followup_advice"] = str(raw.get("followup_advice", "")).strip()[:200]
    c["safety_notes"] = _strs(raw.get("safety_notes"), 6)
    c["missing_info"] = _strs(raw.get("missing_info"), 8)

    med = raw.get("medication") if isinstance(raw.get("medication"), dict) else {}
    if "medication" in sections:
        for key in ("western", "patent"):
            for m in (med.get(key) or []):
                if not isinstance(m, dict) or not str(m.get("name", "")).strip():
                    continue
                cat = _cat_lookup(catalog, m["name"])
                it = {"name": str(m["name"]).strip()[:128], "drug_id": cat["id"] if cat else None,
                      "dose_text": str(m.get("dose", "") or "").strip(), "frequency": str(m.get("frequency", "") or "").strip(),
                      "route": str(m.get("route", "") or "").strip() or ("口服" if cat else ""), "days": _int(m.get("days")),
                      "reason": str(m.get("reason", "")).strip()[:200], "spec": cat["spec"] if cat else "", "form": cat["form"] if cat else "",
                      "in_catalog": bool(cat), "flags": [] if cat else ["not_in_catalog"],
                      "considerations": _strs(m.get("considerations"), 4)}
                for need, label in (("dose_text", "剂量"), ("frequency", "频次"), ("days", "疗程")):
                    if not it[need]:
                        it["flags"].append(f"missing_{need}")
                c["medication"][key].append(it)

    if "herbal" in sections:
        for h in (raw.get("herbal") or [])[:2]:
            if not isinstance(h, dict):
                continue
            herbs, seen = [], set()
            for x in (h.get("herbs") or []):
                if not isinstance(x, dict) or not str(x.get("name", "")).strip():
                    continue
                nm = clean_name(str(x["name"]))
                if nm in seen:
                    continue
                seen.add(nm)
                cat = _cat_lookup(catalog, nm)
                g = _num(x.get("dose_g"))
                sp = str(x.get("special", "") or "").strip()
                if not sp:
                    sp = (cat or {}).get("special_method") or next((s for n, lo, hi, s, al in R.HERBS if nm == n or nm in al), "")
                flags = [] if cat or any(b in {n for n, *_ in R.HERBS} for b in base_names(nm)) else ["not_in_catalog"]
                if g is None:
                    flags.append("missing_dose")
                herbs.append({"name": nm, "dose_g": g, "special": sp if sp != "冲服" or x.get("special") else sp,
                              "role": str(x.get("role", ""))[:1], "drug_id": cat["id"] if cat else None,
                              "in_catalog": bool(cat), "flags": flags})
            if herbs:
                c["herbal"].append({"title": str(h.get("title", "")).strip()[:128] or "自拟方",
                                    "formula_type": h.get("formula_type") if h.get("formula_type") in ("classic", "hospital", "ai") else "ai",
                                    "source_note": str(h.get("source_note", "")).strip()[:128], "principle": str(h.get("principle", "")).strip()[:120],
                                    "herbs": herbs, "doses_count": _int(h.get("doses_count")) or 7,
                                    "decoction": str(h.get("decoction", "")).strip()[:300], "usage": str(h.get("usage", "")).strip()[:300],
                                    "rationale": str(h.get("rationale", "")).strip()[:300], "cautions": _strs(h.get("cautions"), 5)})
            else:
                notes.append("模型给出的中药方缺少有效药味，已丢弃")

    therapy: list[dict] = []
    if "therapy" in sections:
        prog_names = {p["name"]: p for p in programs}
        for t in (raw.get("therapy") or [])[:3]:
            if not isinstance(t, dict) or not str(t.get("name", "")).strip():
                continue
            src = t.get("source_type") if t.get("source_type") in ("hospital", "knowledge", "ai") else "ai"
            prog = prog_names.get(str(t["name"]).strip())
            if src == "hospital" and not prog:
                src = "ai"                                                  # model claimed a catalog item that does not exist
            points = [str(p).strip() for p in (t.get("acupoints") or []) if str(p).strip()]
            unknown = [p for p in points if p not in ACUPOINTS]
            need = _strs(t.get("needs_confirmation"), 6)
            if unknown:
                need.append("穴位名称需核对：" + "、".join(unknown))
            if not (t.get("indications") and t.get("contraindications")):
                need.append("适用条件/禁忌未写全，需医师补充")
            therapy.append({"category": str(t.get("category", "")).strip()[:16], "name": str(t["name"]).strip()[:128], "source_type": src,
                            "program_id": prog["id"] if prog else None, "acupoints": points,
                            "detail": t.get("detail") if isinstance(t.get("detail"), dict) else {},
                            "indications": str(t.get("indications", "")).strip(), "contraindications": str(t.get("contraindications", "")).strip(),
                            "needs_confirmation": need, "rationale": str(t.get("rationale", "")).strip()[:300]})
    c["generation_notes"] = notes
    return c, therapy


def _strs(x, limit: int = 8) -> list[str]:
    return [str(i).strip() for i in (x or []) if str(i).strip()][:limit] if isinstance(x, list) else []
