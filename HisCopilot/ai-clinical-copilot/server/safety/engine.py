"""Rule-based prescription safety engine (pure functions, no I/O).

Philosophy
  * Assist, never decide: every finding carries rule id, severity, a human-readable reason and the item it
    concerns. The engine never edits a prescription.
  * Severity ladder (see Severity): info → warning (ack) → critical (ack + written reason) → block (fix first).
  * Deterministic & versioned: same input → same output; `RULES_VERSION` is stamped on every evaluation and the
    evaluation is bound to the prescription's content hash, so an edit after evaluation invalidates the result.
  * Catalog values (dose range, class, allergen classes) take precedence over the built-in reference data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from server.domain.enums import RxType, Severity
from server.domain.ids import hash_obj
from server.safety.data import reference as R

_PREP_PREFIXES = ("麸炒", "炒", "炙", "制", "煅", "酒", "醋", "盐", "蜜", "姜", "法", "清", "焦", "烫", "煨", "生", "熟")
_NAME_CLEAN = re.compile(r"[\s（(].*$")          # "黄芪 (蜜炙)" → "黄芪"


def clean_name(name: str) -> str:
    return _NAME_CLEAN.sub("", (name or "").strip())


def base_names(name: str) -> set[str]:
    """{name, name-without-preparation-prefix,...}: '炙甘草' → {'炙甘草','甘草'}."""
    n = clean_name(name)
    out = {n}
    cur = n
    for _ in range(2):
        for p in _PREP_PREFIXES:
            if cur.startswith(p) and len(cur) - len(p) >= 2:
                cur = cur[len(p):]
                out.add(cur)
                break
        else:
            break
    return {x for x in out if x}


# A single dose should look like "5mg", "0.5 g", "1片", "1/2片", "半片", "10-20mg", "2.5ml", "1g/次". Anything else (e.g. "5mg10mg",
# "适量", "遵医嘱") is not a dose a pharmacist can dispense from, so the doctor is asked to look again. Free text is allowed — it is flagged,
# never silently accepted.
_DOSE_UNITS = r"(?:mg|g|kg|ug|μg|µg|mcg|ml|mL|ML|l|L|U|IU|iu|mmol|克|毫克|微克|毫升|升|单位|片|粒|丸|袋|支|瓶|滴|喷|揿|贴|包|颗|枚|盒|丸)"
_DOSE_QTY = r"(?:半|[一二三四五六七八九十]|\d+(?:\.\d+)?(?:/\d+)?(?:\s*[-~–]\s*\d+(?:\.\d+)?)?)"
DOSE_RE = re.compile(rf"^\s*{_DOSE_QTY}\s*{_DOSE_UNITS}(?:\s*/\s*(?:次|日|天|d|kg|m2))?\s*$")


def dose_text_ok(text: str) -> bool:
    return bool(DOSE_RE.match(text or ""))


# --------------------------------------------------------------------------- data classes
@dataclass
class Item:
    index: int
    name: str
    kind: str                                  # western | patent | herb
    dose_text: str = ""
    dose_g: float | None = None
    frequency: str = ""
    days: int | None = None
    route: str = ""
    special: str = ""
    quantity: float | None = None
    rx_id: str = ""                            # which prescription this belongs to (for cross-Rx checks)


@dataclass
class Issue:
    rule_id: str
    severity: str
    message: str
    item_index: int | None = None
    item_name: str = ""
    refs: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity used for acknowledgements: survives re-evaluation if the finding is unchanged."""
        return hash_obj([self.rule_id, self.item_name, self.refs, self.item_index])[:16]

    def as_dict(self) -> dict:
        return {"key": self.key, "rule_id": self.rule_id, "severity": self.severity, "message": self.message,
                "item_index": self.item_index, "item_name": self.item_name, "refs": self.refs}


@dataclass
class Context:
    allergies: list[str] = field(default_factory=list)
    pregnant: bool | None = None
    lactating: bool | None = None
    renal_impairment: bool | None = None
    hepatic_impairment: bool | None = None
    gender: str = ""
    age: int | None = None
    current_meds: list[str] = field(default_factory=list)
    other_items: list[Item] = field(default_factory=list)       # items from the encounter's other live prescriptions
    catalog: dict[str, dict] = field(default_factory=dict)       # lowercase name/alias → catalog entry
    diagnoses: list[str] = field(default_factory=list)


