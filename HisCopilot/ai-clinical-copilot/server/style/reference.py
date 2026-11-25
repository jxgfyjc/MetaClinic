"""方案生成 × 经验画像：把「已发布、已审核、允许 AI 使用」的经验条目，编成带编号的参考块 S1..Sn 交给模型，并校验模型的引用。

四栏结构（对应需求里的双层体系）
  ① 名医经验参考   —— 画像里的条目原文（逐字，来自已发布版本），含适用条件/例外/证据数/授权来源。**不经过模型改写**。
  ② 当前患者分析   —— 模型对“患者当前事实（F 编号）是否符合该经验适用条件”的判断：符合 / 部分符合 / 不符合 / 无法判断，并引用 F 与 S 编号。
  ③ 个性化建议     —— 模型给出的方案本体（现有 plan 内容）+ 与参考经验的异同（采纳/调整/未采纳 及理由）。
  ④ 医师最终决策   —— 医师逐条标注（采纳/不采纳）并在原有方案编辑/确认流程中落定；系统不替医师决定。

安全约束
  * 历史经验不是临床规则：参考块明确告诉模型“患者当前事实与安全约束优先”，并禁止原样照抄历史处方；
  * 命中严重安全提示且未经书面理由放行的条目，不会进入参考块；
  * 引用校验：模型引用的 S 编号必须存在；其对某条经验“药物/剂量”的声称，必须与该条目 body 中的数据一致，否则降级为“未核实”；
  * 最后仍走同一套签署前安全引擎——画像不会给任何药开绿灯。
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import StyleAuthorization, StyleEntry, StyleEntryEvidence, StyleProfile, StyleVersion, User
from server.domain.enums import StyleKind, StyleReview
from server.style import DISCLAIMER
from server.style import lexicon as L
from server.style import service as svc

MAX_ENTRIES_PER_PROFILE = 12
MAX_REFS = 24
PREFERRED = ("formula", "reasoning", "principle", "modification", "herb_pair", "preference", "outcome", "followup", "note")


def _label(p: StyleProfile, auth: StyleAuthorization | None) -> dict:
    if p.kind == StyleKind.PERSONAL.value:
        return {"layer": "personal", "name": f"{p.subject_name}（本人诊疗习惯）", "source_note": "接诊医师本人的历史诊疗习惯"}
    basis = {"self": "本人授权", "family": "家属授权", "institution": "单位授权", "publication": "公开出版物", "other": "其他依据"}.get(auth.basis_type if auth else "", "")
    note = f"{basis}：{auth.basis_text}（授权人：{auth.authorizer_name}）" if auth else "未备案授权"
    return {"layer": "master", "name": f"{p.subject_name}{('·' + p.subject_title) if p.subject_title else ''}", "source_note": note}


def gather(db: Session, user: User, profile_ids: list[str]) -> list[dict]:
    """→ [{ref_id:'S1', profile_id, profile_name, layer, source_note, version_id, version_no, snapshot_hash, entry_id, category, group_key, title, summary,
            conditions, exceptions, support, total, confidence, flags, body}]。只含：可用画像、已发布版本、已审核且 ai_eligible 的条目。"""
    refs: list[dict] = []
    n = 0
    for pid in profile_ids:
        p = db.get(StyleProfile, pid)
        if p is None or not svc.usable_by(db, user, p):
            continue
        v = db.get(StyleVersion, p.active_version_id)
        auth = svc.active_authorization(db, p) if p.kind == StyleKind.MASTER.value else None
        meta = _label(p, auth)
        rows = db.scalars(select(StyleEntry).where(StyleEntry.version_id == v.id, StyleEntry.review_status == StyleReview.APPROVED.value, StyleEntry.ai_eligible.is_(True))).all()
        rows.sort(key=lambda e: (PREFERRED.index(e.category) if e.category in PREFERRED else 99, -e.support_count, e.sort_order))
        for e in rows[:MAX_ENTRIES_PER_PROFILE]:
            if n >= MAX_REFS:
                break
            n += 1
            refs.append({"ref_id": f"S{n}", "profile_id": p.id, "profile_name": meta["name"], "layer": meta["layer"], "source_note": meta["source_note"], "version_id": v.id,
                         "version_no": v.version_no, "snapshot_hash": v.snapshot_hash, "entry_id": e.id, "category": e.category, "group_key": e.group_key, "title": e.title,
                         "summary": e.summary, "conditions": e.conditions, "exceptions": e.exceptions, "support": e.support_count, "total": e.group_total,
                         "confidence": e.confidence, "flags": [f for f in (e.flags_json or []) if f.get("severity") in ("warn", "critical")],
                         "body": {k: v2 for k, v2 in (e.body_json or {}).items() if not k.startswith("_")}})
    return refs


def prompt_block(refs: list[dict]) -> str:
    """交给模型的参考块。刻意不放入统计之外的 body 细节以外的任何“患者信息”；条目文字来自已脱敏的历史病例归纳。"""
    if not refs:
        return ""
    lines = []
    for r in refs:
        stat = f"{r['support']}/{r['total']}例" if r["total"] else "医师自述"
        cond = f"｜适用：{r['conditions']}" if r["conditions"] else ""
        exc = f"｜例外：{r['exceptions']}" if r["exceptions"] else ""
        flag = f"｜提示：{'；'.join(f['message'] for f in r['flags'])}" if r["flags"] else ""
        lines.append(f"{r['ref_id']} | {r['profile_name']} | {r['category']} | {r['title']} | {r['summary']}（{stat}，置信度{r['confidence']}）{cond}{exc}{flag}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- output validation
_VERDICTS = {"match": "符合", "partial": "部分符合", "mismatch": "不符合", "unknown": "无法判断"}
_STANCE = {"adopted": "采纳", "adjusted": "调整后采纳", "not_adopted": "未采纳", "info_only": "仅供参考"}
_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|克)")


def _body_herbs(body: dict) -> dict[str, set[float]]:
    """从条目 body 里取出它实际记录过的 药名 → 剂量集合（含中位/范围端点），用来核对模型对这条经验的转述。"""
    out: dict[str, set[float]] = {}

    def add(name: str, dose: dict | float | None) -> None:
        if not name:
            return
        k = L.canonical(name)
        s = out.setdefault(k, set())
        if isinstance(dose, dict):
            for key in ("median", "low", "high", "min", "max"):
                if isinstance(dose.get(key), (int, float)):
                    s.add(float(dose[key]))
        elif isinstance(dose, (int, float)):
            s.add(float(dose))

    for key in ("core", "optional", "herbs"):
        for h in body.get(key, []) or []:
            if isinstance(h, dict):
                add(h.get("name", ""), h.get("dose"))
    pair = body.get("pair")
    if pair and isinstance(body.get("dose"), dict):
        for nm in pair:
            add(nm, body["dose"].get(nm))
    return out


def _herb_claims(text: str) -> list[tuple[str, float]]:
    """文本里所有“已知药名 + 克数”（药名按词表最长匹配）。"""
    out: list[tuple[str, float]] = []
    i, n = 0, len(text)
    while i < n:
        nm = L.longest_herb_at(text, i)
        if nm:
            m = re.match(r"\s*[（(]?[^)）\d]{0,4}[)）]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:g|克|G)", text[i + len(nm):i + len(nm) + 16])
            if m:
                out.append((L.canonical(nm), float(m.group(1))))
                i += len(nm) + m.end()
                continue
            i += len(nm)
        else:
            i += 1
    return out


def validate(raw_style: dict | None, refs: list[dict], fact_ids: set[str]) -> tuple[dict, dict]:
    """→ (clean_style, stats)。丢弃不存在的 S/F 引用；核对模型转述的药/量与条目一致；保证每个被选画像都有一个展示块。"""
    by_id = {r["ref_id"]: r for r in refs}
    stats = {"dropped_s_refs": 0, "dropped_f_refs": 0, "unverified_claims": 0, "dropped_items": 0}
    raw = raw_style if isinstance(raw_style, dict) else {}

    def f_refs(x) -> list[str]:
        xs = [i for i in (x or []) if isinstance(i, str)] if isinstance(x, list) else []
        good = [i for i in xs if i in fact_ids]
        stats["dropped_f_refs"] += len(xs) - len(good)
        return good

    def s_refs(x) -> list[str]:
        xs = [i for i in (x or []) if isinstance(i, str)] if isinstance(x, list) else []
        good = [i for i in xs if i in by_id]
        stats["dropped_s_refs"] += len(xs) - len(good)
        return good

    applicability = []
    for a in (raw.get("applicability") or []):
        if not isinstance(a, dict):
            continue
        sr = s_refs([a.get("ref")] if isinstance(a.get("ref"), str) else a.get("refs"))
        if not sr:
            stats["dropped_items"] += 1
            continue
        verdict = a.get("verdict") if a.get("verdict") in _VERDICTS else "unknown"
        applicability.append({"refs": sr, "verdict": verdict, "verdict_label": _VERDICTS[verdict], "reason": str(a.get("reason", "")).strip()[:300], "fact_refs": f_refs(a.get("fact_refs"))})

    comparison = []
    for c in (raw.get("comparison") or []):
        if not isinstance(c, dict):
            continue
        sr = s_refs([c.get("ref")] if isinstance(c.get("ref"), str) else c.get("refs"))
        if not sr:
            stats["dropped_items"] += 1
            continue
        stance = c.get("stance") if c.get("stance") in _STANCE else "info_only"
        note = str(c.get("note", "")).strip()[:400]
        claims: dict[str, set[float]] = {}
        for r in sr:
            for k, v2 in _body_herbs(by_id[r]["body"]).items():
                claims.setdefault(k, set()).update(v2)
        # 转述里出现的每个“药名 + 克数”，都必须能在被引用条目里找到依据：条目没记录这味药，或记录的剂量对不上，一律标“未核实”。
        # （用词表按最长匹配取药名，而不是用正则猜，避免“习惯天麻90g”被当成药名。）
        unverified: list[str] = []
        for nm, dose in _herb_claims(note):
            have = claims.get(nm)
            if not have or not any(abs(dose - d) <= max(1.0, 0.1 * d) for d in have):
                unverified.append(f"{nm}{dose:g}g")
        verified = not unverified
        if not verified:
            stats["unverified_claims"] += 1
        comparison.append({"refs": sr, "stance": stance, "stance_label": _STANCE[stance], "note": note, "item": str(c.get("item", "")).strip()[:120],
                           "verified": verified, "unverified": unverified})

    summary = str(raw.get("summary", "")).strip()[:500]
    clean = {"summary": summary, "applicability": applicability, "comparison": comparison, "disclaimer": DISCLAIMER}
    return clean, stats


def snapshot(refs: list[dict], clean: dict, stats: dict, profile_ids: list[str], facts: dict[str, dict] | None = None) -> dict:
    """持久化到 treatment_plans.style_json 的快照：引用条目的当时原文 + 模型对照 + 医师采纳决定（初始为空）。"""
    profiles: dict[str, dict] = {}
    for r in refs:
        pr = profiles.setdefault(r["profile_id"], {"profile_id": r["profile_id"], "name": r["profile_name"], "layer": r["layer"], "source_note": r["source_note"],
                                                   "version_id": r["version_id"], "version_no": r["version_no"], "snapshot_hash": r["snapshot_hash"], "entries": []})
        pr["entries"].append({k: r[k] for k in ("ref_id", "entry_id", "category", "group_key", "title", "summary", "conditions", "exceptions", "support", "total", "confidence", "flags")})
    # 被引用的患者事实（F 编号）按“生成方案那一刻”的原文存入快照：事实表会随病史/检验变化而重新编号，快照必须自包含才能保证日后点开看到的是当时引用的那条。
    cited = {f for a in clean.get("applicability", []) for f in a.get("fact_refs", [])}
    keep = {k: v for k, v in (facts or {}).items() if k in cited}
    return {"requested": profile_ids, "profiles": list(profiles.values()), "refs": [r["ref_id"] for r in refs], **clean, "facts": keep, "validation": stats, "decisions": {},
            "disclaimer": DISCLAIMER}


# --------------------------------------------------------------------------- doctor decisions on the comparison
def apply_decision(style_json: dict, idx: int, decision: str, note: str = "") -> dict:
    if decision not in ("adopt", "skip", "clear"):
        raise ValueError("bad decision")
    d = dict(style_json.get("decisions") or {})
    if decision == "clear":
        d.pop(str(idx), None)
    else:
        d[str(idx)] = {"decision": decision, "note": note[:200]}
    return {**style_json, "decisions": d}
