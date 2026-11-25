"""Job engine + AI pipeline behaviour (fake model backend; real parsing/validation/persistence)."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

import server.jobs.handlers  # noqa: F401  (register handlers)
from server.ai.client import ModelError, extract_json
from server.db.base import session_scope
from server.db.models import (Analysis, AnalysisModule, Artifact, Diagnosis, Job, JobEvent, LabItem, TreatmentPlan)
from server.domain import encounters as enc_svc
from server.domain import plans as plan_svc
from server.domain.enums import AnalysisStatus, ArtifactKind, DxStatus, JobStatus, PlanStatus, ProcStatus
from server.domain.ids import utcnow
from server.jobs import engine
from server.jobs.handlers import parse_lab_text
from server.security.audit import Actor


@pytest.fixture()
def world(db, make_user):
    doc = make_user("dr-j", name="张医生")
    a = Actor.staff(doc)
    p, _ = enc_svc.create_patient(db, a, name="孙测试", gender="男", age=50, phone="13900003333")
    enc = enc_svc.create_encounter(db, a, patient=p, doctor_id=doc.id, chief_complaint="头痛3天", hpi="伴眩晕、乏力")
    db.commit()
    return type("W", (), dict(doc=doc, actor=a, patient=p, enc=enc))


def new_analysis(db, w, note=""):
    if note:
        w.enc.hpi = w.enc.hpi + " " + note
        enc_svc.refresh_inputs(db, w.enc)
    an = Analysis(encounter_id=w.enc.id, status="queued", trigger="manual", inputs_version=w.enc.inputs_version, inputs_hash=w.enc.inputs_hash)
    db.add(an); db.flush()
    j = engine.enqueue("analysis", {"analysis_id": an.id}, subject_type="analysis", subject_id=an.id, encounter_id=w.enc.id, session=db)
    db.commit()
    return an, j


# ============================================================ extract_json
def test_extract_json_variants():
    assert extract_json('{"a":1}') == {"a": 1}
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('让我想想…先分析。\n{"a":{"b":2}}\n以上。') == {"a": {"b": 2}}
    with pytest.raises(ModelError) as e:
        extract_json('{"a": [1,2')
    assert e.value.code == "truncated"
    with pytest.raises(ModelError) as e:
        extract_json("no json here")
    assert e.value.code == "bad_output"
    with pytest.raises(ModelError):
        extract_json("  ")


# ============================================================ engine mechanics
def test_enqueue_is_idempotent_by_key(db):
    a = engine.enqueue("noop", {"x": 1}, idempotency_key="k1", session=db); db.commit()
    b = engine.enqueue("noop", {"x": 1}, idempotency_key="k1"); assert a.id == b.id
    assert db.scalar(select(Job).where(Job.idempotency_key == "k1")) is not None and len(db.scalars(select(Job)).all()) == 1


def test_success_failure_retry_and_terminal_states(db):
    calls = {"n": 0}

    @engine.register("flaky", resource="light")
    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise engine.JobError("temp", "临时失败", retryable=True)
        return {"ok": True, "attempt": ctx.attempt}

    j = engine.enqueue("flaky", {}, max_attempts=2, session=db); db.commit()
    engine.drain()
    db.expire_all(); j = db.get(Job, j.id)
    assert j.status == JobStatus.QUEUED.value and j.attempts == 1 and j.error_code == "temp" and j.run_after > utcnow()   # backoff scheduled
    j.run_after = utcnow(); db.commit()
    engine.drain(); db.expire_all(); j = db.get(Job, j.id)
    assert j.status == JobStatus.SUCCEEDED.value and j.result_json == {"ok": True, "attempt": 2} and j.error_code == ""
    evs = [e.message for e in db.scalars(select(JobEvent).where(JobEvent.job_id == j.id).order_by(JobEvent.id))]
    assert any("自动重试" in m for m in evs) and any("执行成功" in m for m in evs)


def test_non_retryable_and_max_attempts_end_failed(db):
    @engine.register("hard_fail", resource="light")
    def hard(ctx):
        raise engine.JobError("nope", "不可恢复", retryable=False)

    @engine.register("always_temp", resource="light")
    def always(ctx):
        raise engine.JobError("temp", "总是临时失败", retryable=True)

    j1 = engine.enqueue("hard_fail", {}, session=db); j2 = engine.enqueue("always_temp", {}, max_attempts=2, session=db); db.commit()
    engine.drain()
    for j in (j1, j2):
        db.expire_all(); jj = db.get(Job, j.id)
        if jj.status == JobStatus.QUEUED.value:
            jj.run_after = utcnow(); db.commit(); engine.drain(); db.expire_all(); jj = db.get(Job, j.id)
        assert jj.status == JobStatus.FAILED.value and jj.error_detail
    assert db.get(Job, j2.id).attempts == 2


def test_unexpected_exception_recorded_with_traceback(db):
    @engine.register("boom", resource="light")
    def boom(ctx):
        raise ZeroDivisionError("x")

    j = engine.enqueue("boom", {}, session=db); db.commit(); engine.drain(); db.expire_all()
    jj = db.get(Job, j.id)
    assert jj.status == "failed" and jj.error_code == "unexpected_error" and "ZeroDivisionError" in jj.error_detail


def test_cancel_queued_and_manual_retry_creates_no_duplicate(db):
    @engine.register("slowish", resource="light")
    def s(ctx):
        return {}

    j = engine.enqueue("slowish", {}, session=db); db.commit()
    assert engine.cancel(j.id); db.expire_all(); assert db.get(Job, j.id).status == "cancelled"
    assert engine.retry(j.id) is not None; db.expire_all()
    engine.drain(); db.expire_all()
    assert db.get(Job, j.id).status == "succeeded" and len(db.scalars(select(Job)).all()) == 1


def test_recover_requeues_stale_running_and_fails_exhausted(db):
    a = engine.enqueue("noop2", {}, session=db); b = engine.enqueue("noop2", {}, max_attempts=1, session=db); db.commit()
    for j, att in ((a, 1), (b, 1)):
        jj = db.get(Job, j.id); jj.status, jj.attempts, jj.heartbeat_at, jj.locked_by = "running", att, utcnow() - timedelta(minutes=5), "dead-worker"
    db.commit()
    assert engine.recover(stale_after_s=30) == 2; db.expire_all()
    assert db.get(Job, a.id).status == "queued" and db.get(Job, b.id).status == "failed" and db.get(Job, b.id).error_code == "interrupted"
    # a fresh heartbeat is NOT recovered
    c = engine.enqueue("noop2", {}, session=db); db.commit()
    jc = db.get(Job, c.id); jc.status, jc.heartbeat_at = "running", utcnow(); db.commit()
    assert engine.recover(stale_after_s=30) == 0


def test_claim_is_exclusive(db):
    j = engine.enqueue("noop3", {}, session=db); db.commit()
    first = engine._claim("light", "w1"); second = engine._claim("light", "w2")
    assert first is not None and first.id == j.id and second is None


# ============================================================ analysis pipeline
def test_analysis_success_creates_pending_candidates_and_drops_fabricated_refs(db, world):
    an, j = new_analysis(db, world, "FAKE_GHOST_REF")                      # model cites F99 which does not exist
    engine.drain(); db.expire_all()
    an = db.get(Analysis, an.id)
    assert an.status == AnalysisStatus.SUCCEEDED.value and an.inputs_hash == world.enc.inputs_hash
    mods = {m.module: m for m in an.modules}
    assert mods["western"].status == "succeeded" and mods["tcm"].status == "succeeded" and mods["tongue"].status == "skipped"
    assert mods["western"].output_json["validation"]["dropped_refs"] == 1                       # ghost ref removed
    dxs = db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == world.enc.id)).all()
    assert {d.module for d in dxs} == {"western", "tcm"} and all(d.status == "pending" for d in dxs)
    weak = [d for d in dxs if d.evidence_json.get("evidence_weak")]
    assert weak and all(d.name == "原发性高血压" for d in weak)                                  # lost its only (fabricated) citation → flagged
    assert any(f["id"] == "F1" for f in an.facts_json)                                          # fact sheet persisted with the run


def test_analysis_bad_json_module_fails_but_run_is_partial_or_failed_visibly(db, world):
    an, j = new_analysis(db, world, "FAKE_BAD_JSON")
    engine.drain(); db.expire_all()
    an = db.get(Analysis, an.id); jj = db.get(Job, j.id)
    assert an.status == AnalysisStatus.FAILED.value and an.error_code == "all_modules_failed"
    assert {m.module: m.status for m in an.modules}["western"] == "failed" and an.modules[0].error_detail
    # retryable → job re-queued with backoff, not silently dropped
    assert jj.status == JobStatus.QUEUED.value and jj.attempts == 1


def test_western_module_tolerates_common_key_variants_the_model_may_use(db, world):
    """Real LLMs do not always use the literal key/field names. `differential_diagnoses[].diagnosis` must still become candidates."""
    an, j = new_analysis(db, world, "FAKE_VARIANT_KEYS")
    engine.drain(); db.expire_all()
    names = {d.name for d in db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == world.enc.id, Diagnosis.module == "western"))}
    assert names == {"原发性高血压", "偏头痛"}


def test_valid_json_without_a_candidate_list_fails_visibly_and_keeps_the_raw_excerpt(db, world):
    """Seen with the real 27B: a parseable object but no usable `differentials`. The module must (a) retry once, (b) fail with a code,
    (c) NOT invent diagnoses, (d) keep what the model said so the run can be diagnosed afterwards."""
    an, j = new_analysis(db, world, "FAKE_NO_DIFFERENTIALS")
    engine.drain(); db.expire_all()
    an = db.get(Analysis, an.id)
    west = next(m for m in an.modules if m.module == "western")
    assert west.status == "failed" and west.error_code == "no_candidates"
    assert "no differentials here" in (west.output_json or {}).get("failure_raw_excerpt", "")
    assert not db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == world.enc.id, Diagnosis.module == "western")).all()
    tcm = next(m for m in an.modules if m.module == "tcm")
    assert tcm.status == "succeeded" and an.status == AnalysisStatus.PARTIAL.value                    # the other module still delivers


def test_missing_candidate_list_is_retried_once_before_giving_up(monkeypatch):
    from server.ai import analysis as ana
    from server.ai.client import LLMResult
    from server.ai.facts import Fact
    calls = []
    def fake_chat_json(system, user, **kw):
        calls.append(user)
        out = {"summary": "x"} if len(calls) == 1 else {"differentials": [{"name": "2型糖尿病", "confidence": "medium", "rationale": "r", "supporting": [{"text": "口渴", "refs": ["F1"]}]}]}
        return out, LLMResult("", "m", 1)
    monkeypatch.setattr(ana, "chat_json", fake_chat_json)
    out = ana.run_western([Fact("F1", "主诉", "口渴", "doctor")], [])
    assert len(calls) == 2 and "differentials" in calls[1] and "重要提醒" in calls[1]
    assert [c["name"] for c in out["candidates"]] == ["2型糖尿病"]


def test_analysis_model_down_fails_with_clear_reason_then_manual_recovery(db, world):
    an, j = new_analysis(db, world, "FAKE_FAIL_LLM")
    for _ in range(3):
        engine.drain(); db.expire_all()
        jj = db.get(Job, j.id)
        if jj.status == "queued":
            jj.run_after = utcnow(); db.commit()
    jj = db.get(Job, j.id); an = db.get(Analysis, an.id)
    assert jj.status == "failed" and "不可用" in jj.error_detail and an.status == "failed"          # domain row mirrors the job
    assert not db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == world.enc.id)).all()   # NO fabricated results
    # model returns → clinician retries the SAME job; no duplicate analysis row
    world.enc.hpi = world.enc.hpi.replace(" FAKE_FAIL_LLM", "")
    enc_svc.refresh_inputs(db, world.enc); db.commit()
    engine.retry(j.id); engine.drain(); db.expire_all()
    assert db.get(Analysis, an.id).status == "succeeded" and len(db.scalars(select(Analysis)).all()) == 1


def test_insufficient_facts_refuses_to_analyse(db, make_user):
    doc = make_user("dr-k"); a = Actor.staff(doc)
    p, _ = enc_svc.create_patient(db, a, name="空白"); enc = enc_svc.create_encounter(db, a, patient=p, doctor_id=doc.id); db.commit()
    class W: pass
    w = W(); w.enc = enc
    an, j = new_analysis(db, w); engine.drain(); db.expire_all()
    assert db.get(Analysis, an.id).status == "failed" and db.get(Analysis, an.id).error_code == "insufficient_facts"


def test_reanalysis_supersedes_unreviewed_candidates_but_keeps_reviewed(db, world):
    an1, _ = new_analysis(db, world); engine.drain(); db.expire_all()
    first = db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == world.enc.id)).all()
    keep = next(d for d in first if d.module == "western")
    plan_svc.review_diagnosis(db, world.actor, keep, action="accept"); db.commit()
    an2, _ = new_analysis(db, world, "新增症状：恶心"); engine.drain(); db.expire_all()
    keep = db.get(Diagnosis, keep.id)
    assert keep.status == "accepted" and keep.analysis_id == an1.id                                 # reviewed row untouched
    olds = [d for d in db.scalars(select(Diagnosis).where(Diagnosis.analysis_id == an1.id)) if d.id != keep.id]
    assert olds and all(d.status == DxStatus.SUPERSEDED.value for d in olds)
    assert any(d.analysis_id == an2.id and d.status == "pending" for d in db.scalars(select(Diagnosis)))


# ============================================================ plan generation
def prepared(db, w):
    an, _ = new_analysis(db, w); engine.drain(); db.expire_all()
    for d in db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == w.enc.id)):
        plan_svc.review_diagnosis(db, w.actor, d, action="accept")
    db.commit()


def make_plan_job(db, w, sections=("medication", "herbal", "therapy")):
    plan = plan_svc.new_plan_shell(db, w.actor, w.enc, sections=list(sections), ai_used=True)
    j = engine.enqueue("plan", {"plan_id": plan.id}, subject_type="plan", subject_id=plan.id, encounter_id=w.enc.id, session=db); db.commit()
    return plan, j


def test_plan_is_one_coherent_plan_over_all_diagnoses(db, world):
    prepared(db, world)
    plan, j = make_plan_job(db, world); engine.drain(); db.expire_all()
    plan = db.get(TreatmentPlan, plan.id)
    assert plan.status == PlanStatus.DRAFT.value and len(db.scalars(select(TreatmentPlan)).all()) == 1       # NOT one per diagnosis
    assert len(plan.dx_snapshot_json) == 3                                                                   # all accepted diagnoses feed ONE plan
    c = plan.content_json
    assert c["medication"]["western"][0]["name"] == "氨氯地平" and len(c["herbal"]) == 1 and len(c["herbal"][0]["herbs"]) == 11
    assert plan.therapy_items and plan.therapy_items[0].status == "pending" and plan.therapy_items[0].acupoints_json == ["太冲", "风池", "百会"]
    assert plan.ai_original_json["raw"]                                                                      # AI original preserved separately


def test_plan_normalisation_flags_gaps_instead_of_guessing(db):
    raw = {"medication": {"western": [{"name": "未知神药", "dose": "", "frequency": "", "days": None}], "patent": []},
           "herbal": [{"title": "x", "herbs": [{"name": "黄芪", "dose_g": "abc"}, {"name": "砂仁", "dose_g": 6}, {"name": "砂仁", "dose_g": 9}]}],
           "therapy": [{"category": "针灸", "name": "伪造院内项目", "source_type": "hospital", "acupoints": ["足三里", "火星穴"]}]}
    from server.ai import plan_gen
    c, th = plan_gen.normalise(raw, {}, [], ["medication", "herbal", "therapy"])
    w = c["medication"]["western"][0]
    assert "not_in_catalog" in w["flags"] and {"missing_dose_text", "missing_frequency", "missing_days"} <= set(w["flags"]) and w["dose_text"] == ""
    herbs = c["herbal"][0]["herbs"]
    assert [h["name"] for h in herbs] == ["黄芪", "砂仁"] and herbs[0]["dose_g"] is None and "missing_dose" in herbs[0]["flags"]   # deduped, no guessed dose
    assert herbs[1]["special"] == "后下"                                                                      # default decoction method filled from reference
    assert th[0]["source_type"] == "ai"                                                                      # fake 'hospital' item downgraded
    assert any("火星穴" in n for n in th[0]["needs_confirmation"]) and th[0]["program_id"] is None


def test_plan_model_failure_marks_plan_failed_with_reason(db, world):
    prepared(db, world)
    world.enc.hpi += " FAKE_FAIL_LLM"; enc_svc.refresh_inputs(db, world.enc); db.commit()          # trips fake failure inside plan prompt (facts include hpi)
    plan = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["herbal"], ai_used=True)
    j = engine.enqueue("plan", {"plan_id": plan.id}, max_attempts=1, session=db); db.commit()
    engine.drain(); db.expire_all()
    plan = db.get(TreatmentPlan, plan.id)
    assert plan.status == PlanStatus.FAILED.value and plan.error_code and "不可用" in plan.error_detail
    assert plan.sections_state_json["herbal"]["status"] == "failed" and plan.content_json["herbal"] == []


def test_plan_superseded_while_generating_is_discarded(db, world):
    prepared(db, world)
    plan1, j1 = make_plan_job(db, world)
    plan2 = plan_svc.new_plan_shell(db, world.actor, world.enc, sections=["medication"], ai_used=False); db.commit()           # supersedes plan1
    engine.drain(); db.expire_all()
    assert db.get(Job, j1.id).status in ("failed", "succeeded") and db.get(TreatmentPlan, plan1.id).status == "superseded"
    assert db.get(TreatmentPlan, plan2.id).status == "draft"                                                                  # live plan untouched


# ============================================================ artifacts
def add_artifact(db, w, kind, data=b"x", **kw):
    from server.config import get_settings
    key = f"t_{kind}_{len(data)}_{utcnow().timestamp()}"
    p = get_settings().objects_dir / key; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    a = Artifact(kind=kind, encounter_id=w.enc.id, patient_id=w.patient.id, storage_key=key, thumb_key=key, original_name=kw.get("name", "f.wav"),
                 mime=kw.get("mime", "audio/wav"), size_bytes=len(data), sha256=str(abs(hash(data + key.encode()))))
    db.add(a); db.flush()
    j = engine.enqueue("asr" if kind == "audio" else "ocr" if kind == "lab_image" else "tongue", {"artifact_id": a.id}, session=db); db.commit()
    return a, j


def test_asr_success_changes_inputs_and_failure_is_visible_with_retry(db, world):
    v0 = world.enc.inputs_version
    a, j = add_artifact(db, world, "audio", b"RIFFxxxxWAVEdata")
    engine.drain(); db.expire_all(); a = db.get(Artifact, a.id); enc = db.get(type(world.enc), world.enc.id)
    assert a.proc_status == "succeeded" and "头痛" in a.result_text and enc.inputs_version == v0 + 1
    b, jb = add_artifact(db, world, "audio", b"FAKE_ASR_FAIL" + b"1")
    for _ in range(3):
        engine.drain(); db.expire_all()
        jj = db.get(Job, jb.id)
        if jj.status == "queued":
            jj.run_after = utcnow(); db.commit()
    b = db.get(Artifact, b.id)
    assert b.proc_status == "failed" and b.error_code.startswith("asr_") and b.error_detail                 # real failure shown, not a fake transcript
    assert not b.result_text
    c, jc = add_artifact(db, world, "audio", b"FAKE_ASR_SILENT")
    engine.drain(); db.expire_all(); c = db.get(Artifact, c.id)
    assert c.proc_status == "failed" and c.error_code == "asr_no_speech" and db.get(Job, jc.id).status == "failed"   # non-retryable: no pointless retries


def test_ocr_creates_unverified_lab_items_and_reocr_keeps_verified(db, world):
    a, j = add_artifact(db, world, "lab_image", b"\xff\xd8img", name="lab.jpg", mime="image/jpeg")
    engine.drain(); db.expire_all()
    items = db.scalars(select(LabItem).where(LabItem.encounter_id == world.enc.id, LabItem.deleted_at.is_(None)).order_by(LabItem.sort_order)).all()
    assert [i.name for i in items][:2] == ["丙氨酸氨基转移酶", "总胆固醇"] and all(not i.verified and i.source == "ocr" for i in items)
    tc = next(i for i in items if i.abbr == "TC")
    assert tc.flag == "H" and tc.ref_high == 5.17 and tc.value_num == 6.20
    items[0].verified, items[0].verified_by = True, world.doc.id; db.commit()
    engine.retry(j.id); engine.drain(); db.expire_all()                                                     # re-run OCR
    live = db.scalars(select(LabItem).where(LabItem.encounter_id == world.enc.id, LabItem.deleted_at.is_(None))).all()
    assert len([i for i in live if i.verified]) == 1 and len(live) == 4                                     # verified row kept, unverified ones replaced (not duplicated)


def test_parse_lab_text_on_real_report_format():
    text = """姓名:余锦晨 性别:男 年龄:21岁 医疗类别:门诊 样本号:2
