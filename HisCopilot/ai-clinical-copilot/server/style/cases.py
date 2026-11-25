"""病例拆分 + 病历/处方关联 + 重复与异常识别（纯函数，无 I/O，便于大量测试）。

输入：`ParsedSource`（文本块或表格行）→ 输出：`RawCase` 列表，每个含：
  text        该病例的完整文本（病历 + 处方已合并）
  herbs       规则解析出的饮片 [{name, dose_g, special}]（权威来源；LLM 抽取只能在其上补充语义）
  anomalies   [{code, severity, message}]
  link_key    病历—处方关联键（用于把同一就诊的多行/多块合并）

切分策略（保守，宁可少切）：
  表格：每行一个病例；列名按同义词表映射到 {date, complaint, history, tcm, diagnosis, formula, herbs, advice, outcome, patient_key, name};
        若同一 patient_key+date 出现多行（病历行 + 处方行分开），合并为一个病例（病历-处方关联）。
  文本：先找"病例N/第N例/案N/【病例】/患者X,男,NN岁"这类**边界标记**；没有标记就按空行分段，且仅当段内含"处方/方药/药物组成"或 ≥3 个"药名+剂量"时才算病例，
        其余（学术心得、医话）归为经验笔记 note。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from server.domain.ids import sha256_hex
from server.style import lexicon as L

# --------------------------------------------------------------------------- 列名同义词
COLS = {
    "date": ("就诊日期", "日期", "就诊时间", "时间", "门诊日期", "初诊日期", "date"),
    "name": ("姓名", "患者姓名", "病人姓名", "患者", "name"),
    "patient_key": ("病历号", "门诊号", "病案号", "就诊卡号", "患者编号", "患者id", "病人id", "档案号", "id", "编号", "住院号"),
    "age": ("年龄", "岁数"),
    "gender": ("性别", "男女"),
    "complaint": ("主诉", "症状", "就诊原因", "chief complaint"),
    "history": ("现病史", "病史", "病情", "病情描述", "病历", "病历摘要", "临床资料", "一般情况", "四诊", "症状体征"),
    "tcm": ("证型", "中医辨证", "辨证", "证候", "中医诊断", "辨证分型", "中医证型", "病机", "舌脉", "舌苔脉象"),
    "diagnosis": ("诊断", "西医诊断", "初步诊断", "临床诊断", "病名", "疾病"),
    "formula": ("方名", "方剂", "处方名称", "主方", "方剂名称", "方药名称"),
    "herbs": ("处方", "方药", "药物组成", "药物", "中药", "用药", "组成", "饮片", "处方内容", "药方", "中药处方"),
    "advice": ("医嘱", "治法", "治则", "调护", "服法", "煎服法", "用法", "嘱托", "生活调护", "医嘱及调护"),
    "outcome": ("疗效", "转归", "疗效评价", "复诊", "复诊情况", "随访", "随访记录", "结果", "二诊", "效果", "预后"),
}

_HEAD_RE = re.compile(
    r"(?m)^\s*(?:[#>*]+\s*)?(?:【?\s*(?:典型)?(?:病例|医案|案例|验案|病案)\s*[一二三四五六七八九十百零〇\d]*\s*】?[：:、.．\s]|第\s*[一二三四五六七八九十百零〇\d]+\s*[例案]\s*[：:、.．\s]?|案\s*[一二三四五六七八九十百零〇\d]{1,4}(?=[\s：:、.．*])|[（(]\s*\d{1,4}\s*[)）]|\d{1,4}\s*[、.．]\s*(?=患者|姓名|[一-龥]{1,3}[，,]\s*[男女]))")
_MD_HEADING = re.compile(r"(?m)^\s*#{1,6}\s*(.+?)\s*$")
_ANNOT = re.compile(r"(?:\*\*)?\s*按\s*[：:]\s*(?:\*\*)?")
_PATIENT_LEAD = re.compile(r"(?m)^\s*(?:患者|病人)\s*[一-龥]{1,4}[，,]\s*[男女][，,]\s*\d{1,3}\s*岁")
_HAS_RX_WORD = re.compile(r"处方|方药|药物组成|组成|方用|予[^\n，。]{0,12}(?:汤|散|丸|饮|方|煎)|拟方|用药|中药|饮片|煎服|水煎")
_DOSE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|克|G)(?![A-Za-z])")


def section_name(h: str) -> str:
    s = h.replace("*", "").strip()
    s = re.sub(r"^[一二三四五六七八九十\d]+[、.．]\s*", "", s)
    s = re.sub(r"[（(][^)）]*[)）]$", "", s)
    return s.strip()[:40]


@dataclass
class RawCase:
    seq: int
    kind: str = "case"                          # case | note
    origin: str = "text"                        # text | table | ocr | paste
    text: str = ""
    label: str = ""
    section: str = ""
    herbs: list[dict] = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    anomalies: list[dict] = field(default_factory=list)
    link_key_raw: str = ""                      # 关联键明文（仅内存）；入库前哈希
    date: str = ""                              # YYYY-MM 或 ""
    names: set[str] = field(default_factory=set)  # 表格里的姓名（仅内存，供脱敏全文遮盖）


# --------------------------------------------------------------------------- 饮片解析
_SEP = r"[\s,，、;；/]*"


def parse_herbs(text: str) -> list[dict]:
    """从文本里解析 "药名 + 剂量(g)" 序列。只接受词表内的药名；"3g黄芪"（剂量在前）也识别。
    返回 [{name, dose_g, special, raw}]；同名重复保留第一次（异常里另报）。"""
    out: list[dict] = []
    seen: set[str] = set()
    t = text
    i, n = 0, len(t)
    while i < n:
        nm = L.longest_herb_at(t, i)
        if not nm:
            i += 1
            continue
        j = i + len(nm)
        m = re.match(r"\s*[（(]?\s*(?:(先煎|后下|包煎|另煎|烊化|冲服|打碎|捣碎|研末|兑服)\s*)?[)）]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:g|克|G)", t[j:j + 24])
        sp_before = ""
        dose = None
        k = j
        if m:
            dose = float(m.group(2))
            sp_before = m.group(1) or ""
            k = j + m.end()
        else:
            m2 = re.match(r"\s*[（(]\s*(先煎|后下|包煎|另煎|烊化|冲服|打碎|捣碎|研末|兑服)\s*[)）]\s*(\d+(?:\.\d+)?)\s*(?:g|克|G)", t[j:j + 24])
            if m2:
                dose, sp_before, k = float(m2.group(2)), m2.group(1), j + m2.end()
        if dose is None:
            i = j
            continue
        tail = t[k:k + 8]
        sp = sp_before or L.special_in(re.match(r"\s*[（(][^)）]{0,6}[)）]", tail).group(0) if re.match(r"\s*[（(][^)）]{0,6}[)）]", tail) else "")
        key = L.canonical(nm)
        if key not in seen:
            seen.add(key)
            out.append({"name": key, "as_written": nm, "dose_g": dose, "special": sp})
        i = k
    return out


# --------------------------------------------------------------------------- 异常
def anomalies_for(rc: RawCase) -> list[dict]:
    a: list[dict] = []
    txt = rc.text
    if len(txt.strip()) < 25:
        a.append({"code": "too_short", "severity": "warn", "message": "文本过短，信息量不足，建议排除或补充"})
    if rc.kind == "case":
        if not rc.herbs:
            a.append({"code": "no_prescription", "severity": "warn", "message": "未识别到“药名+克数”的处方，可能是只有病历没有处方，或剂量格式不标准"})
        if rc.herbs:
            if len(rc.herbs) < 4:
                a.append({"code": "few_herbs", "severity": "info", "message": f"仅识别到 {len(rc.herbs)} 味药，可能是识别不全"})
            big = [h["name"] for h in rc.herbs if h["dose_g"] > 60]
            if big:
                a.append({"code": "dose_extreme", "severity": "warn", "message": f"剂量异常大（>60g）：{'、'.join(big[:4])}，请核对是否录入错误"})
            tiny = [h["name"] for h in rc.herbs if h["dose_g"] < 0.5]
            if tiny:
                a.append({"code": "dose_tiny", "severity": "info", "message": f"剂量异常小（<0.5g）：{'、'.join(tiny[:4])}"})
        dups = _dup_herb_names(txt, rc.herbs)
        if dups:
            a.append({"code": "dup_herb", "severity": "info", "message": f"同一处方中重复出现：{'、'.join(dups[:4])}（已按首次计）"})
        if not re.search(r"证|辨|舌|脉|诊断|主诉|症", txt):
            a.append({"code": "no_clinical_info", "severity": "warn", "message": "没有症状/辨证/舌脉/诊断信息，无法归纳辨证思路"})
    if re.search(r"(?:[A-Za-z]:\\|/home/|<\?xml|<html)", txt):
        a.append({"code": "garbled", "severity": "warn", "message": "文本疑似包含非病历内容（路径/标记语言），请核对来源"})
    bad = sum(1 for c in txt if c == "�")
    if bad:
        a.append({"code": "encoding", "severity": "warn", "message": f"含 {bad} 个乱码字符（编码识别不准确），请核对"})
    return a


def _dup_herb_names(text: str, herbs: list[dict]) -> list[str]:
    cnt: dict[str, int] = {}
    i = 0
    while i < len(text):
        nm = L.longest_herb_at(text, i)
        if nm and re.match(r"\s*[（(]?\s*(?:先煎|后下|包煎|另煎|烊化|冲服)?\s*[)）]?\s*\d", text[i + len(nm):i + len(nm) + 14]):
            k = L.canonical(nm)
            cnt[k] = cnt.get(k, 0) + 1
            i += len(nm)
        else:
            i += 1
    return [k for k, v in cnt.items() if v > 1]


# --------------------------------------------------------------------------- 表格
def _map_columns(columns: list[str], override: dict | None = None) -> dict[str, str]:
    """{canonical field → 实际列名}；`override` 来自用户在向导里手工指定的映射。"""
    m: dict[str, str] = {}
    low = {c: re.sub(r"[\s（）()_\-#\d]+", "", c).lower() for c in columns}
    for canon, names in COLS.items():
        for col in columns:
            if col in m.values():
                continue
            if any(low[col] == re.sub(r"[\s_\-]+", "", n).lower() for n in names):
                m[canon] = col
                break
    for canon, names in COLS.items():                        # 次选：包含关系（"初诊主诉"）
        if canon in m:
            continue
        for col in columns:
            if col in m.values():
                continue
            if any(re.sub(r"[\s_\-]+", "", n).lower() in low[col] for n in names if len(n) >= 2):
                m[canon] = col
                break
    if override:
        for k, v in override.items():
            if v and v in columns:
                m[k] = v
            elif k in m and not v:
                m.pop(k)
    return m


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    return _map_columns(columns)


def _month(s: str) -> str:
    m = re.search(r"((?:19|20)\d{2})\s*[-/.年]\s*(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else ""


def cases_from_rows(rows: list[dict], columns: list[str], mapping: dict | None = None, source_label: str = "") -> tuple[list[RawCase], dict]:
    mp = _map_columns(columns, mapping)
    info = {"mapping": mp, "unmapped": [c for c in columns if c not in mp.values()], "rows": len(rows)}
    groups: dict[str, list[tuple[int, dict]]] = {}
    order: list[str] = []
    for i, r in enumerate(rows):
        pk = (r.get(mp.get("patient_key", ""), "") or "").strip()
        nm = (r.get(mp.get("name", ""), "") or "").strip()
        dt = (r.get(mp.get("date", ""), "") or "").strip()
        key = f"{pk or nm}|{dt}" if (pk or nm) and dt else f"row{i}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((i, r))
    out: list[RawCase] = []
    merged = 0
    for key in order:
        items = groups[key]
        if len(items) > 1:
            merged += len(items) - 1
        flds: dict[str, list[str]] = {}
        for _i, r in items:
            for canon in COLS:
                c = mp.get(canon)
                v = (r.get(c, "") if c else "").strip()
                if v and v not in flds.get(canon, []):
                    flds.setdefault(canon, []).append(v)
            for c in info["unmapped"]:                       # 未映射列也并入正文（可能含有用信息；脱敏会处理）
                v = (r.get(c, "") or "").strip()
                if v and len(v) <= 400:
                    flds.setdefault(f"其他·{c}", []).append(v)
        text = _compose(flds)
        rc = RawCase(seq=len(out) + 1, origin="table", text=text, fields={k: "；".join(v) for k, v in flds.items() if not k.startswith("其他·")},
                     label=f"{source_label} 第{items[0][0] + 2}行" + (f"等{len(items)}行" if len(items) > 1 else ""),
                     link_key_raw=key if not key.startswith("row") else "", date=_month(" ".join(flds.get("date", []))))
        if flds.get("name"):
            rc.names = {n for v in flds["name"] for n in re.split(r"[\s,，、]+", v) if len(n) >= 2}
        hs_text = " ".join(flds.get("herbs", []) + flds.get("formula", []))
        rc.herbs = parse_herbs(hs_text) or parse_herbs(text)
        rc.anomalies = anomalies_for(rc)
        if len(items) > 1:
            rc.anomalies.append({"code": "merged_rows", "severity": "info", "message": f"已把同一患者同一天的 {len(items)} 行（病历/处方分行）合并为一个病例"})
        out.append(rc)
    info["merged_rows"] = merged
    return out, info


_LABELS = {"date": "就诊时间", "complaint": "主诉", "history": "现病史/病情", "tcm": "中医辨证/舌脉", "diagnosis": "诊断", "formula": "方名", "herbs": "处方",
           "advice": "医嘱/治法", "outcome": "疗效/复诊", "age": "年龄", "gender": "性别", "name": "姓名", "patient_key": "病历号"}


def _compose(flds: dict[str, list[str]]) -> str:
    lines = []
    for canon in ("name", "gender", "age", "patient_key", "date", "complaint", "history", "tcm", "diagnosis", "formula", "herbs", "advice", "outcome"):
        if flds.get(canon):
            lines.append(f"{_LABELS[canon]}：{'；'.join(flds[canon])}")
    for k, v in flds.items():
        if k.startswith("其他·"):
            lines.append(f"{k[3:]}：{'；'.join(v)}")
    return "\n".join(lines)


def _split_document(joined: str, source_label: str = "", origin: str = "text") -> list[RawCase]:
    marks = [m.start() for m in _HEAD_RE.finditer(joined)]
    headings = [(m.start(), section_name(m.group(1))) for m in _MD_HEADING.finditer(joined)]
    out: list[RawCase] = []
    for a, b in zip(marks, marks[1:] + [len(joined)]):
        seg = joined[a:b]
        section = ""
        for pos, name in headings:
            if pos < a:
                section = name
            else:
                break
        cleaned_lines = [ln for ln in seg.splitlines() if not _MD_HEADING.match(ln)]
        cleaned = "\n".join(cleaned_lines).replace("**", "").strip()
        if not cleaned or len(cleaned) < 8:
            continue
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0][:80]
        m_annot = _ANNOT.search(cleaned)
        if m_annot:
            body = cleaned[:m_annot.start()].strip()
            annot = cleaned[m_annot.end():].strip()
        else:
            body, annot = cleaned, ""
        label = f"{source_label} · {title}"[:150] if source_label else title[:150]
        if body and len(body) >= 8:
            herbs = parse_herbs(body)
            looks_case = bool(_HAS_RX_WORD.search(body)) or len(herbs) >= 3
            rc = RawCase(seq=len(out) + 1, kind="case" if looks_case else "note", origin=origin, text=body,
                         label=label, section=section, herbs=herbs, date=_month(body[:200]))
            rc.anomalies = anomalies_for(rc) if rc.kind == "case" else ([{"code": "note", "severity": "info", "message": "未发现处方，已按“经验笔记”处理（可直接作为文字经验，不计入病例统计）"}]
                                                                       if len(body) >= 25 else [{"code": "too_short", "severity": "warn", "message": "文本过短"}])
            out.append(rc)
        if len(annot) >= 25:
            nc = RawCase(seq=len(out) + 1, kind="note", origin=origin, text=annot,
                         label=(label + " · 按语")[:150], section=section, herbs=[], date=_month(annot[:200]),
                         anomalies=[{"code": "annotation", "severity": "info", "message": f"医案按语（医师对本案的分析），已单列为经验笔记，对应「{title}」"}])
            out.append(nc)
    return out


# --------------------------------------------------------------------------- 文本
def cases_from_blocks(blocks: list[str], source_label: str = "", origin: str = "text") -> list[RawCase]:
    if len(blocks) > 1:
        joined = "\n\n".join(blocks)
        if len(list(_HEAD_RE.finditer(joined))) >= 2:
            return _split_document(joined, source_label, origin)
    out: list[RawCase] = []
    for bi, block in enumerate(blocks):
        parts = _split_by_markers(block)
        for pi, part in enumerate(parts):
            part = part.strip()
            if len(part) < 8:
                continue
            herbs = parse_herbs(part)
            looks_case = bool(_HAS_RX_WORD.search(part)) or len(herbs) >= 3
            rc = RawCase(seq=len(out) + 1, kind="case" if looks_case else "note", origin=origin, text=part,
                         label=f"{source_label} 第{len(out) + 1}段", herbs=herbs, date=_month(part[:200]))
            rc.anomalies = anomalies_for(rc) if rc.kind == "case" else ([{"code": "note", "severity": "info", "message": "未发现处方，已按“经验笔记”处理（可直接作为文字经验，不计入病例统计）"}]
                                                                       if len(part) >= 25 else [{"code": "too_short", "severity": "warn", "message": "文本过短"}])
            out.append(rc)
    return out


def _split_by_markers(block: str) -> list[str]:
    marks = [m.start() for m in _HEAD_RE.finditer(block)]
    if len(marks) < 2:
        marks = [m.start() for m in _PATIENT_LEAD.finditer(block)]
    if len(marks) >= 2:
        if marks[0] > 0 and block[:marks[0]].strip():
            marks = [0] + marks
        segs = [block[a:b] for a, b in zip(marks, marks[1:] + [len(block)])]
        # 一个病例段内若出现空行 + 一段完全没有处方/病例特征的文字（医话、心得），把它拆成独立段，避免被并入上一个病例而丢失
        out: list[str] = []
        for seg in segs:
            paras = [p for p in re.split(r"\n\s*\n", seg) if p.strip()]
            if len(paras) > 1 and not _HEAD_RE.match(paras[-1]) and not _HAS_RX_WORD.search(paras[-1]) and len(parse_herbs(paras[-1])) < 3:
                out.append("\n\n".join(paras[:-1]))
                out.append(paras[-1])
            else:
                out.append(seg)
        return out
    paras = [p for p in re.split(r"\n\s*\n", block) if p.strip()]
    return paras if len(paras) > 1 else [block]


# --------------------------------------------------------------------------- 去重
_DATE_FIELD = re.compile(r"(?:就诊时间|就诊日期|日期|时间)\s*[:：]\s*(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月")
_DATE_INLINE = re.compile(r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月")


def norm_for_hash(text: str) -> str:
    """去重用的归一化文本。占位符、标点、**日期**都不参与：同一处方在不同日期就诊 ≠ 重复录入（见 find_duplicates 的复诊判定）。"""
    t = _DATE_FIELD.sub("", text)
    t = _DATE_INLINE.sub("", t)
    t = re.sub(r"【[^】]*】", "", t)
    t = re.sub(r"\s+|[，,。；;：:、（）()\[\]【】]", "", t)
    return t.lower()


def content_hash(text: str) -> str:
    return sha256_hex(norm_for_hash(text))


def _shingles(s: str, k: int = 3) -> set[str]:
    return {s[i:i + k] for i in range(max(0, len(s) - k + 1))}


def jaccard(a: str, b: str) -> float:
    A, B = _shingles(a), _shingles(b)
    return len(A & B) / len(A | B) if A and B else 0.0


def herb_similarity(h1: list[dict], h2: list[dict]) -> float:
    a, b = {h["name"] for h in h1}, {h["name"] for h in h2}
    return len(a & b) / len(a | b) if a and b else 0.0


def find_duplicates(cases: list[tuple], near: float = 0.82) -> dict[str, tuple[str, float, str]]:
    """cases: [(id, normalized_text, herbs[, visit_date])] 按顺序 → {后出现者 id: (先出现者 id, 相似度, kind)}。
    kind: "exact" | "near" | "revisit"。
      * 内容相同（或高度相似）且就诊日期**都有且不同** → "revisit"（同一患者/同一方案的复诊，**不是**重复，不影响确认与统计，仅提示）；
      * 日期相同或缺失 → "exact"/"near"（疑似重复录入）。
    近似重复还要求饮片集合 Jaccard ≥ 0.9（防止"同一模板、不同患者"被误杀）。"""
    seen_hash: dict[str, tuple[str, str]] = {}
    out: dict[str, tuple[str, float, str]] = {}
    kept: list[tuple[str, str, list[dict], str]] = []

    def rel(a_date: str, b_date: str, base: str) -> str:
        return "revisit" if (a_date and b_date and a_date != b_date) else base

    for item in cases:
        cid, nt, hs = item[0], item[1], item[2]
        dt = item[3] if len(item) > 3 else ""
        h = sha256_hex(nt)
        if h in seen_hash:
            oid, odt = seen_hash[h]
            out[cid] = (oid, 1.0, rel(dt, odt, "exact"))
            continue
        seen_hash[h] = (cid, dt)
        hit = None
        for kid, kt, kh, kdt in kept[-400:]:
            if abs(len(kt) - len(nt)) > max(40, 0.3 * len(nt)):
                continue
            sc = jaccard(nt, kt)
            if sc >= near and (not hs and not kh or herb_similarity(hs, kh) >= 0.9):
                hit = (kid, sc, kdt)
                break
        if hit:
            out[cid] = (hit[0], round(hit[1], 3), rel(dt, hit[2], "near"))
        else:
            kept.append((cid, nt, hs, dt))
    return out
