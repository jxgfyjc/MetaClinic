"""画像归纳：已确认的病例 → 经验条目（带证据、置信度、风险标记）。纯函数，无 I/O、无模型调用。

可信度原则
  * 一切**数字**（病例数、频率、剂量区间、共现次数）由代码从病例里统计，不来自模型；
  * 一条经验至少要有 MIN_SUPPORT 例支持才生成（笔记/人工条目除外）；病例数不足的病证只记入 stats.below_threshold，不产出条目；
  * 置信度由 (支持例数, 占比) 计算，**不取模型自报**；
  * 每条经验附 evidence：病例 id + 原文逐字片段；没有证据的自动条目不会生成；
  * 先过安全规则（毒性/十八反/十九畏/超量/妊娠）：含严重风险的条目标 critical，默认不允许进入 AI 提示词，审核人需书面理由才能放开；
  * 过期/无日期/证据较少都会作为 flags 展示，由审核人判断——历史经验不是临床规则。
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations

from server.safety import engine as E
from server.safety.data import reference as R
from server.style import extract as X
from server.style import lexicon as L

MIN_SUPPORT = 3          # 一条自动经验至少需要的支持病例数
MIN_GROUP = 3            # 一个病证组至少需要的病例数，否则只记入 below_threshold
CORE_RATIO = 0.6         # 核心药：在该方/该组病例中出现比例
ALT_RATIO = 0.3          # 常加减药
UBIQUITOUS = 0.8         # 全库出现比例 ≥ 此值的药（如甘草）不参与药对
STALE_YEARS = 8
MAX_GROUPS = 40
MAX_PAIRS = 8
LOW_EVIDENCE = 5

CATEGORY_LABEL = {"reasoning": "辨证思路", "principle": "治疗原则", "formula": "常用方药", "herb_pair": "药物配伍", "modification": "加减规律",
                  "preference": "用药偏好", "outcome": "疗效反馈", "followup": "随访习惯", "note": "经验笔记"}


# --------------------------------------------------------------------------- helpers
def confidence(support: int, ratio: float) -> str:
    if support >= 8 and ratio >= 0.6:
        return "high"
    if support >= 4 and ratio >= 0.4:
        return "medium"
    return "low"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def dose_summary(doses: list[float]) -> dict:
    d = sorted(doses)
    n = len(d)
    if not n:
        return {}
    if n >= 5:
        lo, hi = d[int(round((n - 1) * 0.25))], d[int(round((n - 1) * 0.75))]
    else:
        lo, hi = d[0], d[-1]
    return {"median": round(statistics.median(d), 1), "low": round(lo, 1), "high": round(hi, 1), "min": round(d[0], 1), "max": round(d[-1], 1), "n": n}


def find_quote(text: str, needle: str, pad: int = 6, limit: int = 140) -> str:
    """Return a VERBATIM fragment of `text` containing `needle` (punctuation/whitespace-insensitive). '' if not found."""
    if not needle or not text:
        return ""
    i = text.find(needle)
    if i >= 0:
        a, b = max(0, i - pad), min(len(text), i + len(needle) + pad)
        return text[a:b][:limit]
    chars = [c for c in needle if not re.match(r"[\s，,。；;：:、（）()\[\]【】\"'“”‘’·\-—]", c)]
    if len(chars) < 2:
        return ""
    rx = re.compile(r"[\s，,。；;：:、（）()\[\]【】]*".join(re.escape(c) for c in chars))
    m = rx.search(text)
    if not m:
        return ""
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return text[a:b][:limit]


def _herb_quote(text: str, h: dict) -> str:
    """The herb exactly as written, from the name through its dose and any special-method bracket — ends on a token boundary, never mid-word."""
    asw = h.get("as_written") or h["name"]
    m = re.search(re.escape(asw) + r"\s*(?:[（(][^)）]{0,6}[)）])?\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:g|克|G)(?:\s*[（(][^)）]{0,6}[)）])?", text)
    return m.group(0)[:60] if m else find_quote(text, asw, pad=0)


def _age_flags(visit_dates: list[str], today: date) -> list[dict]:
    ds = sorted(d for d in visit_dates if re.match(r"^\d{4}-\d{2}$", d or ""))
    out: list[dict] = []
    if len(ds) < max(1, len(visit_dates) // 2):
        out.append({"code": "undated", "severity": "info", "message": "多数支持病例没有就诊日期，无法判断经验是否过时"})
        return out
    newest = ds[-1]
    yrs = (today.year - int(newest[:4])) + (today.month - int(newest[5:])) / 12
    if yrs >= STALE_YEARS:
        out.append({"code": "stale", "severity": "warn", "message": f"支持病例最近的就诊在 {newest}（约 {int(yrs)} 年前），诊疗理念/药材/规范可能已变化，请审核人判断是否仍适用"})
    return out


_SEV = {"critical": "critical", "block": "critical", "warning": "warn", "info": "info"}


def safety_flags(herbs: list[dict]) -> list[dict]:
    """herbs: [{name, dose_g}]。用签署时同一套规则筛**历史经验**；命中只代表"需要人审"，不代表这位医师用错了药。"""
    if not herbs:
        return []
    items = [E.Item(index=i, name=h["name"], kind="herb", dose_g=h.get("dose_g")) for i, h in enumerate(herbs)]
    flags: list[dict] = []
    for iss in E.screen_herbs(items):
        if iss.rule_id in ("herb.special_method", "herb.dose_below"):
            continue
        code = "safety." + iss.rule_id.split(".", 1)[1]
        flags.append({"code": code, "severity": _SEV.get(iss.severity, "info"), "message": iss.message})
    names = {L.canonical(h["name"]) for h in herbs} | {h["name"] for h in herbs}
    forb = sorted(n for n in names if n in R.PREGNANCY_FORBIDDEN)
    caut = sorted(n for n in names if n in R.PREGNANCY_CAUTION)
    if forb:
        flags.append({"code": "safety.pregnancy_forbidden", "severity": "critical", "message": f"含妊娠禁忌药：{'、'.join(forb)}（孕妇不得使用）"})
    if caut:
        flags.append({"code": "safety.pregnancy_caution", "severity": "warn", "message": f"含妊娠慎用药：{'、'.join(caut)}"})
    seen, out = set(), []
    for f in flags:
        k = (f["code"], f["message"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _pair_flags(a: str, b: str, ds: dict) -> list[dict]:
    """药对条目的风险：配对本身的配伍禁忌（十八反/十九畏）+ 单药的严重/超量风险；单药的"毒性/妊娠慎用"提示合并为一条，避免在每个药对里重复刷屏。"""
    items = [{"name": a, "dose_g": ds[a].get("median")}, {"name": b, "dose_g": ds[b].get("median")}]
    fl = safety_flags(items)
    pair_codes = ("safety.eighteen_antagonism", "safety.nineteen_fear")
    keep = [f for f in fl if f["code"] in pair_codes or f["severity"] == "critical"]
    soft = sorted({f["code"].split(".", 1)[1] for f in fl if f not in keep})
    if soft:
        keep.append({"code": "safety.single_herb_notes", "severity": "info", "message": "所含单味药另有提示（毒性/妊娠慎用/超量等），见“常用方药”条目与安全规则"})
    return keep


def _entry(category: str, group: str, key: str, title: str, body: dict, summary: str, support: int, total: int, evidence: list[dict],
           flags: list[dict], conditions: str = "", exceptions: str = "", conf: str | None = None, origin: str = "auto") -> dict:
    ratio = support / total if total else 0
    fl = list(flags)
    if origin == "auto" and support < LOW_EVIDENCE:
        fl.append({"code": "insufficient", "severity": "info", "message": f"证据较少（{support} 例支持），仅作线索，不宜当作该医师的稳定习惯"})
    return {"category": category, "group_key": group, "key": key, "title": title, "body": body, "summary": summary, "support": support, "total": total,
            "confidence": conf or confidence(support, ratio), "evidence": evidence, "flags": fl, "conditions": conditions, "exceptions": exceptions, "origin": origin}


def entry_match_key(e: dict) -> str:
    return f"{e['category']}|{e['group_key']}|{e['key']}"


# --------------------------------------------------------------------------- main
def build(cases: list[dict], *, today: date | None = None) -> tuple[list[dict], dict]:
    """cases: [{id, kind, text, group_key, fields, herbs, visit_date}]，仅包含**已确认**病例。→ (entries, stats)。"""
    today = today or date.today()
    real = [c for c in cases if c.get("kind", "case") == "case"]
    notes = [c for c in cases if c.get("kind") == "note"]
    entries: list[dict] = []

    # ---- 全库统计（用于药对"普遍药"过滤、用药偏好、随访习惯）
    corpus_n = len([c for c in real if c["herbs"]])
    herb_cases: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for c in real:
        for h in c["herbs"]:
            herb_cases[h["name"]].append((c, h))
    ubiq = {n for n, v in herb_cases.items() if corpus_n >= MIN_SUPPORT and len(v) / corpus_n >= UBIQUITOUS}

    groups: dict[str, list[dict]] = defaultdict(list)
    ungrouped = 0
    for c in real:
        g = c.get("group_key") or ""
        if g:
            groups[g].append(c)
        else:
            ungrouped += 1
    # Fallback: cases whose syndrome group has < MIN_GROUP (or no group_key) fall back to document section
    small_groups = {g for g, cs in groups.items() if len(cs) < MIN_GROUP}
    fallback_n = 0
    if small_groups or ungrouped:
        new_groups: dict[str, list[dict]] = defaultdict(list)
        ungrouped = 0
        for c in real:
            g = c.get("group_key") or ""
            sec = (c.get("section") or "").strip()
            if (not g or g in small_groups) and sec:
                cc = dict(c)
                cc["group_key"] = sec
                new_groups[sec].append(cc)
                fallback_n += 1
            elif g:
                new_groups[g].append(c)
            else:
                ungrouped += 1
        groups = new_groups
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    below = [{"group": g, "n": len(cs)} for g, cs in ranked if len(cs) < MIN_GROUP]
    used = [(g, cs) for g, cs in ranked if len(cs) >= MIN_GROUP][:MAX_GROUPS]

    for g, cs in used:
        n = len(cs)
        dates = [c.get("visit_date", "") for c in cs]
        age = _age_flags(dates, today)

        # 1) 辨证思路：常见症状/舌/脉（≥2 例且 ≥20%）
        sym, tg, pl, dis = Counter(), Counter(), Counter(), Counter()
        who: dict[tuple[str, str], list[str]] = defaultdict(list)
        for c in cs:
            f = c["fields"]
            for s in f.get("symptoms", []):
                sym[s] += 1
                who[("s", s)].append(c["id"])
            if f.get("tongue"):
                tg[f["tongue"]] += 1
                who[("t", f["tongue"])].append(c["id"])
            if f.get("pulse"):
                pl[f["pulse"]] += 1
                who[("p", f["pulse"])].append(c["id"])
            if f.get("disease"):
                dis[f["disease"]] += 1
        thr = max(2, round(n * 0.2))
        top = lambda cnt: [(k, v) for k, v in cnt.most_common(6) if v >= thr]
        if top(sym) or top(tg) or top(pl):
            body = {"syndrome": g, "n": n, "symptoms": [{"text": k, "n": v} for k, v in top(sym)], "tongue": [{"text": k, "n": v} for k, v in top(tg)],
                    "pulse": [{"text": k, "n": v} for k, v in top(pl)], "diseases": [{"text": k, "n": v} for k, v in dis.most_common(3)]}
            ev = []
            for c in cs[:30]:
                q = find_quote(c["text"], c["fields"].get("syndrome", "")) or find_quote(c["text"], c["fields"].get("tongue", ""))
                if q:
                    ev.append({"case_id": c["id"], "kind": "support", "quote": q, "detail": {}})
            parts = []
            if body["symptoms"]:
                parts.append("常见表现：" + "、".join(f"{x['text']}（{x['n']}例）" for x in body["symptoms"][:4]))
            if body["tongue"]:
                parts.append("舌象多为 " + "、".join(f"{x['text']}（{x['n']}例）" for x in body["tongue"][:2]))
            if body["pulse"]:
                parts.append("脉象多为 " + "、".join(f"{x['text']}（{x['n']}例）" for x in body["pulse"][:2]))
            if ev:
                entries.append(_entry("reasoning", g, "reasoning", f"{g}：辨证要点", body, f"在“{g}”类病例（{n} 例）中，" + "；".join(parts) + "。", n, n, ev, age,
                                      conditions=f"病例被该医师辨为“{g}”时的共同特征（统计自 {n} 例）"))

        # 2) 治疗原则
        pr = Counter()
        pr_who: dict[str, list[tuple[dict, str]]] = defaultdict(list)
        for c in cs:
            raw_pr = c["fields"].get("principle", "")
            seen_p: set[str] = set()
            for part in re.split(r"[，,、；;]", raw_pr):
                p = X._norm(part)
                if len(p) >= 2 and p not in seen_p:
                    seen_p.add(p)
                    pr[p] += 1
                    pr_who[p].append((c, part.strip()))
        for p, v in pr.most_common(3):
            if v >= 2:
                ev = [{"case_id": c["id"], "kind": "support", "quote": find_quote(c["text"], raw_p), "detail": {}} for c, raw_p in pr_who[p][:30]]
                ev = [e for e in ev if e["quote"]]
                if ev:
                    entries.append(_entry("principle", g, p, f"{g}：治以{p}", {"principle": p, "n": v, "of": n}, f"“{g}”类病例中，{v}/{n} 例（{_pct(v / n)}）明确的治法为“{p}”。", v, n, ev, age))

        # 3) 常用方药：按方名分桶，其余合并为"药物组合归纳"
        by_formula: dict[str, list[dict]] = defaultdict(list)
        for c in cs:
            fm = X.norm_formula(c["fields"].get("formula", ""))
            if fm and len(fm) >= 2:
                by_formula[fm].append(c)
        rest = [c for c in cs]
        for fm, fcs in sorted(by_formula.items(), key=lambda kv: -len(kv[1])):
            if len(fcs) < MIN_SUPPORT:
                continue
            entries.append(_formula_entry(g, fm, fcs, n, age))
            ids = {c["id"] for c in fcs}
            rest = [c for c in rest if c["id"] not in ids]
        rest = [c for c in rest if c["herbs"]]
        if len(rest) >= MIN_SUPPORT:
            entries.append(_formula_entry(g, "", rest, n, age))

        # 4) 药对：只保留"共现明显高于各自独立出现概率"的配伍（lift）。组内几乎每例都用的核心药两两配对没有信息量，交给"常用方药"条目展示
        herb_n: Counter = Counter()
        for c in cs:
            for nm in {h["name"] for h in c["herbs"] if h["name"] not in ubiq}:
                herb_n[nm] += 1
        pair_n: Counter = Counter()
        pair_cases: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for c in cs:
            names = sorted({h["name"] for h in c["herbs"] if h["name"] not in ubiq})
            for a, b in combinations(names, 2):
                pair_n[(a, b)] += 1
                pair_cases[(a, b)].append(c)
        cand = []
        for (a, b), v in pair_n.items():
            if v < MIN_SUPPORT or v / n < 0.4:
                continue
            fa, fb = herb_n[a] / n, herb_n[b] / n
            if fa >= 0.85 and fb >= 0.85:                      # 双方都是该组的"必备药"，配对只是重复了核心药信息
                continue
            lift = (v / n) / (fa * fb)
            if lift < 1.15:
                continue
            cand.append((lift * v, a, b, v, lift))
        cand.sort(reverse=True)
        for _score, a, b, v, lift in cand[:MAX_PAIRS]:
            doses = {a: [], b: []}
            ev = []
            for c in pair_cases[(a, b)][:30]:
                ha = next(h for h in c["herbs"] if h["name"] == a)
                hb = next(h for h in c["herbs"] if h["name"] == b)
                doses[a].append(ha["dose_g"])
                doses[b].append(hb["dose_g"])
                ev.append({"case_id": c["id"], "kind": "support", "quote": _herb_quote(c["text"], ha),
                           "detail": {"herbs": {a: ha["dose_g"], b: hb["dose_g"]}, "quote2": _herb_quote(c["text"], hb)}})
            ds = {a: dose_summary(doses[a]), b: dose_summary(doses[b])}
            fl = _pair_flags(a, b, ds) + age
            entries.append(_entry("herb_pair", g, f"{a}+{b}", f"{a} + {b}", {"pair": [a, b], "co_n": v, "of": n, "lift": round(lift, 2), "dose": ds},
                                  f"“{g}”类病例中，{v}/{n} 例（{_pct(v / n)}）同时使用 {a}（常用 {ds[a]['median']:g}g）与 {b}（常用 {ds[b]['median']:g}g）；"
                                  f"同时使用的比例约为二者独立出现时预期的 {lift:.1f} 倍，提示这是有意识的配伍而非巧合。", v, n, ev, fl))

        # 5) 加减规律（≥2 例同一说法）
        mod_c: Counter = Counter()
        mod_w: dict[str, list[dict]] = defaultdict(list)
        for c in cs:
            for m in c["fields"].get("modifications", []):
                k = X._norm(m)
                if k:
                    mod_c[k] += 1
                    mod_w[k].append((c, m))
        for k, v in mod_c.most_common(6):
            if v < 2:
                continue
            raw = mod_w[k][0][1]
            ev = [{"case_id": c["id"], "kind": "support", "quote": find_quote(c["text"], m), "detail": {}} for c, m in mod_w[k][:30]]
            ev = [e for e in ev if e["quote"]]
            if not ev:
                continue
            hs = [{"name": h} for h in {L.canonical(x) for x in re.findall("|".join(sorted(L.HERBS, key=len, reverse=True)[:500]), raw)}]
            fl = safety_flags([{"name": h["name"], "dose_g": None} for h in hs if re.search(r"加", raw)]) + age
            entries.append(_entry("modification", g, k, f"{g}：{raw}", {"text": raw, "n": v, "of": n}, f"“{g}”类病例中有 {v} 例出现加减：{raw}。", v, n, ev, fl,
                                  conditions="随兼症加减，需有对应症状才适用"))

        # 6) 疗效反馈（病历自述）
        with_out = [(c, c["fields"]["outcome"]) for c in cs if c["fields"].get("outcome")]
        if len(with_out) >= MIN_SUPPORT:
            imp = [x for x in with_out if re.search(r"好转|缓解|减轻|消失|改善|痊愈|稳定|显效|有效", x[1]) and not re.search(r"未见|无效|不明显|加重", x[1])]
            wor = [x for x in with_out if re.search(r"加重|无效|未见好转|不明显", x[1])]
            ev = [{"case_id": c["id"], "kind": "support" if (c, o) in imp else "counter", "quote": find_quote(c["text"], o), "detail": {}} for c, o in with_out[:30]]
            ev = [e for e in ev if e["quote"]]
            if ev:
                entries.append(_entry("outcome", g, "outcome", f"{g}：疗效反馈", {"with_outcome": len(with_out), "improved": len(imp), "worse_or_none": len(wor), "of": n},
                                      f"“{g}”类病例中 {len(with_out)} 例记录了复诊/疗效，其中 {len(imp)} 例描述为好转/缓解，{len(wor)} 例描述为无效或加重。",
                                      len(imp), len(with_out), ev, age + [{"code": "outcome_self_reported", "severity": "info", "message": "疗效来自病历文字自述，不是经随访验证的结局"}],
                                      exceptions=("；".join(f"{len(wor)} 例无效/加重" for _ in [0] if wor))))

    # ---- 全库：用药偏好（剂量倾向）
    pref = []
    for name, lst in herb_cases.items():
        if len(lst) >= MIN_SUPPORT:
            pref.append((name, lst))
    pref.sort(key=lambda kv: -len(kv[1]))
    if pref:
        top = pref[:15]
        body_herbs, ev, fl_h = [], [], []
        seen_case: set[str] = set()
        for name, lst in top:
            ds = dose_summary([h["dose_g"] for _c, h in lst])
            body_herbs.append({"name": name, "n": len(lst), "dose": ds})
            fl_h.append({"name": name, "dose_g": ds["median"]})
            for c, h in lst[:2]:
                if c["id"] not in seen_case and len(ev) < 30:
                    seen_case.add(c["id"])
                    ev.append({"case_id": c["id"], "kind": "support", "quote": _herb_quote(c["text"], h), "detail": {"herb": name}})
        summ = "；".join(f"{x['name']}（{x['n']}例，常用{x['dose']['median']:g}g，范围{x['dose']['low']:g}–{x['dose']['high']:g}g）" for x in body_herbs[:8])
        entries.append(_entry("preference", "", "dose-tendency", "常用药物与剂量倾向", {"herbs": body_herbs, "corpus": corpus_n},
                              f"在 {corpus_n} 份含处方的病例中最常用的药物及其剂量习惯：{summ}。", len(top[0][1]), corpus_n, ev,
                              safety_flags(fl_h) + _age_flags([c.get("visit_date", "") for c in real], today)))

    # ---- 全库：复诊/随访习惯
    fu = Counter()
    fu_w: dict[str, list[dict]] = defaultdict(list)
    for c in real:
        for m in re.finditer(r"(\d{1,2})\s*(天|日|周|个月|月)\s*(?:后|内)?\s*(?:复诊|复查|随访|再诊)", c["text"]):
            k = f"{m.group(1)}{'周' if m.group(2) == '周' else ('个月' if m.group(2) in ('月', '个月') else '天')}"
            fu[k] += 1
            fu_w[k].append((c, m.group(0)))
    if fu and fu.most_common(1)[0][1] >= MIN_SUPPORT:
        k, v = fu.most_common(1)[0]
        ev = [{"case_id": c["id"], "kind": "support", "quote": find_quote(c["text"], frag), "detail": {}} for c, frag in fu_w[k][:30]]
        ev = [e for e in ev if e["quote"]]
        if ev:
            entries.append(_entry("followup", "", "revisit", "复诊间隔习惯", {"interval": k, "n": v, "of": len(real), "distribution": [{"text": a, "n": b} for a, b in fu.most_common(4)]},
                                  f"{len(real)} 份病例中 {sum(fu.values())} 份写明复诊/复查间隔，最常见为“{k}后复诊”（{v} 例）。", v, len(real), ev, []))

    # ---- 经验笔记（医师自述，非病例归纳）
    for c in notes:
        txt = c["text"].strip()
        title = re.sub(r"\s+", "", txt)[:24] + ("…" if len(txt) > 24 else "")
        entries.append(_entry("note", "", c["id"], title, {"text": txt[:1500]}, txt[:300] + ("…" if len(txt) > 300 else ""), 1, 1,
                              [{"case_id": c["id"], "kind": "support", "quote": txt[:120], "detail": {}}],
                              [{"code": "self_reported", "severity": "info", "message": "医师自述/笔记，不是从病例统计归纳而来"}], conf="low", origin="note"))

    for i, e in enumerate(entries):
        e["sort_order"] = i
    dates = sorted(d for d in (c.get("visit_date", "") for c in real) if d)
    stats = {"cases": len(real), "notes": len(notes), "with_prescription": corpus_n, "ungrouped": ungrouped, "section_fallback": fallback_n, "groups": [{"group": g, "n": len(cs)} for g, cs in used],
             "below_threshold": below, "entries": len(entries), "distinct_herbs": len(herb_cases), "ubiquitous_herbs": sorted(ubiq),
             "date_range": [dates[0], dates[-1]] if dates else [], "thresholds": {"min_support": MIN_SUPPORT, "min_group": MIN_GROUP}}
    return entries, stats


def _formula_entry(g: str, fm: str, cs: list[dict], group_n: int, age: list[dict]) -> dict:
    n = len(cs)
    per_herb: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for c in cs:
        for h in c["herbs"]:
            per_herb[h["name"]].append((c, h))
    core, opt = [], []
    for name, lst in sorted(per_herb.items(), key=lambda kv: -len(kv[1])):
        fr = len(lst) / n
        sp = Counter(h["special"] for _c, h in lst if h["special"])
        item = {"name": name, "n": len(lst), "freq": round(fr, 2), "dose": dose_summary([h["dose_g"] for _c, h in lst]), "special": sp.most_common(1)[0][0] if sp else ""}
        if fr >= CORE_RATIO:
            core.append(item)
        elif fr >= ALT_RATIO:
            opt.append(item)
    ev = []
    for c in cs[:30]:
        fq = find_quote(c["text"], c["fields"].get("formula", "")) if fm else ""
        hq = " ".join(_herb_quote(c["text"], h) for h in c["herbs"][:2])
        ev.append({"case_id": c["id"], "kind": "support", "quote": (fq or hq)[:140], "detail": {"n_herbs": len(c["herbs"])}})
    title = f"{g}：{fm}" if fm else f"{g}：常用药物组合（归纳）"
    ctext = "、".join(f"{x['name']}{x['dose']['median']:g}g" for x in core[:10])
    otext = "、".join(x["name"] for x in opt[:8])
    summ = (f"“{g}”类病例中 {n}/{group_n} 例（{_pct(n / group_n)}）用“{fm}”加减。" if fm else f"“{g}”类病例中 {n}/{group_n} 例有可归纳的用药组合。")
    summ += f"核心药（≥{round(CORE_RATIO * 100)}% 病例使用，括号为中位剂量）：{ctext or '无'}。" + (f"常加减：{otext}。" if otext else "")
    fl = safety_flags([{"name": x["name"], "dose_g": x["dose"].get("median")} for x in core + opt]) + age
    return _entry("formula", g, fm or "herb-set", title, {"formula": fm, "n": n, "of": group_n, "core": core, "optional": opt}, summ, n, group_n, ev, fl,
                  conditions=f"证属“{g}”", exceptions="核心药是统计共性，不是固定处方；具体取舍、加减与剂量需结合当前患者")
