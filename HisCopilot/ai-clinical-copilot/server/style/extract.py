"""结构化抽取：病例文本 → {syndrome, disease, symptoms, tongue, pulse, principle, formula, outcome …}。

可信度设计（防止“模型凭空总结医师习惯”）
  * 饮片与剂量 **永远由规则解析（cases.parse_herbs）决定**，LLM 不参与；LLM 只补充语义字段。
  * LLM 返回的每个字段值都要在病例文本里“逐字找得到”（允许去掉标点/空格后匹配）才被采信，否则丢弃并计数；
    因此画像里的每条经验都能回溯到原文片段。
  * 没有 LLM（或 LLM 失败）时退化为**规则抽取**（关键词/正则），能力有限但完全可追溯；struct_status 标 `rules_only`。
  * 抽取只看“已脱敏且医师确认”的文本。外部模型额外要求：管理员开关 + 本批次书面确认（在 service 层校验，这里不重复）。
"""
from __future__ import annotations

import json
import re

from server.ai.client import ModelError, chat_json
from server.style import lexicon as L

SYSTEM = """你是中医病例信息抽取助手。只从给定病例文本中**摘录**信息，不得推断、补充或改写。
规则：
1. 每个字段的值必须是病例原文中**连续出现**的词语或短句（可去掉标点），找不到就填空字符串/空数组。严禁自己总结或编造。
2. 不要输出任何药物、剂量、克数（处方由程序单独解析）。
3. 不要输出姓名、电话、编号等个人信息；文本中的【姓名】等占位符保持原样或忽略。
4. 所有值使用简体中文。以 { 开头，只输出一个 JSON 对象，不要解释。
输出 JSON：
{"disease":"西医/中医病名（原文）","syndrome":"证型/证候（原文，如 肝阳上亢）","symptoms":["主要症状（原文）"],
 "tongue":"舌象（原文）","pulse":"脉象（原文）","principle":"治法/治则（原文，如 平肝潜阳）","formula":"方名（原文，如 天麻钩藤饮加减）",
 "modifications":["加减说明（原文，如 失眠加酸枣仁）"],"outcome":"疗效/复诊反馈（原文）","advice":["医嘱/调护要点（原文）"]}"""

_STR_FIELDS = ("disease", "syndrome", "tongue", "pulse", "principle", "formula", "outcome")
_LIST_FIELDS = ("symptoms", "modifications", "advice")


def _norm(s: str) -> str:
    return re.sub(r"[\s，,。；;：:、（）()\[\]【】\"'“”‘’·\-—]+", "", s or "")


def verbatim(value: str, text_norm: str) -> bool:
    v = _norm(value)
    return len(v) >= 2 and v in text_norm


# --------------------------------------------------------------------------- 规则抽取（无模型也能得到可追溯的最小结构）
_RULE = {
    "syndrome": re.compile(r"(?:辨证|证型|证候|辨为|辨属|证属|属|证见|中医诊断)[为：:]?\s*([一-龥]{2,12}?证?)(?=[，。；;,\s]|$)"),
    "principle": re.compile(r"(?:治法|治则|治以|治宜|法当|拟|立法)[：:为]?\s*([一-龥、，]{2,16}?)(?=[。；;\n]|方|处方|拟|予|用|$)"),
    "formula": re.compile(r"(?:予|拟|用|方用|方选|选用|以|投|方名[：:]?)\s*([一-龥]{2,10}(?:汤|散|丸|饮|煎|方|膏|丹|颗粒))(?:加减|合方|加味|化裁)?"),
    "tongue": re.compile(r"(舌[:：]?\s*[一-龥、，]{2,14}?)(?=[，。；;,]?\s*脉|[。；;\n]|$)"),
    "pulse": re.compile(r"脉[:：]?\s*([一-龥、]{1,10})"),
    "disease": re.compile(r"(?:诊断|西医诊断|病名|临床诊断)[：:]\s*([一-龥A-Za-z0-9、，（）()]{2,30}?)(?=[。；;\n]|$)"),
    "outcome": re.compile(r"((?:服|药后|二诊|复诊|三诊|随访)[^。\n]{0,40}(?:好转|缓解|减轻|消失|改善|痊愈|加重|无效|未见好转|稳定)[^。\n]{0,20})"),
}