def _cat(ctx: Context, name: str) -> dict | None:
    n = clean_name(name).lower()
    if n in ctx.catalog:
        return ctx.catalog[n]
    for b in base_names(name):
        if b.lower() in ctx.catalog:
            return ctx.catalog[b.lower()]
    return None


# --------------------------------------------------------------------------- helpers
_HERB_REF = {n: (lo, hi, sp, aliases) for n, lo, hi, sp, aliases in R.HERBS}


def _herb_range(ctx: Context, name: str) -> tuple[float | None, float | None, str]:
    """(min,max,default special method): catalog first, reference second."""
    c = _cat(ctx, name)
    if c and (c.get("dose_max_g") or c.get("dose_min_g")):
        return c.get("dose_min_g"), c.get("dose_max_g"), c.get("special_method", "") or ""
    for b in (clean_name(name), *base_names(name)):
        if b in _HERB_REF:
            lo, hi, sp, _ = _HERB_REF[b]
            return lo, hi, sp
        for k, (lo, hi, sp, aliases) in _HERB_REF.items():
            if b in aliases:
                return lo, hi, sp
    return None, None, (c or {}).get("special_method", "") or ""


def _western_class(ctx: Context, name: str) -> str:
    c = _cat(ctx, name)
    if c and c.get("drug_class"):
        return c["drug_class"]
    n = clean_name(name)
    for generic, cls in R.WESTERN_CLASS.items():
        if generic in n:
            return cls
    return ""


def _allergen_hits(allergen: str, name: str, cls_list: Iterable[str]) -> str | None:
    """Return a human reason if `allergen` plausibly matches this drug name or its allergen classes."""
    a = (allergen or "").strip().lower()
    if not a:
        return None
    n = clean_name(name).lower()
    if a in n or n in a and len(n) >= 2:
        return f"药品名称与过敏原「{allergen}」相同/包含"
    for cname, spec in R.ALLERGY_CLASSES.items():
        alias_hit = a in [x.lower() for x in spec["aliases"]] or any(x.lower() in a for x in spec["aliases"])
        class_hit = alias_hit or a == cname.lower()
        if not class_hit:
            continue
        if cname in cls_list:
            return f"属于「{cname}」，患者对「{allergen}」过敏"
        if any(d.lower() in n for d in spec["drugs"]):
            return f"属于「{cname}」，患者对「{allergen}」过敏"
    return None


