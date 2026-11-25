from __future__ import annotations

import pytest

from server.safety.engine import (Context, Item, base_names, evaluate, signing_blockers, summarize)

H, W = "herb", "western"


def herb(i, name, g, **kw):
    return Item(index=i, name=name, kind=H, dose_g=g, **kw)


def west(i, name, dose="1片", freq="qd", days=7, **kw):
    return Item(index=i, name=name, kind=W, dose_text=dose, frequency=freq, days=days, route="口服", **kw)


def ids(issues):
    return {i.rule_id for i in issues}


def has(issues, rule_id, name=None):
    return any(i.rule_id == rule_id and (name is None or i.item_name == name) for i in issues)


HERBAL_META = {"doses_count": 7, "decoction": "水煎400ml", "herbal_usage": "每日1剂"}


# ---------------------------------------------------------------- names
def test_base_names_strip_preparation_prefix():
    assert "甘草" in base_names("炙甘草")
    assert "半夏" in base_names("法半夏")
    assert "黄芪" in base_names("黄芪 (蜜炙)")


# ---------------------------------------------------------------- structural
def test_empty_prescription_blocks():
    assert has(evaluate("herbal", [], Context(), HERBAL_META), "rx.empty")


def test_herbal_missing_dose_and_doses_count_block():
    iss = evaluate("herbal", [herb(0, "黄芪", None)], Context(), {"decoction": "水煎"})
    assert has(iss, "herb.dose_missing") and has(iss, "herb.doses_count_missing")
    assert all(i.severity == "block" for i in iss if i.rule_id in {"herb.dose_missing", "herb.doses_count_missing"})


def test_western_missing_fields_block():
    it = Item(index=0, name="二甲双胍", kind=W)
    iss = evaluate("western", [it], Context(), {})
    assert {"rx.dose_missing", "rx.frequency_missing", "rx.duration_missing"} <= ids(iss)


@pytest.mark.parametrize("dose", ["5mg", "0.5g", "1片", "1/2片", "半片", "10-20mg", "2.5ml", "1 g", "0.5g/次", "40U", "2粒", "100 mg", "1袋", "3滴", "1g/kg", "5毫克"])
def test_sane_western_doses_are_not_flagged(dose):
    assert not has(evaluate("western", [west(0, "氨氯地平", dose)], Context(), {}), "rx.dose_format")


@pytest.mark.parametrize("dose", ["5mg10mg", "适量", "遵医嘱", "mg5", "5", "五五mg", "5 mg mg", "1片2片", "abc", "5mg;10mg"])
def test_garbled_or_vague_western_doses_are_flagged_as_warnings_not_silently_accepted(dose):
    iss = evaluate("western", [west(0, "氨氯地平", dose)], Context(), {})
    f = [i for i in iss if i.rule_id == "rx.dose_format"]
    assert f and f[0].severity == "warning" and dose.strip() in f[0].message


def test_empty_dose_is_a_block_not_a_format_warning():
    iss = evaluate("western", [west(0, "氨氯地平", "")], Context(), {})
    assert has(iss, "rx.dose_missing") and not has(iss, "rx.dose_format")


def test_duplicate_item_flagged():
    iss = evaluate("herbal", [herb(0, "黄芪", 15), herb(1, "黄芪", 10)], Context(), HERBAL_META)
    assert has(iss, "rx.duplicate_item")


# ---------------------------------------------------------------- 十八反 / 十九畏
def test_eighteen_antagonism_licorice_gansui_is_critical():
    iss = evaluate("herbal", [herb(0, "甘草", 6), herb(1, "甘遂", 1)], Context(), HERBAL_META)
    hit = [i for i in iss if i.rule_id == "herb.eighteen_antagonism"]
    assert hit and all(i.severity == "critical" for i in hit)


def test_eighteen_antagonism_wutou_banxia():
    iss = evaluate("herbal", [herb(0, "附子", 6), herb(1, "法半夏", 9)], Context(), HERBAL_META)
    assert has(iss, "herb.eighteen_antagonism")


def test_prepared_licorice_still_antagonises_haizao_group():
    iss = evaluate("herbal", [herb(0, "炙甘草", 6), herb(1, "大戟", 1.5)], Context(), HERBAL_META)
    assert has(iss, "herb.eighteen_antagonism")


def test_nineteen_fear_ginseng_wulingzhi():
    iss = evaluate("herbal", [herb(0, "人参", 6), herb(1, "五灵脂", 9)], Context(), HERBAL_META)
    assert has(iss, "herb.nineteen_fear")