def rule_extract(text: str) -> dict:
    out: dict = {k: "" for k in _STR_FIELDS}
    out.update({k: [] for k in _LIST_FIELDS})
    for k, rx in _RULE.items():
        m = rx.search(text)
        if m:
            out[k] = m.group(1).strip("，、 ")
    m = re.search(r"(?:主诉|症见|症状)[：:]?\s*([^。\n]{4,80})", text)
    if m:
        out["symptoms"] = [x for x in re.split(r"[，,、；;]", m.group(1)) if 1 < len(x) <= 14][:8]
    mods = []
    for m in re.finditer(r"(?<![一-龥])([一-龥]{1,6}(?:加|去|减)[一-龥]{1,6})(?![一-龥]*(?:减轻|减少|减退|加重|加班|加强))", text):
        frag = m.group(1)
        if re.search(r"(?:加减|加味)$", frag) or L.longest_herb_at(frag, 0) == "" and not re.search(r"(?:加|去|减)" + "(?:" + "|".join(sorted(L.HERBS, key=len, reverse=True)[:400]) + ")", frag):
            continue
        mods.append(frag)
    out["modifications"] = list(dict.fromkeys(mods))[:4]
    return out


# --------------------------------------------------------------------------- 主入口
def extract(text: str, herbs: list[dict], *, role: str = "main") -> dict:
    """→ {fields:{…}, herbs, status: ok|rules_only, model, dropped:int, error}。role: main(本地 27B) | tcm(本地 7B) | ext(外部，需调用方先校验授权)。"""
    tn = _norm(text)
    rules = rule_extract(text)
    base = {"fields": rules, "herbs": herbs, "status": "rules_only", "model": "rules", "dropped": 0, "error": ""}
    try:
        d, res = chat_json(SYSTEM, "病例文本：\n" + text[:3500], role=role, max_tokens=700, timeout=240, retries=1)
    except ModelError as e:
        base["error"] = f"{e.code}: {e.message}"
        return base
    dropped = 0
    fields: dict = {}
    for k in _STR_FIELDS:
        v = d.get(k, "")
        v = v.strip() if isinstance(v, str) else ""
        if v and verbatim(v, tn):
            fields[k] = v[:80]
        else:
            if v:
                dropped += 1
            fields[k] = rules.get(k, "") if rules.get(k) and verbatim(rules[k], tn) else ""
    for k in _LIST_FIELDS:
        vs = d.get(k, [])
        vs = [x.strip() for x in vs if isinstance(x, str)] if isinstance(vs, list) else []
        ok = [x[:60] for x in vs if verbatim(x, tn)][:8]
        dropped += len(vs) - len(ok)
        fields[k] = ok or [x for x in rules.get(k, []) if verbatim(x, tn)]
    return {"fields": fields, "herbs": herbs, "status": "ok", "model": res.model, "dropped": dropped, "error": ""}


# --------------------------------------------------------------------------- 规范化（把"肝阳上亢证""肝阳上亢型"并为一组）
_SYN_SUFFIX = re.compile(r"(证|型|症)$")
_SYN_MAP = {"肝阳上亢": "肝阳上亢", "肝阳亢盛": "肝阳上亢", "阴虚阳亢": "阴虚阳亢", "气阴两虚": "气阴两虚", "心脾两虚": "心脾两虚", "脾胃虚弱": "脾胃虚弱",
            "脾虚湿盛": "脾虚湿盛", "痰湿中阻": "痰湿中阻", "肝郁气滞": "肝郁气滞", "肝气郁结": "肝郁气滞", "肝胃不和": "肝胃不和", "肾阴虚": "肾阴虚", "肾阳虚": "肾阳虚",
            "气血两虚": "气血两虚", "风寒袭肺": "风寒袭肺", "风热犯肺": "风热犯肺", "湿热下注": "湿热下注"}


def norm_syndrome(s: str) -> str:
    s = _norm(s)
    s = _SYN_SUFFIX.sub("", s)
    return _SYN_MAP.get(s, s)


def norm_formula(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"(加减|加味|化裁|合方|合|方|颗粒|汤剂)+$", "", s)
    return s


def group_key(fields: dict) -> str:
    """病证分组键：优先中医证型，其次病名。"""
    syn = norm_syndrome(fields.get("syndrome", ""))
    if syn and len(syn) >= 2:
        return syn
    dis = _norm(fields.get("disease", ""))
    return dis[:16] if len(dis) >= 2 else ""


def herb_vocab_ok(name: str) -> bool:
    return L.is_known_herb(name)


def extract_rules(text: str, herbs: list[dict]) -> dict:
    """纯规则抽取（不调用任何模型）：只保留能在原文中逐字找到的字段。"""
    tn = _norm(text)
    r = rule_extract(text)
    fields: dict = {}
    for k in _STR_FIELDS:
        fields[k] = r.get(k, "") if r.get(k) and verbatim(r[k], tn) else ""
    for k in _LIST_FIELDS:
        fields[k] = [x for x in r.get(k, []) if verbatim(x, tn)]
    return {"fields": fields, "herbs": herbs, "status": "rules_only", "model": "rules", "dropped": 0, "error": ""}