def _num(x) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- rule groups
def _structural(rx_type: str, items: list[Item], meta: dict) -> list[Issue]:
    out: list[Issue] = []
    if not items:
        return [Issue("rx.empty", Severity.BLOCK.value, "处方没有任何药品，无法签署")]
    seen: dict[str, int] = {}
    for it in items:
        if not it.name.strip():
            out.append(Issue("rx.name_missing", Severity.BLOCK.value, f"第 {it.index + 1} 行药名为空", it.index))
            continue
        key = clean_name(it.name)
        if key in seen:
            out.append(Issue("rx.duplicate_item", Severity.WARNING.value,
                             f"「{it.name}」在本处方中重复出现（第 {seen[key] + 1} 行与第 {it.index + 1} 行）",
                             it.index, it.name))
        seen[key] = it.index
        if rx_type == RxType.HERBAL.value:
            if it.dose_g is None or it.dose_g <= 0:
                out.append(Issue("herb.dose_missing", Severity.BLOCK.value, f"「{it.name}」未填写有效克数", it.index, it.name))
        else:
            if not it.dose_text.strip():
                out.append(Issue("rx.dose_missing", Severity.BLOCK.value, f"「{it.name}」未填写单次剂量", it.index, it.name))
            elif not dose_text_ok(it.dose_text):
                out.append(Issue("rx.dose_format", Severity.WARNING.value,
                                 f"「{it.name}」的单次剂量「{it.dose_text.strip()}」格式无法识别（应形如 5mg、0.5g、1片），请核对是否填错", it.index, it.name))
            if not it.frequency.strip():
                out.append(Issue("rx.frequency_missing", Severity.BLOCK.value, f"「{it.name}」未填写用药频次", it.index, it.name))
            if (it.days is None or it.days <= 0) and (it.quantity is None or it.quantity <= 0):
                out.append(Issue("rx.duration_missing", Severity.BLOCK.value, f"「{it.name}」未填写疗程天数或发药数量", it.index, it.name))
            if not it.route.strip():
                out.append(Issue("rx.route_missing", Severity.WARNING.value, f"「{it.name}」未填写给药途径", it.index, it.name))
    if rx_type == RxType.HERBAL.value:
        dc = meta.get("doses_count")
        if not dc or int(dc) <= 0:
            out.append(Issue("herb.doses_count_missing", Severity.BLOCK.value, "未填写中药剂数"))
        elif int(dc) > 14:
            out.append(Issue("herb.doses_count_high", Severity.WARNING.value, f"剂数 {dc} 超过 14 剂，请确认"))
        if not (meta.get("decoction") or "").strip():
            out.append(Issue("herb.decoction_missing", Severity.WARNING.value, "未填写煎煮方法"))
        if not (meta.get("herbal_usage") or "").strip():
            out.append(Issue("herb.usage_missing", Severity.INFO.value, "未填写服用方法（如每日1剂，分早晚温服）"))
        if len(items) > 20:
            out.append(Issue("herb.many_items", Severity.INFO.value, f"共 {len(items)} 味药，超过 20 味，请确认组方"))
    else:
        for it in items:
            if it.days and it.days > 30:
                out.append(Issue("rx.long_course", Severity.WARNING.value, f"「{it.name}」疗程 {it.days} 天，超过 30 天，请确认", it.index, it.name))
    return out


def _catalog_presence(rx_type: str, items: list[Item], ctx: Context) -> list[Issue]:
    out = []
    for it in items:
        if not it.name.strip():
            continue
        if _cat(ctx, it.name) is None:
            known_ref = rx_type == RxType.HERBAL.value and any(b in _HERB_REF for b in base_names(it.name))
            if not known_ref:
                out.append(Issue("catalog.not_found", Severity.WARNING.value,
                                 f"「{it.name}」不在药品目录中，药房可能无法调配（或名称有误）", it.index, it.name))
    return out


def _herbal_rules(items: list[Item], ctx: Context) -> list[Issue]:
    out: list[Issue] = []
    names = [(it, base_names(it.name)) for it in items if it.name.strip()]
    for it, bases in names:
        lo, hi, default_sp = _herb_range(ctx, it.name)
        toxic = any(b in R.TOXIC_HERBS for b in bases)
        if any(b in R.RESTRICTED_HERBS for b in bases):
            out.append(Issue("herb.restricted", Severity.CRITICAL.value,
                             f"「{it.name}」属毒性/管制药材，普通门诊处方一般不得使用，需符合特殊管理规定", it.index, it.name))
        elif toxic:
            out.append(Issue("herb.toxic", Severity.WARNING.value,
                             f"「{it.name}」为毒性或有毒药材，请严格掌握剂量、炮制与疗程并告知患者", it.index, it.name))
        if it.dose_g and hi:
            if it.dose_g > hi * (1.0 if toxic else 2.0):
                out.append(Issue("herb.dose_far_above", Severity.CRITICAL.value,
                                 f"「{it.name}」{it.dose_g:g}g 明显超出常规用量上限（{hi:g}g）", it.index, it.name))
            elif it.dose_g > hi:
                out.append(Issue("herb.dose_above", Severity.WARNING.value,
                                 f"「{it.name}」{it.dose_g:g}g 超出常规用量上限（{hi:g}g），请确认", it.index, it.name))
        if it.dose_g and lo and it.dose_g < lo * 0.5:
            out.append(Issue("herb.dose_below", Severity.INFO.value,
                             f"「{it.name}」{it.dose_g:g}g 低于常规用量下限（{lo:g}g）", it.index, it.name))
        if default_sp and not it.special and default_sp != "冲服":
            out.append(Issue("herb.special_method", Severity.INFO.value,
                             f"「{it.name}」通常需「{default_sp}」，当前未标注特殊煎法", it.index, it.name))
    # --- incompatibilities
    for g1, g2 in R.EIGHTEEN_ANTAGONISMS:
        a = [it for it, b in names if b & set(g1)]
        c = [it for it, b in names if b & set(g2)]
        for x in a:
            for y in c:
                if x.index != y.index:
                    out.append(Issue("herb.eighteen_antagonism", Severity.CRITICAL.value,
                                     f"十八反：「{x.name}」与「{y.name}」属配伍禁忌", x.index, x.name,
                                     refs=[y.name]))
    for p, q in R.NINETEEN_FEARS:
        a = [it for it, b in names if p in b]
        c = [it for it, b in names if q in b]
        for x in a:
            for y in c:
                if x.index != y.index:
                    out.append(Issue("herb.nineteen_fear", Severity.CRITICAL.value,
                                     f"十九畏：「{x.name}」与「{y.name}」相畏，属配伍禁忌", x.index, x.name,
                                     refs=[y.name]))
    return out


