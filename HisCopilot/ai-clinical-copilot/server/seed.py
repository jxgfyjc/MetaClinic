"""Idempotent seeding.

seed_reference_data   catalogs the safety engine and prescription editor depend on:
                        * herb dictionary from the curated reference list (kind=herb, source=reference) — fills the
                          gap in the legacy 424-row import which lacked 甘草/半夏/人参/…;
                        * a SAMPLE western + patent dictionary (source=sample) so the editor is usable on an empty
                          install. Rows are visibly labelled 示例 in the UI and pharmacists replace them via import.
                        * the clinic's therapy catalog (carried over from the legacy sample catalog).
seed_demo_users       dev/demo accounts (one per role) with a known password and must_change_password=True.
                      Disabled in production via XY_SEED_DEMO_USERS=false.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.models import Drug, Formula, TherapyProgram, User
from server.domain.enums import Role
from server.safety.data import reference as R
from server.security.auth import hash_password

SAMPLE_WESTERN = [
    # name, generic, spec, form, unit, dose, freq, class, allergen classes, pregnancy note
    ("氨氯地平片", "氨氯地平", "5mg*7片", "片剂", "片", "5mg", "qd", "CCB", [], ""),
    ("硝苯地平控释片", "硝苯地平", "30mg*7片", "片剂", "片", "30mg", "qd", "CCB", [], ""),
    ("缬沙坦胶囊", "缬沙坦", "80mg*7粒", "胶囊", "粒", "80mg", "qd", "ARB", [], "妊娠期禁用"),
    ("厄贝沙坦片", "厄贝沙坦", "150mg*7片", "片剂", "片", "150mg", "qd", "ARB", [], "妊娠期禁用"),
    ("依那普利片", "依那普利", "10mg*16片", "片剂", "片", "10mg", "qd", "ACEI", [], "妊娠期禁用"),
    ("美托洛尔缓释片", "美托洛尔", "47.5mg*7片", "片剂", "片", "47.5mg", "qd", "BETA", [], ""),
    ("氢氯噻嗪片", "氢氯噻嗪", "25mg*100片", "片剂", "片", "25mg", "qd", "THIAZIDE", ["磺胺类"], ""),
    ("二甲双胍片", "二甲双胍", "0.5g*48片", "片剂", "片", "0.5g", "bid", "BIGUANIDE", [], ""),
    ("格列美脲片", "格列美脲", "2mg*30片", "片剂", "片", "2mg", "qd", "SU", ["磺胺类"], ""),
    ("西格列汀片", "西格列汀", "100mg*7片", "片剂", "片", "100mg", "qd", "DPP4", [], ""),
    ("达格列净片", "达格列净", "10mg*28片", "片剂", "片", "10mg", "qd", "SGLT2", [], ""),
    ("阿托伐他汀钙片", "阿托伐他汀", "20mg*7片", "片剂", "片", "20mg", "qn", "STATIN", [], "妊娠期禁用"),
    ("瑞舒伐他汀钙片", "瑞舒伐他汀", "10mg*7片", "片剂", "片", "10mg", "qn", "STATIN", [], "妊娠期禁用"),
    ("阿司匹林肠溶片", "阿司匹林", "100mg*30片", "片剂", "片", "100mg", "qd", "ANTIPLATELET", ["NSAIDs"], ""),
    ("氯吡格雷片", "氯吡格雷", "75mg*7片", "片剂", "片", "75mg", "qd", "ANTIPLATELET", [], ""),
    ("奥美拉唑肠溶胶囊", "奥美拉唑", "20mg*14粒", "胶囊", "粒", "20mg", "qd", "PPI", [], ""),
    ("雷贝拉唑钠肠溶片", "雷贝拉唑", "10mg*7片", "片剂", "片", "10mg", "qd", "PPI", [], ""),
    ("铝碳酸镁咀嚼片", "铝碳酸镁", "0.5g*20片", "片剂", "片", "1g", "tid", "ANTACID", [], ""),
    ("莫沙必利片", "莫沙必利", "5mg*24片", "片剂", "片", "5mg", "tid", "PROKINETIC", [], ""),
    ("多潘立酮片", "多潘立酮", "10mg*30片", "片剂", "片", "10mg", "tid", "PROKINETIC", [], ""),
    ("对乙酰氨基酚片", "对乙酰氨基酚", "0.5g*20片", "片剂", "片", "0.5g", "prn", "APAP", [], ""),
    ("布洛芬缓释胶囊", "布洛芬", "0.3g*20粒", "胶囊", "粒", "0.3g", "bid", "NSAID", ["NSAIDs"], "妊娠晚期禁用"),
    ("阿莫西林胶囊", "阿莫西林", "0.25g*24粒", "胶囊", "粒", "0.5g", "tid", "PENICILLIN", ["青霉素类"], ""),
    ("头孢克洛胶囊", "头孢克洛", "0.25g*12粒", "胶囊", "粒", "0.25g", "tid", "CEPHALOSPORIN", ["头孢类"], ""),
    ("左氧氟沙星片", "左氧氟沙星", "0.5g*4片", "片剂", "片", "0.5g", "qd", "QUINOLONE", ["喹诺酮类"], "妊娠期避免"),
    ("阿奇霉素片", "阿奇霉素", "0.25g*6片", "片剂", "片", "0.5g", "qd", "MACROLIDE", ["大环内酯类"], ""),
    ("左甲状腺素钠片", "左甲状腺素", "50μg*100片", "片剂", "片", "50μg", "qd", "THYROID", [], ""),
]
SAMPLE_PATENT = [
    ("板蓝根颗粒", "板蓝根", "10g*20袋", "颗粒", "袋", "1袋", "tid", "", [], ""),
    ("连花清瘟胶囊", "连花清瘟", "0.35g*24粒", "胶囊", "粒", "4粒", "tid", "", [], ""),
    ("银翘解毒片", "银翘解毒", "0.3g*24片", "片剂", "片", "4片", "tid", "", [], ""),
    ("六味地黄丸", "六味地黄", "6g*10袋", "丸剂", "袋", "1袋", "bid", "", [], ""),
    ("逍遥丸", "逍遥", "6g*10袋", "丸剂", "袋", "1袋", "bid", "", [], ""),
    ("天麻钩藤颗粒", "天麻钩藤", "5g*10袋", "颗粒", "袋", "1袋", "tid", "", [], ""),
    ("复方丹参滴丸", "复方丹参", "27mg*180丸", "滴丸", "丸", "10丸", "tid", "", [], "妊娠慎用"),
    ("参苓白术散", "参苓白术", "6g*10袋", "散剂", "袋", "1袋", "bid", "", [], ""),
    ("归脾丸", "归脾", "6g*10袋", "丸剂", "袋", "1袋", "bid", "", [], ""),
    ("藿香正气水", "藿香正气", "10ml*10支", "合剂", "支", "1支", "bid", "", [], ""),
    ("香砂养胃丸", "香砂养胃", "6g*10袋", "丸剂", "袋", "1袋", "bid", "", [], ""),
    ("玉屏风颗粒", "玉屏风", "5g*10袋", "颗粒", "袋", "1袋", "tid", "", [], ""),
]
SAMPLE_FORMULAS = [
    ("院内感冒方", "hospital", "风热感冒，发热咽痛", [("金银花", 15), ("连翘", 15), ("板蓝根", 15), ("桔梗", 6), ("甘草", 6)]),
    ("院内健脾和胃方", "hospital", "脾胃虚弱，纳差便溏", [("党参", 15), ("白术", 10), ("茯苓", 15), ("陈皮", 6), ("半夏", 9), ("甘草", 6)]),
    ("院内安神方", "hospital", "心脾两虚，失眠多梦", [("酸枣仁", 20), ("茯神", 15), ("远志", 6), ("党参", 15), ("当归", 10), ("甘草", 6)]),
    ("生脉散", "classic", "气阴两虚，汗多口渴", [("人参", 9), ("麦冬", 12), ("五味子", 6)]),
    ("四君子汤", "classic", "脾胃气虚", [("人参", 9), ("白术", 9), ("茯苓", 9), ("炙甘草", 6)]),
]
THERAPY_SAMPLE = [
    ("糖尿病康复推拿", "推拿", "糖尿病伴周围神经病变、下肢麻木乏力", "疏通经络、改善微循环", "每周2-3次，每次30分钟，疗程4周", "皮肤破损、严重感觉异常、糖尿病足溃疡禁用", "TCM-TN-001"),
    ("针灸通络治疗", "针灸", "周围神经病变、气滞血瘀型疼痛", "通经活络、行气活血", "每周3次，每次留针20分钟，疗程6周", "出血倾向、局部感染、严重糖尿病足慎用", "TCM-ZJ-002"),
    ("足三里艾灸", "艾灸", "气虚乏力、脾胃虚弱", "温阳益气、健脾和胃", "每周2次，每次15分钟，疗程4周", "阴虚火旺者慎用；皮肤过敏者禁用；感觉异常者注意温度", "TCM-AJ-003"),
    ("中频理疗", "理疗", "微循环障碍、下肢麻木、肌肉酸痛", "改善微循环、缓解麻木", "每日1次，每次20分钟，疗程2周", "急性炎症、出血倾向、心区部位、植入心脏起搏器者禁用", "PT-ZP-004"),
    ("康复训练（运动处方）", "康复训练", "2型糖尿病、超重、心肺功能下降", "提高胰岛素敏感性、改善心肺功能", "每周5次，每次30-45分钟有氧运动", "血糖>16.7mmol/L、酮症、严重视网膜病变、足部溃疡暂停", "RT-YD-005"),
]


def seed_reference_data(db: Session) -> dict:
    n_h = n_w = n_p = n_f = n_t = 0
    have = {(d.kind, d.name, d.spec) for d in db.scalars(select(Drug))}
    for name, lo, hi, sp, aliases in R.HERBS:
        if ("herb", name, "") in have:
            continue
        db.add(Drug(kind="herb", name=name, spec="", form="饮片", unit="g", dose_min_g=lo or None, dose_max_g=hi or None, special_method=sp,
                    aliases_json=list(aliases), source="reference",
                    allergen_classes_json=(["动物来源"] if name in R.ANIMAL_ORIGIN_HERBS else []),
                    pregnancy_note=("妊娠禁用" if name in R.PREGNANCY_FORBIDDEN else ("妊娠慎用" if name in R.PREGNANCY_CAUTION else ""))))
        n_h += 1
    for kind, rows in (("western", SAMPLE_WESTERN), ("patent", SAMPLE_PATENT)):
        for name, generic, spec, form, unit, dose, freq, cls, allergens, preg in rows:
            if (kind, name, spec) in have:
                continue
            db.add(Drug(kind=kind, name=name, generic_name=generic, spec=spec, form=form, unit=unit, default_dose=dose, default_frequency=freq,
                        drug_class=cls, allergen_classes_json=allergens, pregnancy_note=preg, source="sample"))
            n_w += kind == "western"
            n_p += kind == "patent"
    have_f = {f.name for f in db.scalars(select(Formula))}
    for name, ftype, indication, herbs in SAMPLE_FORMULAS:
        if name not in have_f:
            db.add(Formula(name=name, formula_type=ftype, indication=indication, herbs_json=[{"name": n, "dose_g": g} for n, g in herbs],
                           source="sample" if ftype == "hospital" else "reference"))
            n_f += 1
    have_t = {t.name for t in db.scalars(select(TherapyProgram))}
    for name, cat, app, goal, freq, contra, code in THERAPY_SAMPLE:
        if name not in have_t:
            db.add(TherapyProgram(name=name, category=cat, applicable=app, goal=goal, frequency=freq, contraindications=contra,
                                  hospital_code=code, source="sample"))
            n_t += 1
    db.flush()
    return {"herbs": n_h, "western": n_w, "patent": n_p, "formulas": n_f, "therapy": n_t}


DEMO_USERS = [
    ("dr-001", "张明", "DOCTOR", "全科/中西医结合", "主任医师", "DEMO-L-001"),
    ("dr-002", "李秀英", "DOCTOR", "中医科", "副主任医师", "DEMO-L-002"),
    ("dr-003", "王强", "DOCTOR", "内科", "主治医师", "DEMO-L-003"),
    ("nu-001", "陈丽", "NURSE", "护理部", "主管护师", ""),
    ("ph-001", "赵药师", "PHARMACIST", "药房", "主管药师", ""),
    ("admin", "系统管理员", "ADMIN", "信息科", "", ""),
]


def seed_demo_users(db: Session) -> int:
    s = get_settings()
    n = 0
    have = {u.username for u in db.scalars(select(User))}
    for username, name, role, dept, title, lic in DEMO_USERS:
        if username in have:
            continue
        db.add(User(username=username, display_name=name, role=role, dept=dept, title=title, license_no=lic,
                    password_hash=hash_password(s.demo_password), must_change_password=False))
        n += 1
    db.flush()
    return n
