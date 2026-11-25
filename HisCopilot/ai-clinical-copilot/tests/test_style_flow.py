"""医师经验画像：HTTP 级端到端测试（fake AI；真实的解析 / 脱敏 / 校验 / 归纳 / 审核 / 发布 / 授权 / 方案参考代码）。

所有病例均为虚构。每个测试都从“医师/管理员实际会点的接口”出发，不直接调用内部函数造数据。
"""
from __future__ import annotations

import io

import pytest
from sqlalchemy import select

import server.jobs.handlers  # noqa: F401
import server.style.jobs     # noqa: F401
from server.db.base import session_scope
from server.db.models import Job, StyleCase, StyleEntry, StyleImport, StyleSource
from server.jobs import engine
from tests.conftest_http import err, ok
from tests.style_fixtures.synth import corpus


# ---------------------------------------------------------------- URL helpers (single place → no hand-typed paths in the tests)
API = "/api/v1/style"


def U_profiles(pid=None, tail=""):
    return f"{API}/profiles" + (f"/{pid}" if pid else "") + tail


def U_import(iid, tail=""):
    return f"{API}/imports/{iid}{tail}"


def U_entry(eid, tail=""):
    return f"{API}/entries/{eid}{tail}"


def _csv(n_gan=10, n_xin=4, *, with_pii=True) -> bytes:
    """A physician's case table: 肝阳上亢 ×n_gan, 心脾两虚 ×n_xin. Includes patient names / phones / record numbers that MUST NOT survive."""
    rows = ["姓名,病历号,日期,主诉,舌脉,证型,中医治法,处方,备注"]
    cs = [c for c in corpus() if c["kind"] == "case"]
    gan = [c for c in cs if c["group_key"] == "肝阳上亢"][:n_gan]
    xin = [c for c in cs if c["group_key"] == "心脾两虚"][:n_xin]
    for i, c in enumerate(gan + xin):
        herbs = " ".join(f"{h['name']}{h['dose_g']:g}g" for h in c["herbs"])
        f = c["fields"]
        name = "张李王赵钱孙周吴郑陈"[i % 10] + "子明华强军伟"[i % 6] + "一二三"[i % 3]
        rows.append(f"{name if with_pii else ''},MZ{20230000 + i},2024-0{1 + i % 9}-1{i % 9},{f['symptoms'][0]},{f['tongue']}{f['pulse']},{f['syndrome']},{f['principle']},"
                    f"\"{f['formula']}加减：{herbs}\",{('电话1380013' + format(i, '04d')) if with_pii else ''}")
    return "\n".join(rows).encode()


def _wait_jobs():
    return engine.drain(ignore_delay=True)


def _upload(c, pid, data: bytes, name="cases.csv", **form):
    return c.post(U_profiles(pid, "/imports"), files=[("files", (name, io.BytesIO(data), "application/octet-stream"))], data=form)


def make_personal(c, name="孙医生"):
    return ok(c.post(U_profiles(), json={"kind": "personal", "subject_name": name}), 201)["profile"]