def screen_herbs(items: list[Item], ctx: Context | None = None) -> list[Issue]:
    """Rule screen of a herb SET (toxic / restricted / dose range / 十八反 / 十九畏) without prescription-structure checks.
    Used to vet HISTORICAL experience (style profiles); the signing-time `evaluate()` remains the only authority on a real prescription."""
    return _herbal_rules(items, ctx or Context())


def _pregnancy_rules(rx_type: str, items: list[Item], ctx: Context) -> list[Issue]:
    out: list[Issue] = []
    preg_possible = ctx.gender in ("女", "F", "female") and (ctx.age is None or 12 <= ctx.age <= 55)
    if ctx.pregnant:
        for it in items:
            bases = base_names(it.name)
            if rx_type == RxType.HERBAL.value:
                if bases & R.PREGNANCY_FORBIDDEN:
                    out.append(Issue("preg.herb_forbidden", Severity.CRITICAL.value,
                                     f"妊娠期：「{it.name}」属妊娠禁用药", it.index, it.name))
                elif bases & R.PREGNANCY_CAUTION:
                    out.append(Issue("preg.herb_caution", Severity.WARNING.value,
                                     f"妊娠期：「{it.name}」属妊娠慎用药", it.index, it.name))
            else:
                cls = _western_class(ctx, it.name)
                if cls in R.PREGNANCY_WESTERN_AVOID:
                    out.append(Issue("preg.drug_avoid", Severity.CRITICAL.value,
                                     f"妊娠期：「{it.name}」（{cls}）一般应避免使用", it.index, it.name))
                c = _cat(ctx, it.name)
                if c and (c.get("pregnancy_note") or "").strip():
                    out.append(Issue("preg.catalog_note", Severity.WARNING.value,
                                     f"妊娠期：「{it.name}」目录提示：{c['pregnancy_note']}", it.index, it.name))
    elif ctx.pregnant is None and preg_possible:
        flagged = [it for it in items if (base_names(it.name) & (R.PREGNANCY_FORBIDDEN | R.PREGNANCY_CAUTION))
                   or _western_class(ctx, it.name) in R.PREGNANCY_WESTERN_AVOID]
        if flagged:
            names = "、".join(i.name for i in flagged[:4])
            out.append(Issue("preg.status_unknown", Severity.WARNING.value,
                             f"患者为育龄女性且未记录妊娠状态，处方含妊娠禁用/慎用药（{names}），请先确认是否妊娠"))
    if ctx.lactating:
        for it in items:
            if _western_class(ctx, it.name) in {"QUINOLONE"} or (base_names(it.name) & {"大黄", "麦芽"}):
                out.append(Issue("lact.caution", Severity.WARNING.value,
                                 f"哺乳期：「{it.name}」可能影响乳汁/婴儿，请评估", it.index, it.name))
    return out


