"""规则脱敏（病例文本 → 去标识文本）。

原则
  * 宁可多遮：误遮一个词的代价是画像少一点信息，漏遮一个姓名的代价是泄露患者隐私。
  * 脱敏前的原文**不落库**：解析阶段在内存里脱敏，只持久化脱敏后的文本；占位符形如【姓名】。
  * 规则脱敏不是保证：叙述里没有任何提示词的姓名、罕见病 + 年龄 + 地区的组合都可能漏。所以流程上
    ① 每个病例必须由人看过脱敏后的文本并确认才能进入抽取；② 外发外部模型需要管理员开关 + 本批次的书面确认；
    ③ 返回的 `risk` / `residual` 只描述"哪里可疑"，绝不包含可疑内容本身（因为会被存储）。
  * 不影响临床含义的信息保留：年龄（≥90 岁归并为"90岁以上"）、性别、年月、化验数值、药名剂量。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# 常见姓氏（单姓 + 常见复姓）。刻意不含容易和医学用词混淆的字：年、方、米、石、白、黄(黄芪?)——黄、白、石、方在药名里太常见，
# 只在"明确的姓名提示词之后"才按任意 2-4 字处理，不参与无提示的姓氏启发式。
_SURNAMES_SAFE = ("王李张刘陈杨赵周吴徐孙朱马胡郭林何高罗郑梁谢宋唐许韩冯邓曹彭曾萧田董潘袁蔡蒋余于杜叶程魏苏吕丁任沈姚卢姜崔钟谭陆汪范金廖"
                  "贾夏韦付傅邹孟熊秦邱江尹薛闫段雷侯龙史陶贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
_COMPOUND = ("欧阳", "司马", "上官", "诸葛", "东方", "慕容", "皇甫", "尉迟", "公孙", "夏侯", "令狐", "长孙", "宇文", "司徒")
_SUR = "[" + _SURNAMES_SAFE + "]"
_CN = "[一-龥]"

_PH = {"name": "【姓名】", "phone": "【电话】", "idcard": "【证件号】", "code": "【编号】", "addr": "【地址】", "mail": "【邮箱】",
       "url": "【链接】", "doctor": "【医师】", "account": "【账号】", "org": "【单位】"}

# --- 带提示词的字段（最可靠）
_LABEL_NAME = re.compile(r"(姓\s*名|患者姓名|病人姓名|就诊人|联系人|家属姓名|陪同人|监护人)\s*[:：]\s*(" + _CN + r"[一-龥·]{1,3})")
_LABEL_PATIENT = re.compile(r"(?:患者|病人)\s*[:：]\s*(" + _CN + r"[一-龥·]{1,3})(?=[，,、\s；;（(]|$)")
_LABEL_DOCTOR = re.compile(r"(?:主治医[师生]|开方医[师生]|处方医[师生]|接诊医[师生]|经治医[师生]|签名|医师签名|医生签名|审核|调配|核对|复核|发药|医师|医生)\s*[:：]\s*(" + _CN + r"[一-龥·]{1,3})")
_LABEL_CODE = re.compile(r"(门诊号|住院号|病历号|病案号|就诊卡号|就诊号|医保号|社保号|身份证号?|证件号|登记号|流水号|处方号|卡号|病人\s*ID|患者\s*ID|档案号|床号|检验号|条码号|ID)\s*[:：#]?\s*([A-Za-z0-9\-_/]{3,})", re.I)
_LABEL_ADDR = re.compile(r"(家庭住址|现住址|户籍地址|联系地址|居住地|住址|地址|工作单位|单位|职业单位)\s*[:：]\s*([^，。；;\n]{2,60})")
_LABEL_ACCOUNT = re.compile(r"(微信号?|微信|QQ|qq|支付宝)\s*[:：]?\s*([A-Za-z0-9_\-]{5,})")
_LABEL_BIRTH = re.compile(r"(出生日期|出生年月|生日|出生)\s*[:：]?\s*(\d{4})\s*[-/.年]\s*\d{1,2}(?:\s*[-/.月]\s*\d{1,2}日?)?")

# --- 无提示词、形态可靠的标识
_IDCARD = re.compile(r"(?<![0-9A-Za-z])(\d{17}[\dXx]|\d{15})(?![0-9A-Za-z])")
_PHONE = re.compile(r"(?<![\d.])1[3-9](?:[ \t-]?\d){9}(?![\d.])")      # 11 位手机号，允许任意空格/连字符分组
_LANDLINE = re.compile(r"(?<!\d)(0\d{2,3}[- ]\d{7,8}|\(0\d{2,3}\)\d{7,8})(?!\d)")
_MAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s，。；;）)]+")

# 地址：省市区县街道门牌（至少两级才算，避免把"北京"这种单独地名当地址）
_ADDR_CHAIN = re.compile(r"(?:" + _CN + r"{2,8}(?:省|自治区))?" + _CN + r"{2,8}(?:市|州|盟)(?:" + _CN + r"{1,8}(?:区|县|旗|市))?" + _CN + r"{0,12}(?:路|街|道|巷|弄|镇|乡|村|大道|小区|花园|新村|苑|大厦|公寓)" + r"[一-龥0-9\-]{0,16}(?:号|栋|幢|楼|室|单元|户)?")
_ADDR_ROAD = re.compile(_CN + r"{2,10}(?:路|街|道|巷|弄)\s*\d{1,5}\s*(?:号|弄)(?:\s*\d{1,4}\s*(?:栋|幢|号楼|楼|室|单元))?")
_ADDR_ESTATE = re.compile(_CN + r"{2,12}(?:小区|花园|苑|大厦|公寓|新村|家园)[一-龥0-9\-]{0,10}(?:\d{1,3}\s*(?:栋|幢|号楼|楼|室|单元))")

# 称谓类：王先生/李女士/张老师/刘大爷/陈阿姨/某某/王某
_HONORIFIC = re.compile(r"((?:" + "|".join(_COMPOUND) + r"|" + _SUR + r"))(先生|女士|老师|大爷|大妈|大叔|大哥|大姐|阿姨|奶奶|爷爷|同志|某某)")
_NICK = re.compile(r"(?<![一-龥])(?:老|小)(" + _SUR + r")(?![一-龥]{0,0}[年人便米])")
# 病例开头的"张三，男，58岁"/"患者张三，男"：姓名紧挨性别或年龄
_LEAD_NAME = re.compile(r"(?:^|[\n。；;，,]\s*|患者|病人|就诊人)(" + r"(?:" + "|".join(_COMPOUND) + r"|" + _SUR + r")" + _CN + r"{1,2})(?=\s*[，,、]?\s*(?:男|女|性别|\d{1,3}\s*岁|年龄))")
_DOC_TITLE = re.compile(r"(?<![一-龥])((?:" + "|".join(_COMPOUND) + r"|" + _SUR + r")" + _CN + r"{0,2})(?=医生|医师|大夫|主任|教授|院长|护士)")

_DATE_FULL = re.compile(r"(?<!\d)((?:19|20)\d{2})[ \t]*[-/.年][ \t]*(\d{1,2})[ \t]*[-/.月][ \t]*\d{1,2}[ \t]*日?")
_DATE_MD = re.compile(r"(?<![\d年])(\d{1,2})[ \t]*月[ \t]*\d{1,2}[ \t]*[日号]")
_AGE_OLD = re.compile(r"(?<!\d)(?:9\d|1[01]\d)\s*(?:岁|周岁)")

# 剂量/计量语境的数字不算"可疑长数字"
_UNIT_AFTER = re.compile(r"^\s*(?:mg|g|kg|ml|mL|μg|ug|mmol|U|IU|%|克|毫克|毫升|剂|片|粒|次|天|日|周|年|月|岁|元|kPa|mmHg|cm|mm)", re.I)
_LONG_DIGITS = re.compile(r"(?<![\d.])\d{7,}(?![\d.])")


@dataclass
class Redaction:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    risk: str = "low"                         # low | medium | high
    residual: list[str] = field(default_factory=list)      # 描述（不含内容）
    names: set[str] = field(default_factory=set)           # 本病例内识别出的姓名（仅内存使用，调用方不得存库）

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _bump(counts: dict[str, int], key: str, n: int = 1) -> None:
    counts[key] = counts.get(key, 0) + n


def redact(text: str, extra_names: Iterable[str] = ()) -> Redaction:
    """`extra_names`：调用方已知的患者姓名（如表格“姓名”列），会在全文内被一并遮盖。"""
    counts: dict[str, int] = {}
    names: set[str] = {n.strip() for n in extra_names if n and len(n.strip()) >= 2}
    t = text or ""

    # ---- 1. 先收集姓名（要在替换其它字段之前，否则标签内容被先替换掉就收集不到了）
    for m in _LABEL_NAME.finditer(t):
        names.add(m.group(2))
    for m in _LABEL_PATIENT.finditer(t):
        names.add(m.group(1))
    for m in _LEAD_NAME.finditer(t):
        names.add(m.group(1))
    doctors: set[str] = {m.group(1) for m in _LABEL_DOCTOR.finditer(t)} | {m.group(1) for m in _DOC_TITLE.finditer(t)}
    names -= doctors

    # ---- 2. 带提示词的字段（整段替换内容、保留标签，便于阅读）
    def sub_label(rx: re.Pattern, ph: str, key: str, grp: int) -> None:
        nonlocal t
        def f(m: re.Match) -> str:
            _bump(counts, key)
            s = m.group(0)
            return s[:m.start(grp) - m.start(0)] + ph
        t = rx.sub(f, t)

    sub_label(_LABEL_NAME, _PH["name"], "姓名", 2)
    sub_label(_LABEL_PATIENT, _PH["name"], "姓名", 1)
    sub_label(_LABEL_DOCTOR, _PH["doctor"], "医师", 1)
    sub_label(_LABEL_CODE, _PH["code"], "编号", 2)
    sub_label(_LABEL_ADDR, _PH["addr"], "地址", 2)
    sub_label(_LABEL_ACCOUNT, _PH["account"], "账号", 2)

    def birth(m: re.Match) -> str:
        _bump(counts, "出生日期")
        return f"{m.group(1)}：{m.group(2)}年"
    t = _LABEL_BIRTH.sub(birth, t)

    # ---- 3. 形态可靠的标识
    for rx, ph, key in ((_URL, _PH["url"], "链接"), (_MAIL, _PH["mail"], "邮箱"), (_IDCARD, _PH["idcard"], "证件号"),
                        (_PHONE, _PH["phone"], "电话"), (_LANDLINE, _PH["phone"], "电话")):
        t, n = rx.subn(ph, t)
        if n:
            _bump(counts, key, n)
    for rx in (_ADDR_CHAIN, _ADDR_ROAD, _ADDR_ESTATE):
        t, n = rx.subn(_PH["addr"], t)
        if n:
            _bump(counts, "地址", n)

    # ---- 4. 称谓 / 医师名
    t, n = _HONORIFIC.subn(_PH["name"], t)
    if n:
        _bump(counts, "姓名", n)
    t, n = _NICK.subn(_PH["name"], t)
    if n:
        _bump(counts, "姓名", n)
    for d in sorted(doctors, key=len, reverse=True):
        t, n = re.subn(re.escape(d) + r"(?:医生|医师|大夫|主任|教授|院长|护士)?", _PH["doctor"], t)
        if n:
            _bump(counts, "医师", n)

    # ---- 5. 全文遮盖已识别的姓名（长名优先；单字不处理）
    for nm in sorted(names, key=len, reverse=True):
        if nm in t:
            c = t.count(nm)
            t = t.replace(nm, _PH["name"])
            _bump(counts, "姓名", c)

    # ---- 6. 日期泛化 / 高龄归并
    def dfull(m: re.Match) -> str:
        _bump(counts, "日期泛化")
        return f"{m.group(1)}年{int(m.group(2))}月"
    t = _DATE_FULL.sub(dfull, t)
    t, n = _DATE_MD.subn(lambda m: f"{int(m.group(1))}月", t)
    if n:
        _bump(counts, "日期泛化", n)
    t, n = _AGE_OLD.subn("90岁以上", t)
    if n:
        _bump(counts, "高龄归并", n)

    # ---- 7. 残留检查（只描述，不含内容）
    residual: list[str] = []
    hard = 0
    for m in _LONG_DIGITS.finditer(t):
        if _UNIT_AFTER.match(t[m.end():m.end() + 6]):
            continue
        hard += 1
        residual.append(f"仍含 1 处 {len(m.group(0))} 位连续数字（可能是编号/证件号），位置约第 {m.start()} 字")
    for m in re.finditer(r"(?:姓\s*名|患者姓名)\s*[:：]\s*(?!【)\S{1,6}", t):
        hard += 1
        residual.append(f"“姓名”字段后仍有未遮盖内容，位置约第 {m.start()} 字")
    soft = 0
    for m in re.finditer(r"(?<![一-龥])(?:" + "|".join(_COMPOUND) + r"|" + _SUR + r")" + _CN + r"{1,2}(?=[，,、]\s*(?:女|男)\b)", t):
        soft += 1
        residual.append(f"位置约第 {m.start()} 字处有疑似姓名（“姓+名，性别”形式）")
    if "某" in t and re.search(r"[一-龥]某[一-龥]?", t):
        soft += 1
        residual.append("文本中含“某”字称谓，可能是被省略的姓名")
    risk = "high" if hard else ("medium" if soft else "low")
    return Redaction(text=t, counts=counts, risk=risk, residual=residual[:12], names=names)


def has_placeholder(text: str) -> bool:
    return any(p in text for p in _PH.values())
