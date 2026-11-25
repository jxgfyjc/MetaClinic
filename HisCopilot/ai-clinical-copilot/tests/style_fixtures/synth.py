"""Fictional, fully synthetic physician cases for the style-profile tests (no real patient data)."""
from __future__ import annotations

import random

from server.style import cases as C
from server.style import extract as X

BASE_GAN = [("天麻", 10), ("钩藤", 15), ("石决明", 30), ("栀子", 9), ("黄芩", 9), ("川牛膝", 12), ("杜仲", 10), ("桑寄生", 15), ("夜交藤", 15), ("茯神", 12), ("甘草", 6)]
BASE_XIN = [("黄芪", 20), ("党参", 15), ("白术", 12), ("茯神", 15), ("酸枣仁", 15), ("远志", 6), ("龙眼肉", 10), ("当归", 10), ("木香", 6), ("炙甘草", 6)]


def mk(i, syn, tongue, pulse, sym, principle, formula, herbs, mod="", outcome="", vd="2024-03", rnd=None):
    rnd = rnd or random
    hs = " ".join(f"{n}{g}g" for n, g in herbs)
    text = f"【姓名】，{rnd.choice(['男', '女'])}，{rnd.randint(40, 70)}岁。{sym}，{tongue}，{pulse}，辨证{syn}。治以{principle}，予{formula}加减：{hs}。{mod}{outcome}"
    f = {"syndrome": syn, "tongue": tongue, "pulse": pulse, "principle": principle, "formula": formula, "symptoms": sym.split("，")[:3],
         "modifications": [mod.strip("。")] if mod else [], "outcome": outcome.strip("。") if outcome else "", "disease": ""}
    return {"id": f"c{i}", "kind": "case", "text": text, "group_key": X.group_key(f), "fields": f, "herbs": C.parse_herbs(text), "visit_date": vd}


def corpus(seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    cs = []
    for i in range(10):                                       # 肝阳上亢 ×10：天麻钩藤饮；4 例加酸枣仁治失眠；其中 5 例另加夏枯草+菊花（形成"夏枯草+菊花"配伍）
        hs = [(n, g + rnd.choice([0, 0, 2, -1])) for n, g in BASE_GAN if rnd.random() > 0.08]
        if i % 3 == 0:
            hs.append(("酸枣仁", 15))
        if i % 2 == 1:
            hs += [("夏枯草", 12), ("菊花", 10)]
        cs.append(mk(i, "肝阳上亢", "舌红苔薄黄", "脉弦数", "头晕头痛，烦躁易怒，口苦", "平肝潜阳", "天麻钩藤饮", hs, mod="失眠加酸枣仁。" if i % 3 == 0 else "",
                     outcome="二诊：服药7剂后头痛明显减轻。" if i % 2 == 0 else "", vd="2024-0%d" % (1 + i % 9), rnd=rnd))
    for i in range(10, 14):                                   # 心脾两虚 ×4，较旧
        cs.append(mk(i, "心脾两虚", "舌淡苔薄白", "脉细弱", "失眠多梦，心悸，神疲乏力", "补益心脾", "归脾汤", list(BASE_XIN), vd="2015-05", rnd=rnd))
    for i in range(14, 16):                                   # 气阴两虚 ×2：不足门槛
        cs.append(mk(i, "气阴两虚", "舌红少苔", "脉细数", "口干多饮，乏力", "益气养阴", "生脉散", [("人参", 9), ("麦冬", 12), ("五味子", 6), ("黄芪", 20)], rnd=rnd))
    for i in range(16, 20):                                   # 寒湿痹阻 ×4：含毒性药 + 十八反（乌头+半夏/白及）+ 妊娠禁忌
        cs.append(mk(i, "寒湿痹阻", "舌淡苔白", "脉沉紧", "关节冷痛，遇寒加重", "温经散寒", "乌头汤",
                     [("川乌", 6), ("麻黄", 9), ("白芍", 12), ("黄芪", 15), ("甘草", 6), ("半夏", 9), ("白及", 9)], vd="2023-11", rnd=rnd))
    cs.append({"id": "n1", "kind": "note", "text": "治疗眩晕当先辨虚实，肝阳上亢者以平肝潜阳为主，不可过用温燥。", "group_key": "", "fields": {}, "herbs": [], "visit_date": ""})
    return cs