def _allergy_rules(rx_type: str, items: list[Item], ctx: Context) -> list[Issue]:
    out: list[Issue] = []
    for it in items:
        c = _cat(ctx, it.name) or {}
        cls = list(c.get("allergen_classes") or [])
        for a in ctx.allergies:
            if rx_type == RxType.HERBAL.value:
                if clean_name(a) in base_names(it.name):
                    out.append(Issue("allergy.direct", Severity.CRITICAL.value,
                                     f"患者对「{a}」过敏，处方含「{it.name}」", it.index, it.name))
                elif any(x.lower() in a.lower() for x in R.ALLERGY_CLASSES["海鲜/蛋白"]["aliases"]) \
                        and (base_names(it.name) & R.ANIMAL_ORIGIN_HERBS):
                    out.append(Issue("allergy.animal_herb", Severity.WARNING.value,
                                     f"患者有「{a}」过敏史，「{it.name}」为动物来源药材，请确认", it.index, it.name))
            else:
                why = _allergen_hits(a, it.name, cls)
                if why:
                    out.append(Issue("allergy.drug", Severity.CRITICAL.value,
                                     f"过敏风险：「{it.name}」{why}", it.index, it.name, refs=[a]))
    return out


def _western_interaction_rules(rx_type: str, items: list[Item], ctx: Context) -> list[Issue]:
    out: list[Issue] = []
    this = [(it, _western_class(ctx, it.name)) for it in items if rx_type != RxType.HERBAL.value]
    others = [(it, _western_class(ctx, it.name)) for it in ctx.other_items if it.kind != "herb"]
    cur = [(m, _western_class(ctx, m)) for m in ctx.current_meds]
    # duplicates by class (within this Rx and vs other Rx / current meds)
    for i, (it, cls) in enumerate(this):
        if not cls:
            continue
        same_rx = [j for j, (o, c2) in enumerate(this) if j != i and c2 == cls and cls in R.DUPLICATE_CLASS_WARN]
        if same_rx and i < min(same_rx):
            out.append(Issue("ddi.duplicate_class", Severity.WARNING.value,
                             f"同类药物重复：「{it.name}」与「{this[same_rx[0]][0].name}」同属 {cls}", it.index, it.name))
        for o, c2 in others:
            if c2 == cls and cls in R.DUPLICATE_CLASS_WARN:
                out.append(Issue("ddi.duplicate_class_other_rx", Severity.WARNING.value,
                                 f"「{it.name}」与同次就诊其他处方中的「{o.name}」同属 {cls}", it.index, it.name, refs=[o.name]))
        for m, c2 in cur:
            if c2 == cls and cls in R.DUPLICATE_CLASS_WARN:
                out.append(Issue("ddi.duplicate_current_med", Severity.WARNING.value,
                                 f"「{it.name}」与患者当前用药「{m}」同属 {cls}", it.index, it.name, refs=[m]))
        for m, _ in cur:
            if clean_name(m) and clean_name(m) in clean_name(it.name):
                out.append(Issue("ddi.same_as_current", Severity.WARNING.value,
                                 f"「{it.name}」与患者当前正在使用的「{m}」重复", it.index, it.name, refs=[m]))
    # pair rules across (this ∪ others ∪ current)
    pool = [(it.name, cls, it.index, True) for it, cls in this] + [(o.name, c2, None, False) for o, c2 in others] + \
           [(m, c2, None, False) for m, c2 in cur]
    emitted: set[tuple] = set()
    for rules, sev in ((R.CLASS_PAIR_CRITICAL, Severity.CRITICAL.value), (R.CLASS_PAIR_WARN, Severity.WARNING.value)):
        for c1, c2, msg in rules:
            a = [p for p in pool if p[1] == c1]
            b = [p for p in pool if p[1] == c2]
            for x in a:
                for y in b:
                    if x[3] or y[3]:                       # at least one drug belongs to THIS prescription
                        idx = x[2] if x[3] else y[2]
                        nm = x[0] if x[3] else y[0]
                        k = (c1, c2, x[0], y[0])
                        if k in emitted:
                            continue
                        emitted.add(k)
                        out.append(Issue("ddi.pair", sev, f"{msg}（{x[0]} + {y[0]}）", idx, nm, refs=[x[0], y[0]]))
    # renal / hepatic
    for it, cls in this:
        if ctx.renal_impairment and cls in R.RENAL_CAUTION:
            out.append(Issue("organ.renal", Severity.WARNING.value, f"肾功能不全：「{it.name}」（{cls}）需评估禁忌或调整剂量", it.index, it.name))
        if ctx.hepatic_impairment and cls in R.HEPATIC_CAUTION:
            out.append(Issue("organ.hepatic", Severity.WARNING.value, f"肝功能不全：「{it.name}」（{cls}）需评估禁忌或调整剂量", it.index, it.name))
    return out