def test_safe_classic_formula_has_no_critical():
    herbs = [herb(0, "黄芪", 20), herb(1, "麦冬", 12), herb(2, "五味子", 6), herb(3, "山药", 15),
             herb(4, "葛根", 15), herb(5, "茯苓", 15), herb(6, "炙甘草", 6)]
    iss = evaluate("herbal", herbs, Context(), HERBAL_META)
    assert summarize(iss)["critical"] == 0 and summarize(iss)["block"] == 0


# ---------------------------------------------------------------- doses
def test_dose_above_range_is_warning_far_above_is_critical():
    warn = evaluate("herbal", [herb(0, "黄连", 7)], Context(), HERBAL_META)          # 上限 5 → 7 = warning
    crit = evaluate("herbal", [herb(0, "黄连", 20)], Context(), HERBAL_META)         # 4× → critical
    assert has(warn, "herb.dose_above") and not has(warn, "herb.dose_far_above")
    assert has(crit, "herb.dose_far_above")


def test_toxic_herb_over_limit_is_critical_quickly():
    iss = evaluate("herbal", [herb(0, "附子", 20)], Context(), HERBAL_META)          # 上限 15, toxic: >1.0× → critical
    assert has(iss, "herb.dose_far_above") and has(iss, "herb.toxic")


def test_restricted_herb_critical():
    assert has(evaluate("herbal", [herb(0, "雄黄", 0.05)], Context(), HERBAL_META), "herb.restricted")


def test_cinnabar_in_decoction_is_flagged_toxic():
    # regression: the 7B TCM model once proposed 朱砂 0.5g in a decoction
    iss = evaluate("herbal", [herb(0, "朱砂", 0.5)], Context(), HERBAL_META)
    assert has(iss, "herb.toxic")


def test_special_method_hint_when_missing():
    iss = evaluate("herbal", [herb(0, "砂仁", 6), herb(1, "龙骨", 20, special="先煎")], Context(), HERBAL_META)
    assert has(iss, "herb.special_method", "砂仁") and not has(iss, "herb.special_method", "龙骨")


def test_unknown_herb_not_in_catalog_warns():
    iss = evaluate("herbal", [herb(0, "神仙草", 10)], Context(), HERBAL_META)
    assert has(iss, "catalog.not_found")


# ---------------------------------------------------------------- pregnancy
def test_pregnancy_forbidden_and_caution():
    ctx = Context(pregnant=True, gender="女", age=30)
    iss = evaluate("herbal", [herb(0, "水蛭", 3), herb(1, "桃仁", 9)], ctx, HERBAL_META)
    assert has(iss, "preg.herb_forbidden", "水蛭") and has(iss, "preg.herb_caution", "桃仁")


def test_pregnancy_western_acei_critical():
    ctx = Context(pregnant=True, gender="女", age=28)
    iss = evaluate("western", [west(0, "依那普利")], ctx, {})
    assert has(iss, "preg.drug_avoid") and [i for i in iss if i.rule_id == "preg.drug_avoid"][0].severity == "critical"


def test_unknown_pregnancy_status_for_woman_of_childbearing_age_asks():
    ctx = Context(pregnant=None, gender="女", age=32)
    iss = evaluate("herbal", [herb(0, "桃仁", 9)], ctx, HERBAL_META)
    assert has(iss, "preg.status_unknown")
    assert not has(evaluate("herbal", [herb(0, "桃仁", 9)], Context(pregnant=None, gender="男", age=40), HERBAL_META),
                   "preg.status_unknown")


# ---------------------------------------------------------------- allergy
def test_penicillin_allergy_blocks_amoxicillin_critical():
    ctx = Context(allergies=["青霉素"])
    iss = evaluate("western", [west(0, "阿莫西林胶囊", "0.5g", "tid")], ctx, {})
    assert has(iss, "allergy.drug") and [i for i in iss if i.rule_id == "allergy.drug"][0].severity == "critical"


def test_cephalosporin_allergy_vs_cefalexin():
    assert has(evaluate("western", [west(0, "头孢氨苄")], Context(allergies=["头孢"]), {}), "allergy.drug")


def test_sulfa_allergy_vs_sulfamethoxazole_but_not_unrelated():
    assert has(evaluate("western", [west(0, "复方新诺明")], Context(allergies=["磺胺类"]), {}), "allergy.drug")
    assert not has(evaluate("western", [west(0, "二甲双胍")], Context(allergies=["磺胺类"]), {}), "allergy.drug")