简称 项目 结果 参考范围
TC 血清总胆固醇测定 3.60 mmol/L 0-5.17
CREA 肌酐测定(酶促动力学) 48.5 μmol/L 44-97
A/G 白球比 2.9 ↑ 1.5-2.5
AST/AL 谷草/谷丙 0.9
检验日期:2026-08-25 14:13:00 审核日期:2026-08-25"""
    rows = {r["name"]: r for r in parse_lab_text(text)}
    assert rows["血清总胆固醇测定"]["flag"] == "N" and rows["血清总胆固醇测定"]["ref_high"] == 5.17
    assert rows["白球比"]["flag"] == "H"
    assert not any("姓名" in n or "日期" in n for n in rows)


REAL_OCR_REPORT = """广丰丰溪堂中医院检验报告单
姓 名: 余锦晨 性别: 男 年龄: 21 岁 医疗类别: 门诊 样本号: 2
简称 项目 结果 单位 参考范围 简称 项目 结果 单位 参考范围
LPa 脂蛋白a 31.23 mg/L CHE 血清胆碱酯酶测定 7424.6 U/L 4500-12000
TC 血清总胆固醇测定 3.60 mmol/L 0-5.17 TBA 血清总胆汁酸测定 (1.5 μmol/L 0-15)
TG 血清甘油三酯测定 0.92 mmol/L 0.35-2.30 GLOB 球蛋白 18.3 g/L 15-35
HDL 血清高密度脂蛋白胆固醇 1.40 mmol/L 1.04-1.66 A/G 白球比 2.9 ↑ 1.5-2.5
LDL 血清低密度脂蛋白胆固醇 1.64 mmol/L 1.04-3.10 GLU 葡萄糖测定 4.88 mmol/L 3.89-6.10
CREA 肌酐测定 (酶促动力学) 48.5 μmol/L 44-97
ALT 血清丙氨酸氨基转移酶 19.0 U/L 0-40
AST/AL 谷草/谷丙 0.9
检验日期: 2026-08-25 14:13:00 审核日期: 2026-08-25 14:33:20 检验者: 官振艳 审核者: 刘婷"""


def test_parse_real_two_column_report_and_never_swallows_ref_as_unit():
    """Output of the real PaddleOCR-VL run on test/微信图片_20261013234121_69_2.jpg (see docs/v3/test-report.md)."""
    rows = {r["abbr"]: r for r in parse_lab_text(REAL_OCR_REPORT)}
    assert {"LPa", "CHE", "TC", "TBA", "TG", "GLOB", "HDL", "A/G", "LDL", "GLU", "CREA", "ALT", "AST/AL"} <= set(rows)       # both columns parsed
    ag = rows["A/G"]
    assert ag["value_text"] == "2.9" and ag["unit"] == "" and ag["ref_text"] == "1.5-2.5" and ag["flag"] == "H"            # was: unit='1.5-2.5'
    assert rows["TBA"]["value_text"] == "1.5" and rows["TBA"]["ref_text"] == "0-15" and rows["TBA"]["unit"] == "μmol/L"       # value inside parentheses
    assert rows["CHE"]["unit"] == "U/L" and rows["CHE"]["ref_high"] == 12000 and rows["CHE"]["flag"] == "N"
    assert rows["AST/AL"]["ref_text"] == "" and rows["AST/AL"]["flag"] == ""                                                # no range → no invented flag
    assert not any("日期" in r["name"] or "检验者" in r["name"] for r in rows.values())                                     # header/footer never become rows
    for r in rows.values():
        assert not re.fullmatch(r"[-+]?\d+(\.\d+)?(-[-+]?\d+(\.\d+)?)?", r["unit"])                                   # no numeric "unit" anywhere