def _herb_drug_rules(rx_type: str, items: list[Item], ctx: Context) -> list[Issue]:
    """Herb ↔ western interactions, in either direction (herbs in this Rx vs western in this/other Rx/current meds)."""
    out: list[Issue] = []
    west_pool = [(i.name, _western_class(ctx, i.name)) for i in (ctx.other_items if rx_type == RxType.HERBAL.value else items)
                 if i.kind != "herb"] + [(m, _western_class(ctx, m)) for m in ctx.current_meds]
    herb_pool = [(it.name, base_names(it.name), it.index) for it in items] if rx_type == RxType.HERBAL.value else \
                [(o.name, base_names(o.name), None) for o in ctx.other_items if o.kind == "herb"]
    seen: set[tuple] = set()
    for herbs, cls, sev, msg in R.HERB_DRUG_INTERACTIONS:
        for hn, hb, hi in herb_pool:
            if not (hb & set(herbs)):
                continue
            for wn, wc in west_pool:
                if wc == cls and (hn, wn, cls) not in seen:
                    seen.add((hn, wn, cls))
                    out.append(Issue("hdi.pair", sev, f"{msg}（{hn} + {wn}）", hi, hn, refs=[wn]))
    return out


# --------------------------------------------------------------------------- entry points
def evaluate(rx_type: str, items: list[Item], ctx: Context, meta: dict | None = None) -> list[Issue]:
    meta = meta or {}
    issues: list[Issue] = []
    issues += _structural(rx_type, items, meta)
    issues += _catalog_presence(rx_type, items, ctx)
    if rx_type == RxType.HERBAL.value:
        issues += _herbal_rules(items, ctx)
    issues += _pregnancy_rules(rx_type, items, ctx)
    issues += _allergy_rules(rx_type, items, ctx)
    issues += _western_interaction_rules(rx_type, items, ctx)
    issues += _herb_drug_rules(rx_type, items, ctx)
    order = {Severity.BLOCK.value: 0, Severity.CRITICAL.value: 1, Severity.WARNING.value: 2, Severity.INFO.value: 3}
    issues.sort(key=lambda i: (order.get(i.severity, 9), i.item_index if i.item_index is not None else -1, i.rule_id))
    # de-duplicate identical findings
    seen_k, uniq = set(), []
    for i in issues:
        if i.key not in seen_k:
            seen_k.add(i.key)
            uniq.append(i)
    return uniq


def summarize(issues: list[Issue]) -> dict:
    c = {s.value: 0 for s in Severity}
    for i in issues:
        c[i.severity] = c.get(i.severity, 0) + 1
    return c


def signing_blockers(issues: list[Issue], acks: dict[str, str]) -> list[dict]:
    """What still prevents signing, given acknowledgements {issue_key: reason-or-'ack'}.

    block    → always blocks.
    critical → needs a written reason (≥ 5 chars).
    warning  → needs an acknowledgement (any non-empty string).
    """
    out = []
    for i in issues:
        if i.severity == Severity.BLOCK.value:
            out.append({"key": i.key, "need": "fix", "message": i.message})
        elif i.severity == Severity.CRITICAL.value and len((acks.get(i.key) or "").strip()) < 5:
            out.append({"key": i.key, "need": "reason", "message": i.message})
        elif i.severity == Severity.WARNING.value and not (acks.get(i.key) or "").strip():
            out.append({"key": i.key, "need": "ack", "message": i.message})
    return out