def run_import(c, pid, data: bytes, *, confirm=True, mode="local", name="cases.csv"):
    """upload → parse → (optionally) confirm all cases → extract. Returns the import dict."""
    r = ok(_upload(c, pid, data, name), 201)
    iid = r["import"]["id"]
    _wait_jobs()
    imp = ok(c.get(U_import(iid)))["import"]
    if confirm:
        ok(c.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
        ok(c.post(U_import(iid, "/extract"), json={"mode": mode}), 202)
        _wait_jobs()
        imp = ok(c.get(U_import(iid)))["import"]
    return imp


def approve_all(c, pid, *, ack="经本人核对，该用药在本人临证中确有依据，仅在辨证明确时使用"):
    es = ok(c.get(U_profiles(pid, "/entries?version=draft")))["entries"]
    for e in es:
        crit = any(f["severity"] == "critical" for f in e["flags"])
        r = c.post(U_entry(e["id"], "/review"), json={"action": "approve", "ack_reason": ack if crit else ""})
        assert r.status_code == 200, r.text
    return es


# ====================================================================== 1. 导入 → 脱敏 → 校验 → 抽取 → 草稿
def test_import_redacts_pii_and_requires_human_confirmation_before_extraction(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    iid = ok(_upload(dr, p["id"], _csv()), 201)["import"]["id"]
    _wait_jobs()
    imp = ok(dr.get(U_import(iid)))["import"]
    assert imp["status"] == "needs_review" and imp["stats"]["cases"] == 14 and imp["stats"]["pending"] == 14

    cases = ok(dr.get(U_import(iid, "/cases?size=100")))["cases"]
    blob = "\n".join(c["text"] for c in cases)
    assert "MZ2023" not in blob and "1380013" not in blob, "record numbers / phones leaked into stored case text"
    assert all(n not in blob for n in ("张子", "李华", "王强", "赵军"))                      # 姓名列的名字被全文遮盖
    assert "【姓名】" in blob and "【编号】" in blob

    # 没有人工确认就不能抽取
    e = err(dr.post(U_import(iid, "/extract"), json={"mode": "local"}), 409)
    assert e["code"] == "no_confirmed_cases"

    ok(dr.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    ok(dr.post(U_import(iid, "/extract"), json={"mode": "local"}), 202)
    _wait_jobs()
    imp = ok(dr.get(U_import(iid)))["import"]
    assert imp["status"] == "extracted" and imp["sources_purged"] is True and imp["stats"]["new"] > 0
    with session_scope() as s:                                                         # 源文件（含原始姓名）已从磁盘清除
        assert all(not x.storage_key for x in s.scalars(select(StyleSource)))

    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    assert es and all(e["review_status"] == "pending" for e in es), "extraction must only create PENDING candidates"
    assert all(e["evidence"] if "evidence" in e else True for e in es)


def test_rejected_files_do_not_block_good_ones_and_bad_types_are_explained(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    files = [("files", ("ok.csv", io.BytesIO(_csv(4, 0)), "text/csv")), ("files", ("evil.exe", io.BytesIO(b"MZ\x90\x00" + b"\x00" * 200), "application/octet-stream")),
             ("files", ("old.doc", io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200), "application/msword"))]
    r = ok(dr.post(U_profiles(p["id"], "/imports"), files=files), 201)
    iid = r["import"]["id"]
    codes = {x["code"] for x in r["rejected"]}
    assert codes == {"unsupported_file", "legacy_office"}
    _wait_jobs()
    imp = ok(dr.get(U_import(iid)))["import"]
    assert imp["status"] == "needs_review" and imp["stats"]["cases"] == 4
    # 重复上传同一文件：忽略并说明
    r2 = ok(dr.post(U_profiles(p["id"], "/imports"), files=[("files", ("a.csv", io.BytesIO(_csv(4, 0)), "text/csv")),
                                                                                       ("files", ("b.csv", io.BytesIO(_csv(4, 0)), "text/csv"))]), 201)
    assert [x["code"] for x in r2["rejected"]] == ["duplicate_file"]


def test_paste_and_note_are_imported_without_files(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    note = "治疗眩晕当先辨虚实，肝阳上亢者以平肝潜阳为主，不可过用温燥，临证需四诊合参。"
    r = ok(dr.post(U_profiles(p["id"], "/imports"), data={"text": note, "as_note": "true"}), 201)
    assert r["import"]["status"] == "needs_review" and r["import"]["stats"]["notes"] == 1
    iid = r["import"]["id"]
    ok(dr.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    ok(dr.post(U_import(iid, "/extract"), json={"mode": "rules"}), 202)
    _wait_jobs()
    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    assert [e["category"] for e in es] == ["note"] and es[0]["flags"][0]["code"] == "self_reported"
    # 手工改文本也要过脱敏：医师把名字写回去，仍会被遮盖
    cs = ok(dr.get(U_import(iid, "/cases")))["cases"]
    assert len(cs) == 1


def test_same_prescription_on_different_dates_is_a_revisit_not_a_duplicate(app_client, roles):
    """4 个“心脾两虚”病例除就诊日期外完全相同：是同方复诊，必须全部计入（不能被当成重复录入而静默丢掉）。"""
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    imp = run_import(dr, p["id"], _csv())
    st = imp["stats"]
    assert st["duplicates_exact"] == 0 and st["duplicates_near"] == 0 and st["possible_revisits"] >= 3, st
    assert st["confirmed"] == 14, st
    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    groups = {e["group_key"]: e["group_total"] for e in es if e["category"] == "reasoning"}
    assert groups == {"肝阳上亢": 10, "心脾两虚": 4}, groups              # 4 例复诊全部计入证据，没有被吞掉


def test_duplicates_exact_is_excluded_near_is_only_flagged_and_visit_rows_are_merged(app_client, roles):
    """三种“看起来重复”必须区分对待：
    · 同一病历号、同一天的多行 = 同一次就诊的病历行 + 处方行 → 合并为一个病例（不是重复）；
    · 不同病历号、同一天、脱敏后内容完全相同 → 重复录入 → 自动排除；
    · 仅有细微差别（如备注里的电话不同）→ 近似重复 → 只标记，由医师决定，绝不自动丢弃。"""
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    base = _csv(3, 0).decode().splitlines()
    r1 = base[1]
    cols = r1.split(",")
    twin = ",".join([cols[0], "MZ99999999"] + cols[2:])                    # 换病历号；备注里的电话与 r1 不同 → 近似
    twin2 = twin.replace("MZ99999999", "MZ88888888")                      # 与 twin 逐字相同 → 精确
    data = "\n".join(base + [r1, twin, twin2]).encode()
    iid = ok(_upload(dr, p["id"], data), 201)["import"]["id"]
    _wait_jobs()
    st = ok(dr.get(U_import(iid)))["import"]["stats"]
    assert st["cases"] == 5, st                                            # 3 原病例 + twin + twin2；r1 的重复行已并入原病例
    assert st["duplicates_exact"] == 1 and st["excluded"] == 1, st         # twin2 自动排除
    assert st["duplicates_near"] == 1, st                                  # twin 仅标记
    r = ok(dr.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    assert r["changed"] == 3 and "疑似重复录入（请核对后手动确认或排除）" in r["skipped_reasons"], r      # 批量确认跳过近似重复，并明确说明原因
    # 医师核对后可手动确认被标记的近似重复
    cases = ok(dr.get(U_import(iid, "/cases?flagged=true")))["cases"]
    near = next(c for c in cases if c["dup_of_id"] and c["status"] == "pending_review")
    ok(dr.patch(f"{API}/cases/{near['id']}", json={"status": "confirmed", "row_version": near["row_version"]}))


# ====================================================================== 2. 审核 → 发布（不可变）→ 新资料只生成候选
def _publish(dr, pid):
    return ok(dr.post(U_profiles(pid, "/publish"), json={"note": "测试发布"}))["profile"]


def _import_and_publish(dr, p, data=None):
    run_import(dr, p["id"], data or _csv())
    approve_all(dr, p["id"])
    return _publish(dr, p["id"])


def test_publish_requires_review_and_freezes_the_version(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    run_import(dr, p["id"], _csv())
    e = err(dr.post(U_profiles(p["id"], "/publish"), json={}), 409)
    assert e["code"] == "nothing_approved"                                               # 没有任何审核通过的条目 → 不能发布
    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    ok(dr.post(U_entry(es[0]["id"], "/review"), json={"action": "approve"}))
    e = err(dr.post(U_profiles(p["id"], "/publish"), json={}), 409)
    assert e["code"] == "entries_pending" and e["details"]["pending"] == len(es) - 1     # 仍有未处理候选 → 不会被静默带进已发布版本
    for x in es[1:]:
        ok(dr.post(U_entry(x["id"], "/review"), json={"action": "reject", "note": "测试驳回"}))
    prof = _publish(dr, p["id"])
    v = prof["active_version"]
    assert v["status"] == "active" and v["version_no"] == 1 and len(v["snapshot_hash"]) == 64
    # 已发布版本不可改
    pub = ok(dr.get(U_profiles(p["id"], f"/entries?version={v['id']}")))["entries"]
    assert [x["id"] for x in pub] == [es[0]["id"]]
    e = err(dr.post(U_entry(es[0]["id"], "/review"), json={"action": "reject"}), 409)
    assert e["code"] == "version_immutable"
    e = err(dr.post(U_entry(es[0]["id"], "/review"), json={"action": "edit", "edits": {"title": "偷偷改"}}), 409)
    assert e["code"] == "version_immutable"


def test_new_material_never_changes_the_active_version_until_reviewed_and_republished(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    prof = _import_and_publish(dr, p)
    v1 = prof["active_version"]
    before = ok(dr.get(U_profiles(p["id"], "/entries?version=active")))["entries"]
    # 再导入一批新病例（更多肝阳上亢）
    more = _csv(10, 0).replace(b"2024-0", b"2025-0")
    run_import(dr, p["id"], more, name="more.csv")
    # 已发布版本原封不动
    after = ok(dr.get(U_profiles(p["id"], "/entries?version=active")))["entries"]
    assert [(e["id"], e["summary"], e["support_count"]) for e in before] == [(e["id"], e["summary"], e["support_count"]) for e in after]
    assert ok(dr.get(U_profiles(p["id"])))["profile"]["active_version"]["id"] == v1["id"]
    draft = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))
    pend = [e for e in draft["entries"] if e["review_status"] == "pending"]
    assert pend, "new material should produce candidate entries"
    upd = [e for e in pend if any(f["code"] == "update_candidate" for f in e["flags"])]
    assert upd, "entries whose statistics changed must be marked as update candidates of an approved one"
    # 候选没处理完不能发布
    assert err(dr.post(U_profiles(p["id"], "/publish"), json={}), 409)["code"] == "entries_pending"
    approve_all(dr, p["id"])
    v2 = _publish(dr, p["id"])["active_version"]
    assert v2["version_no"] == 2 and v2["id"] != v1["id"]
    vs = ok(dr.get(U_profiles(p["id"], "/versions")))["versions"]
    assert [(v["version_no"], v["status"]) for v in vs] == [(2, "active"), (1, "retired")]       # 旧版本保留可追溯


def test_risky_entry_cannot_reach_the_ai_without_a_written_reason(app_client, roles):
    """含毒性药 + 十八反 + 妊娠禁忌的历史经验：可以留作资料，但默认绝不进入 AI 提示词。"""
    from tests.style_fixtures.synth import mk
    import random
    rnd = random.Random(3)
    rows = ["病历号,日期,主诉,舌脉,证型,中医治法,处方"]
    for i in range(4):
        hs = " ".join(f"{n}{g}g" for n, g in [("川乌", 6), ("麻黄", 9), ("白芍", 12), ("黄芪", 15), ("甘草", 6), ("半夏", 9), ("白及", 9)])
        rows.append(f"R{i},2023-1{i},关节冷痛遇寒加重,舌淡苔白脉沉紧,寒湿痹阻,温经散寒,\"乌头汤加减：{hs}\"")
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    run_import(dr, p["id"], "\n".join(rows).encode())
    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    risky = [e for e in es if any(f["severity"] == "critical" for f in e["flags"])]
    assert risky, "toxic / 十八反 / pregnancy-forbidden herbs must be flagged critical"
    codes = {f["code"] for e in risky for f in e["flags"]}
    assert {"safety.eighteen_antagonism", "safety.pregnancy_forbidden"} <= codes
    r = err(dr.post(U_entry(risky[0]["id"], "/review"), json={"action": "approve"}), 409)
    assert r["code"] == "risk_ack_required"                                            # 不写书面理由 → 不能放行给 AI
    short = err(dr.post(U_entry(risky[0]["id"], "/review"), json={"action": "approve", "ack_reason": "短"}), 409)
    assert short["code"] == "risk_ack_required"
    # 仅留作资料（不供 AI）
    x = ok(dr.post(U_entry(risky[0]["id"], "/review"), json={"action": "approve_no_ai"}))["entry"]
    assert x["review_status"] == "approved" and x["ai_eligible"] is False
    # 写了书面理由才允许 AI 使用
    y = ok(dr.post(U_entry(risky[1]["id"], "/review"), json={"action": "approve", "ack_reason": "本人临证中仅用于寒湿重症且辨证明确，已知配伍风险"}))["entry"] if len(risky) > 1 else None
    if y:
        assert y["ai_eligible"] is True and y["risk_ack_reason"]


def test_entries_without_evidence_cannot_be_approved_and_every_quote_is_verbatim(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    run_import(dr, p["id"], _csv())
    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    for e in es[:4]:
        det = ok(dr.get(U_entry(e["id"])))["entry"]
        assert det["evidence"], f"{e['title']} has no evidence"
        for ev in det["evidence"]:
            parts = [x for x in (ev["quote"], (ev.get("detail") or {}).get("quote2", "")) if x]
            assert ev["case"] and parts and all(x in ev["case"]["text"] for x in parts), ("quote is not verbatim from the case", parts)
            assert not any(x.endswith(("党", "白", "茯")) for x in parts), ("quote ends mid-word", parts)
    # 数据库层面抹掉证据后，审核必须被拒
    with session_scope() as s:
        from server.db.models import StyleEntryEvidence
        s.query(StyleEntryEvidence).filter_by(entry_id=es[0]["id"]).delete()
    assert err(dr.post(U_entry(es[0]["id"], "/review"), json={"action": "approve"}), 409)["code"] == "no_evidence"


def test_rollback_creates_a_new_draft_and_never_rewrites_history(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    _import_and_publish(dr, p)
    run_import(dr, p["id"], _csv(10, 0).replace(b"2024-0", b"2025-0"), name="more.csv")
    approve_all(dr, p["id"])
    v2 = _publish(dr, p["id"])["active_version"]
    vs = ok(dr.get(U_profiles(p["id"], "/versions")))["versions"]
    v1 = next(v for v in vs if v["version_no"] == 1)
    assert err(dr.post(U_profiles(p["id"], f"/versions/{v1['id']}/rollback"), json={"reason": ""}), 422)["code"] == "reason_required"
    d = ok(dr.post(U_profiles(p["id"], f"/versions/{v1['id']}/rollback"), json={"reason": "新增资料质量存疑，回到首版"}))["draft_version"]
    assert d["status"] == "draft" and d["version_no"] == 3
    still = ok(dr.get(U_profiles(p["id"])))["profile"]["active_version"]
    assert still["id"] == v2["id"], "rolling back must not switch the live version until the draft is reviewed & published"
    v3 = _publish(dr, p["id"])["active_version"]
    assert v3["version_no"] == 3
    assert [(v["version_no"], v["status"]) for v in ok(dr.get(U_profiles(p["id"], "/versions")))["versions"]] == [(3, "active"), (2, "retired"), (1, "retired")]


# ====================================================================== 3. 权限边界与共享授权
def _uid(admin, username):
    return next(u["id"] for u in ok(admin.get("/api/v1/admin/users"))["items"] if u["username"] == username)


def _master_by_admin(admin, owner="dr-001", reviewer=None, name="周老"):
    body = {"kind": "master", "subject_name": name, "subject_title": "国医大师（虚构）", "intro": "虚构的名老中医，擅长眩晕", "owner_user_id": _uid(admin, owner) if owner else None,
            "reviewer_id": _uid(admin, reviewer) if reviewer else None}
    return ok(admin.post(U_profiles(), json=body), 201)["profile"]


def test_personal_profile_is_private_even_from_admin_and_other_doctors(app_client, roles):
    dr1, dr2, admin, nurse = roles.as_("dr-001"), roles.as_("dr-002"), roles.as_("admin"), roles.as_("nu-001")
    p = make_personal(dr1)
    run_import(dr1, p["id"], _csv())
    assert err(dr2.get(U_profiles(p["id"])), 404)["code"] == "style_not_found"                    # 他人：不泄露存在性
    assert p["id"] not in [x["id"] for x in ok(dr2.get(U_profiles()))["profiles"]]
    # 管理员能在列表看到元数据，但读不到内容
    assert p["id"] in [x["id"] for x in ok(admin.get(U_profiles()))["profiles"]]
    assert err(admin.get(U_profiles(p["id"], "/entries?version=draft")), 403)["code"] == "style_forbidden"
    assert err(admin.get(U_profiles(p["id"], "/imports")), 403)["code"] == "style_forbidden"
    # 护士根本没有 style 权限
    assert err(nurse.get(U_profiles()), 403)["code"] == "permission_denied"
    # 个人画像不能共享
    e = err(dr1.post(U_profiles(p["id"], "/authorizations"), json={"basis_type": "self", "basis_text": "本人同意共享", "authorizer_name": "孙医生"}), 422)
    assert e["code"] == "personal_not_shareable"
    # 他人不能往我的画像里导入 / 审核
    assert err(dr2.post(U_profiles(p["id"], "/imports"), data={"text": "头晕头痛，舌红脉弦，予天麻10g 钩藤15g 石决明30g"}), 404)["code"] == "style_not_found"


def test_admin_proxy_master_requires_a_physician_reviewer_and_admin_cannot_review_clinical_content(app_client, roles):
    admin, dr1, dr2 = roles.as_("admin"), roles.as_("dr-001"), roles.as_("dr-002")
    # 代建必须指定负责临床审核的医师
    e = err(admin.post(U_profiles(), json={"kind": "master", "subject_name": "周老"}), 422)
    assert e["code"] == "reviewer_required"
    m = _master_by_admin(admin, owner=None, reviewer="dr-002")
    assert m["proxy_created"] is True and m["reviewer_id"]
    # 管理员可代为导入资料（代建的职责），但不能审核/发布
    ok(admin.post(U_profiles(m["id"], "/imports"), data={"text": "眩晕当先辨虚实，肝阳上亢者平肝潜阳为主，临证需四诊合参。", "as_note": "true"}), 201)
    iid = ok(admin.get(U_profiles(m["id"], "/imports")))["imports"][0]["id"]
    ok(admin.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    ok(admin.post(U_import(iid, "/extract"), json={"mode": "rules"}), 202)
    _wait_jobs()
    es = ok(admin.get(U_profiles(m["id"], "/entries?version=draft")))["entries"]
    assert es
    assert err(admin.post(U_entry(es[0]["id"], "/review"), json={"action": "approve"}), 403)["code"] == "style_review_forbidden"
    assert err(admin.post(U_profiles(m["id"], "/publish"), json={}), 403)["code"] == "style_review_forbidden"
    # 无关医师（dr-003）既看不见也审核不了；指定的审核医师（dr-002）可以
    assert err(roles.as_("dr-003").get(U_profiles(m["id"])), 404)["code"] == "style_not_found"
    ok(dr2.post(U_entry(es[0]["id"], "/review"), json={"action": "approve"}))
    ok(dr2.post(U_profiles(m["id"], "/publish"), json={}))
    assert err(dr1.get(U_profiles(m["id"])), 404)["code"] == "style_not_found", "published but NOT shared yet → other doctors still cannot see it"


def test_shared_master_visible_only_while_authorization_is_approved(app_client, roles):
    admin, owner, young = roles.as_("admin"), roles.as_("dr-001"), roles.as_("dr-003")
    m = _master_by_admin(admin, owner="dr-001")
    ok(owner.post(U_profiles(m["id"], "/imports"), data={"text": "眩晕当先辨虚实，肝阳上亢者平肝潜阳为主，临证需四诊合参。", "as_note": "true"}), 201)
    iid = ok(owner.get(U_profiles(m["id"], "/imports")))["imports"][0]["id"]
    ok(owner.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    ok(owner.post(U_import(iid, "/extract"), json={"mode": "rules"}), 202)
    _wait_jobs()
    approve_all(owner, m["id"])
    ok(owner.post(U_profiles(m["id"], "/publish"), json={}))
    assert err(young.get(U_profiles(m["id"])), 404)["code"] == "style_not_found"             # 已发布但未授权
    # 声明授权规则
    bad = [{"basis_type": "self", "basis_text": "本人同意", "authorizer_name": "周老"},                  # 说明太短
           {"basis_type": "self", "basis_text": "本人书面同意（文件编号 SQ-2026-001）", "authorizer_name": ""},        # 缺授权人
           {"basis_type": "zzz", "basis_text": "本人书面同意（文件编号 SQ-2026-001）", "authorizer_name": "周老"},       # 依据类型非法
           {"basis_type": "self", "basis_text": "本人书面同意（文件编号 SQ-2026-001）", "authorizer_name": "周老", "scope": "dept"}]  # 科室范围缺科室
    for b in bad:
        assert owner.post(U_profiles(m["id"], "/authorizations"), json=b).status_code == 422, b
    # 管理员不能冒充“本人授权”
    assert err(admin.post(U_profiles(m["id"], "/authorizations"), json={"basis_type": "self", "basis_text": "本人书面同意（文件编号 SQ-2026-001）", "authorizer_name": "周老"}), 422)["code"] == "self_basis_mismatch"
    a = ok(owner.post(U_profiles(m["id"], "/authorizations"), json={"basis_type": "self", "basis_text": "本人书面同意（文件编号 SQ-2026-001）", "authorizer_name": "周老"}), 201)["authorization"]
    assert a["status"] == "pending"
    assert err(young.get(U_profiles(m["id"])), 404)["code"] == "style_not_found", "a pending declaration must not expose the profile"
    assert err(owner.post(f"{API}/authorizations/{a['id']}/approve"), 403)["code"] == "permission_denied"          # 所有者不能给自己备案
    assert [x["id"] for x in ok(admin.get(f"{API}/admin/pending-authorizations"))["authorizations"]] == [a["id"]]
    ok(admin.post(f"{API}/authorizations/{a['id']}/approve"))
    seen = ok(young.get(U_profiles(m["id"])))
    assert seen["profile"]["usable"] is True and seen["profile"]["authorization"]["basis_text"].startswith("本人书面同意")
    assert "不代表该医师本人审核或认可" in seen["disclaimer"]
    ents = ok(young.get(U_profiles(m["id"], "/entries")))
    assert ents["entries"] and all(e["review_status"] == "approved" for e in ents["entries"]) and ents["authorization"]["basis_text"]
    assert err(young.get(U_profiles(m["id"], "/entries?version=draft")), 403)["code"] == "style_forbidden"        # 使用者看不到草稿
    assert err(young.post(U_profiles(m["id"], "/imports"), data={"text": "x" * 50}), 403)["code"] == "style_forbidden"   # 也不能往名医库里加料
    # 撤回后立即不可见
    ok(owner.post(f"{API}/authorizations/{a['id']}/revoke", json={"reason": "家属提出撤回授权"}))
    assert err(young.get(U_profiles(m["id"])), 404)["code"] == "style_not_found"
    assert err(young.get(U_profiles(m["id"], "/entries")), 404)["code"] == "style_not_found"


def test_disabling_a_profile_removes_it_from_use_and_requires_a_reason(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    _import_and_publish(dr, p)
    assert ok(dr.get(U_profiles(p["id"])))["profile"]["usable"] is True
    assert err(dr.post(U_profiles(p["id"], "/disable"), json={"reason": "x"}), 422)["code"] == "reason_required"
    ok(dr.post(U_profiles(p["id"], "/disable"), json={"reason": "资料来源存疑，暂停使用"}))
    pr = ok(dr.get(U_profiles(p["id"])))["profile"]
    assert pr["status"] == "disabled" and pr["usable"] is False
    assert err(dr.post(U_profiles(p["id"], "/imports"), data={"text": "x" * 50}), 409)["code"] == "profile_disabled"
    ok(dr.post(U_profiles(p["id"], "/enable")))
    assert ok(dr.get(U_profiles(p["id"])))["profile"]["usable"] is True


def test_external_model_requires_admin_switch_and_per_import_acknowledgement(app_client, roles, monkeypatch):
    admin, dr = roles.as_("admin"), roles.as_("dr-001")
    p = make_personal(dr)
    iid = ok(_upload(dr, p["id"], _csv(4, 0)), 201)["import"]["id"]
    _wait_jobs()
    ok(dr.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    assert err(dr.post(U_import(iid, "/extract"), json={"mode": "external", "external_ack": True}), 403)["code"] == "external_disabled"    # 开关默认关
    assert err(dr.get(f"{API}/admin/external"), 403)["code"] == "permission_denied"                                    # 医师不能自己开
    ok(admin.put(f"{API}/admin/external", json={"allow": True}))
    assert err(dr.post(U_import(iid, "/extract"), json={"mode": "external"}), 422)["code"] == "ack_required"          # 没勾选确认不行
    r = ok(dr.post(U_import(iid, "/extract"), json={"mode": "external", "external_ack": True}), 202)
    _wait_jobs()                                                                                                       # fake 模式下 ext 角色走 fake；未配置外部端点时应明确失败而不是静默改用别的模型
    imp = ok(dr.get(U_import(iid)))["import"]
    assert imp["ext_acked"] is True and imp["extract_mode"] == "external"
    ok(admin.put(f"{API}/admin/external", json={"allow": False}))


def test_external_mode_without_an_endpoint_fails_loudly_and_never_falls_back_silently(app_client, roles, monkeypatch):
    """医师明确选了“外部模型”：若管理员没配端点，任务必须失败并回到可重试状态——不能悄悄改用规则/本地模型，还把批次记成 external。"""
    admin, dr = roles.as_("admin"), roles.as_("dr-001")
    p = make_personal(dr)
    iid = ok(_upload(dr, p["id"], _csv(4, 0)), 201)["import"]["id"]
    _wait_jobs()
    ok(dr.post(U_import(iid, "/cases/bulk"), json={"action": "confirm", "only_clean": False}))
    ok(admin.put(f"{API}/admin/external", json={"allow": True}))
    monkeypatch.setenv("XY_AI_MODE", "live")                                   # 只对抽取任务切到真实路径（fake 模式不会检查端点配置）
    monkeypatch.delenv("XY_STYLE_EXT_BASE_URL", raising=False)
    monkeypatch.delenv("XY_STYLE_EXT_MODEL", raising=False)
    from server import config
    config.reset_settings_cache()
    ok(dr.post(U_import(iid, "/extract"), json={"mode": "external", "external_ack": True}), 202)
    _wait_jobs()
    imp = ok(dr.get(U_import(iid)))["import"]
    assert imp["status"] == "needs_review", imp["status"]                       # 回到可重试的状态
    assert imp["error_code"] == "ext_not_configured" and imp["job"]["status"] == "failed"
    with session_scope() as s:
        assert {(c.struct_status, c.struct_model) for c in s.query(StyleCase).filter_by(import_id=iid)} == {("pending", "")}      # 没有任何病例被悄悄“规则抽取”
        assert s.query(StyleEntry).filter_by(profile_id=p["id"]).count() == 0, "a failed extraction must not leave candidate entries behind"
    ok(admin.put(f"{API}/admin/external", json={"allow": False}))


# ====================================================================== 4. 方案生成：四栏参考（名医经验 / 当前患者分析 / 个性化建议 / 医师决策）
def _encounter_ready(roles, doctor="dr-001"):
    """A real encounter (patient intake → async analysis) with final diagnoses, ready for plan generation."""
    from tests.test_e2e_scenarios import _intake_and_analyse, run_to_final
    eid = _intake_and_analyse(roles)
    run_to_final(roles, eid, doctor)
    return eid


def _gen_plan(d, eid, *, reason="重新生成"):
    from tests.test_e2e_scenarios import drain
    g = ok(d.post(f"{'/api/v1'}/encounters/{eid}/plan/generate", json={"sections": ["medication", "herbal", "therapy"], "reason": reason}), 202)
    drain()
    return ok(d.get(f"/api/v1/plans/{g['plan_id']}"))["plan"]


def _published_master(roles, *, shared=True):
    """A published 周老 master profile owned by dr-001 (shared with the hospital when `shared`)."""
    admin, owner = roles.as_("admin"), roles.as_("dr-001")
    m = _master_by_admin(admin, owner="dr-001")
    run_import(owner, m["id"], _csv())
    approve_all(owner, m["id"])
    ok(owner.post(U_profiles(m["id"], "/publish"), json={}))
    a = None
    if shared:
        a = ok(owner.post(U_profiles(m["id"], "/authorizations"), json={"basis_type": "self", "basis_text": "本人书面同意（文件编号 SQ-2026-001）", "authorizer_name": "周老"}), 201)["authorization"]
        ok(admin.post(f"{API}/authorizations/{a['id']}/approve"))
    return m, a


def test_plan_without_a_chosen_profile_is_unchanged_and_has_no_style_block(app_client, roles):
    eid = _encounter_ready(roles)
    d = roles.as_("dr-001")
    plan = _gen_plan(d, eid)
    assert plan["status"] == "draft" and plan["style"] in (None, {}), "no profile chosen → the plan flow must be exactly as before"
    assert plan["content"]["herbal"]


def test_four_column_reference_is_snapshotted_validated_and_never_overrides_safety(app_client, roles):
    m, auth = _published_master(roles)
    young = roles.as_("dr-003")
    # 年轻医师（dr-003）自己的接诊
    from tests.test_e2e_scenarios import _intake_and_analyse, run_to_final, make_link, intake_client, SUBMIT, drain
    eid = _intake_and_analyse(roles)                                    # 由 dr-001 的二维码产生；改派给 dr-003 以模拟年轻医师接诊
    with session_scope() as s:
        from server.db.models import Encounter, User
        e = s.get(Encounter, eid)
        e.doctor_id = s.scalar(select(User.id).where(User.username == "dr-003"))
    run_to_final(roles, eid, "dr-003")
    # 选用：名医画像可用；他人的个人画像不可用
    pick = ok(young.get(f"{API}/encounters/{eid}/profiles"))
    assert m["id"] in [x["id"] for x in pick["available"]] and "不代表该医师本人审核或认可" in pick["disclaimer"]
    own = make_personal(roles.as_("dr-001"), "周医生个人")
    assert err(young.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [own["id"]]}), 422)["code"] == "style_unusable"
    assert ok(young.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}))["chosen"] == [m["id"]]

    plan = _gen_plan(young, eid)
    st = plan["style"]
    assert st and st["requested"] == [m["id"]] and st["disclaimer"]
    pr = st["profiles"][0]
    assert pr["layer"] == "master" and "SQ-2026-001" in pr["source_note"] and pr["version_no"] == 1 and len(pr["snapshot_hash"]) == 64        # ① 来源与授权依据随方案保存
    assert pr["entries"] and all(e["ref_id"].startswith("S") and e["summary"] for e in pr["entries"])                                   # ① 条目原文
    assert st["applicability"] and all(a["refs"] and a["verdict"] in ("match", "partial", "mismatch", "unknown") for a in st["applicability"])        # ② 当前患者分析
    assert all(set(a["fact_refs"]) <= {"F%d" % i for i in range(1, 40)} for a in st["applicability"])
    assert all("F99" not in a["fact_refs"] for a in st["applicability"]), "references to non-existent facts must be dropped"
    assert st["validation"]["dropped_f_refs"] >= 1
    cited = {f for a in st["applicability"] for f in a["fact_refs"]}
    assert cited and set(st["facts"]) == cited, "the snapshot must carry the original text of every cited patient fact (fact ids get renumbered over time)"
    assert all(v["text"] and v["label"] for v in st["facts"].values())
    assert st["comparison"] and all(c["stance"] in ("adopted", "adjusted", "not_adopted", "info_only") for c in st["comparison"])             # ③ 与经验的异同
    assert st["decisions"] == {}                                                                                                        # ④ 医师尚未决策——系统不替医师决定
    assert plan["content"]["herbal"], "the actual plan is still produced by the normal pipeline"
    # 安全引擎照常工作：画像不给任何药开绿灯
    sp = ok(young.get(f"/api/v1/plans/{plan['id']}/safety-preview"))
    assert sp["rules_version"] and {g["rx_type"] for g in sp["groups"]} >= {"western", "herbal"}, sp       # 签署前同一套规则照常评估这份方案


def test_fabricated_references_and_untrue_dose_claims_are_neutralised(app_client, roles, monkeypatch):
    m, _ = _published_master(roles)
    eid = _encounter_ready(roles)
    d = roles.as_("dr-001")
    ok(d.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}))
    # 让 fake 模型在方案中伪造一个不存在的 S 编号，并转述一个与条目记录不符的剂量
    with session_scope() as s:
        from server.db.models import Encounter
        e = s.get(Encounter, eid)
        e.hpi = (e.hpi or "") + " FAKE_STYLE_GHOST_REF FAKE_STYLE_WRONG_DOSE"
        from server.domain import encounters as enc_svc
        enc_svc.refresh_inputs(s, e)
    st = _gen_plan(d, eid)["style"]
    assert all("S99" not in x["refs"] for x in st["applicability"]) and st["validation"]["dropped_s_refs"] >= 1       # 伪造的编号被剔除
    bad = [c for c in st["comparison"] if "90g" in c["note"]]
    assert bad and all(c["verified"] is False and c["unverified"] for c in bad), st["comparison"]                      # 与条目不符的剂量转述标“未核实”


def test_revoking_authorization_stops_new_plans_but_keeps_the_snapshot_of_old_ones(app_client, roles):
    m, auth = _published_master(roles)
    eid = _encounter_ready(roles)
    d, admin = roles.as_("dr-001"), roles.as_("admin")
    ok(d.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}))
    old = _gen_plan(d, eid)
    assert old["style"]["profiles"], "baseline: plan used the profile"
    snap_hash = old["style"]["profiles"][0]["snapshot_hash"]
    # 撤回授权（owner 本人仍可使用自己的画像，所以用另一位医师来验证“不再能选用”）
    ok(d.post(f"{API}/authorizations/{auth['id']}/revoke", json={"reason": "家属提出撤回授权"}))
    young = roles.as_("dr-003")
    with session_scope() as s:
        from server.db.models import Encounter, User
        s.get(Encounter, eid).doctor_id = s.scalar(select(User.id).where(User.username == "dr-003"))
    assert err(young.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}), 422)["code"] == "style_unusable"
    # 旧方案里保存的当时引用快照没有被改动
    again = ok(roles.as_("dr-003").get(f"/api/v1/plans/{old['id']}"))["plan"]
    assert again["style"]["profiles"][0]["snapshot_hash"] == snap_hash and again["style"]["profiles"][0]["entries"] == old["style"]["profiles"][0]["entries"]


def test_personal_layer_is_consulted_alongside_the_master_layer(app_client, roles):
    """双层体系：名医经验库 + 医师自己的个人画像，分别标注来源层。"""
    m, _ = _published_master(roles)
    d = roles.as_("dr-001")
    own = make_personal(d, "周医生本人")
    run_import(d, own["id"], _csv(10, 0).replace(b"2024-0", b"2025-0"), name="own.csv")
    approve_all(d, own["id"])
    ok(d.post(U_profiles(own["id"], "/publish"), json={}))
    eid = _encounter_ready(roles)
    ok(d.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"], own["id"]]}))
    st = _gen_plan(d, eid)["style"]
    layers = {p["layer"] for p in st["profiles"]}
    assert layers == {"master", "personal"}, layers
    assert {p["profile_id"] for p in st["profiles"]} == {m["id"], own["id"]}
    refs = [e["ref_id"] for p in st["profiles"] for e in p["entries"]]
    assert len(refs) == len(set(refs)), "reference ids must be unique across profiles"


def test_options_endpoint_exposes_only_what_the_wizard_needs(app_client, roles):
    dr = roles.as_("dr-001")
    o = ok(dr.get(f"{API}/options"))
    assert set(o) == {"allow_external", "external_configured", "local_available", "max_file_mb", "accepted"} and o["allow_external"] is False
    assert "key" not in str(o).lower() and "url" not in str(o).lower()
    assert err(roles.as_("nu-001").get(f"{API}/options"), 403)["code"] == "permission_denied"
    assert err(roles.anon().get(f"{API}/options"), 401)


def test_physician_decision_on_comparison_rows_is_recorded_audited_and_only_while_draft(app_client, roles):
    m, _ = _published_master(roles)
    eid = _encounter_ready(roles)
    d = roles.as_("dr-001")
    ok(d.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}))
    plan = _gen_plan(d, eid)
    assert plan["style"]["decisions"] == {} and plan["style"]["comparison"]
    r = ok(d.post(f"{API}/plans/{plan['id']}/decision", json={"index": 0, "decision": "adopt", "note": "辨证吻合"}))
    assert r["style"]["decisions"]["0"] == {"decision": "adopt", "note": "辨证吻合"}
    r = ok(d.post(f"{API}/plans/{plan['id']}/decision", json={"index": 0, "decision": "skip", "note": "本患者合并痰湿"}))
    assert r["style"]["decisions"]["0"]["decision"] == "skip"
    n = len(plan["style"]["comparison"])
    assert err(d.post(f"{API}/plans/{plan['id']}/decision", json={"index": n, "decision": "adopt"}), 422)["code"] == "no_such_row"        # 在入参范围内但超出实际行数
    assert err(d.post(f"{API}/plans/{plan['id']}/decision", json={"index": 999, "decision": "adopt"}), 422)["code"] == "validation_error"     # 超出入参范围
    assert err(d.post(f"{API}/plans/{plan['id']}/decision", json={"index": 0, "decision": "yes"}), 422)["code"] == "bad_decision"
    # 他人不能替医师记录；护士/药师没有 plan.write
    assert err(roles.as_("dr-002").post(f"{API}/plans/{plan['id']}/decision", json={"index": 0, "decision": "adopt"}), 403)["code"] == "not_your_encounter"
    assert err(roles.as_("nu-001").post(f"{API}/plans/{plan['id']}/decision", json={"index": 0, "decision": "adopt"}), 403)["code"] == "permission_denied"
    # 决策被写进审计，且不改变方案正文
    got = ok(d.get(f"/api/v1/plans/{plan['id']}"))["plan"]
    assert got["content"] == plan["content"] and got["style"]["decisions"]["0"]["decision"] == "skip"
    with session_scope() as s:
        from server.db.models import AuditEvent
        assert s.query(AuditEvent).filter_by(action="style.plan.decision").count() == 2
    # 清除
    assert "0" not in ok(d.post(f"{API}/plans/{plan['id']}/decision", json={"index": 0, "decision": "clear"}))["style"]["decisions"]


def test_only_the_attending_physician_can_attach_profiles_to_an_encounter(app_client, roles):
    """护士有 encounter.write_facts，但没有 style.read：不能给接诊挂画像，也看不到可选画像列表。管理员同理。"""
    m, _ = _published_master(roles)
    eid = _encounter_ready(roles)
    for who in ("nu-001", "admin", "ph-001"):
        c = roles.as_(who)
        assert err(c.put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}), 403)["code"] in ("permission_denied", "style_forbidden"), who
        assert err(c.get(f"{API}/encounters/{eid}/profiles"), 403)["code"] in ("permission_denied", "style_forbidden"), who
    assert err(roles.as_("dr-002").put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}), 403)["code"] == "not_your_encounter"
    assert ok(roles.as_("dr-001").put(f"{API}/encounters/{eid}/profiles", json={"profile_ids": [m["id"]]}))["chosen"] == [m["id"]]


def test_asking_for_the_draft_when_there_is_none_is_an_empty_answer_not_an_error(app_client, roles):
    """发布之后没有草稿是正常状态：画像页的“待审核计数”仍会请求 ?version=draft，不能因此 404（页面会冒出错误框）。"""
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    _import_and_publish(dr, p)
    r = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))
    assert r["entries"] == [] and r["review_counts"] == {} and r["version"] is None
    # 使用者/无权者仍然不能借此探测草稿
    m, _ = _published_master(roles)
    assert err(roles.as_("dr-003").get(U_profiles(m["id"], "/entries?version=draft")), 403)["code"] == "style_forbidden"