def test_seafood_allergy_flags_animal_origin_herbs_only():
    iss = evaluate("herbal", [herb(0, "牡蛎", 20, special="先煎"), herb(1, "黄芪", 15)], Context(allergies=["海鲜"]), HERBAL_META)
    assert has(iss, "allergy.animal_herb", "牡蛎") and not has(iss, "allergy.animal_herb", "黄芪")


def test_direct_herb_allergy():
    assert has(evaluate("herbal", [herb(0, "当归", 10)], Context(allergies=["当归"]), HERBAL_META), "allergy.direct")


# ---------------------------------------------------------------- interactions
def test_arb_plus_acei_critical():
    iss = evaluate("western", [west(0, "缬沙坦"), west(1, "依那普利")], Context(), {})
    pair = [i for i in iss if i.rule_id == "ddi.pair"]
    assert pair and pair[0].severity == "critical"


def test_duplicate_statin_same_rx_warns_once():
    iss = evaluate("western", [west(0, "阿托伐他汀"), west(1, "瑞舒伐他汀")], Context(), {})
    assert len([i for i in iss if i.rule_id == "ddi.duplicate_class"]) == 1


def test_duplicate_against_current_med_and_other_rx():
    ctx = Context(current_meds=["氨氯地平 5mg qd"],
                  other_items=[Item(index=0, name="缬沙坦", kind=W)])
    iss = evaluate("western", [west(0, "硝苯地平控释片"), west(1, "厄贝沙坦")], ctx, {})
    assert has(iss, "ddi.duplicate_current_med") and has(iss, "ddi.duplicate_class_other_rx")


def test_warfarin_plus_nsaid_critical():
    iss = evaluate("western", [west(0, "华法林"), west(1, "布洛芬")], Context(), {})
    assert [i for i in iss if i.rule_id == "ddi.pair"][0].severity == "critical"


def test_renal_impairment_metformin():
    iss = evaluate("western", [west(0, "二甲双胍")], Context(renal_impairment=True), {})
    assert has(iss, "organ.renal")


def test_herb_drug_licorice_with_thiazide_and_danshen_with_warfarin():
    ctx = Context(current_meds=["氢氯噻嗪 25mg qd", "华法林 2.5mg"])
    iss = evaluate("herbal", [herb(0, "甘草", 6), herb(1, "丹参", 12)], ctx, HERBAL_META)
    assert has(iss, "hdi.pair", "甘草") and has(iss, "hdi.pair", "丹参")


def test_herb_vs_western_in_other_prescription():
    ctx = Context(other_items=[Item(index=0, name="华法林", kind=W)])
    assert has(evaluate("herbal", [herb(0, "红花", 6)], ctx, HERBAL_META), "hdi.pair")


# ---------------------------------------------------------------- catalog precedence
def test_catalog_dose_range_overrides_builtin():
    ctx = Context(catalog={"黄芪": {"dose_min_g": 5, "dose_max_g": 12, "special_method": ""}})
    iss = evaluate("herbal", [herb(0, "黄芪", 20)], ctx, HERBAL_META)     # builtin max 30 would allow; catalog max 12 flags
    assert has(iss, "herb.dose_above")


# ---------------------------------------------------------------- acknowledgement logic
def test_signing_blockers_ladder():
    iss = evaluate("herbal", [herb(0, "甘草", 6), herb(1, "甘遂", 1), herb(2, "黄连", 7), herb(3, "黄芪", None)],
                   Context(), HERBAL_META)
    blockers = signing_blockers(iss, {})
    kinds = {b["need"] for b in blockers}
    assert kinds == {"fix", "reason", "ack"}
    by_key = {i.key: i for i in iss}
    acks = {}
    for b in blockers:
        if b["need"] == "reason":
            acks[b["key"]] = "经方甘遂半夏汤原方配伍，已与患者沟通并监测"          # written reason
        elif b["need"] == "ack":
            acks[b["key"]] = "ack"
    left = signing_blockers(iss, acks)
    assert {b["need"] for b in left} == {"fix"}                              # only the hard block remains
    # too-short reason is rejected
    for k in list(acks):
        if by_key[k].severity == "critical":
            acks[k] = "ok"
    assert any(b["need"] == "reason" for b in signing_blockers(iss, acks))


def test_issue_keys_are_stable_across_evaluations():
    a = evaluate("herbal", [herb(0, "甘草", 6), herb(1, "甘遂", 1)], Context(), HERBAL_META)
    b = evaluate("herbal", [herb(0, "甘草", 6), herb(1, "甘遂", 1)], Context(), HERBAL_META)
    assert [i.key for i in a] == [i.key for i in b]
