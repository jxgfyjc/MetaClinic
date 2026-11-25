"""Job handlers: artifact processing (ASR/OCR/tongue), encounter analysis, treatment-plan generation.

Pattern in every handler: (1) load plain data in a short read transaction, (2) call the model with NO database
session open, (3) persist the result in a short write transaction. A failure is recorded on BOTH the job and the
domain row (artifact / analysis module / plan) with the same stable error code so the UI never shows "success"
for something that failed, and a retry re-runs only the failed piece.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

from server.ai import analysis as ana
from server.ai import client, plan_gen
from server.ai.client import ModelError
from server.ai.facts import build_facts
from server.config import get_settings
from server.db.base import session_scope
from server.db.models import (Analysis, AnalysisModule, Artifact, Diagnosis, Encounter, LabItem, TherapyItem, TreatmentPlan, User)
from server.domain import encounters as enc_svc
from server.domain import plans as plan_svc
from server.domain import prescriptions as rx_svc
from server.domain.enums import (AnalysisStatus, ArtifactKind, DxModule, DxStatus, JobStatus, ModuleStatus, PlanStatus, ProcStatus)
from server.domain.ids import utcnow
from server.errors import NotFound
from server.jobs.engine import Cancelled, JobContext, JobError, register
from server.security import audit
from server.security.audit import Actor


def artifact_path(key: str) -> Path:
    return get_settings().objects_dir / key


# =========================================================================== artifacts
@register("asr", resource="asr")
def job_asr(ctx: JobContext) -> dict:
    aid = ctx.input["artifact_id"]
    with session_scope() as s:
        a = s.get(Artifact, aid)
        if a is None or a.deleted_at is not None:
            raise JobError("artifact_missing", "录音已被删除", retryable=False)
        a.proc_status, a.proc_job_id, a.attempt_count = ProcStatus.PROCESSING.value, ctx.job_id, a.attempt_count + 1
        path, name = artifact_path(a.storage_key), a.original_name or "audio"
    if not path.exists():
        _fail_artifact(aid, "file_missing", "录音文件不存在（可能被清理）")
        raise JobError("file_missing", "录音文件不存在", retryable=False)
    ctx.progress(0.1, "语音识别中…")
    try:
        text, meta, ms = client.asr_transcribe(path.read_bytes(), name)
    except ModelError as e:
        _fail_artifact(aid, e.code, e.message)
        raise
    with session_scope() as s:
        a = s.get(Artifact, aid)
        a.proc_status, a.result_text, a.result_json = ProcStatus.SUCCEEDED.value, text, {"meta": meta, "latency_ms": ms}
        a.error_code = a.error_detail = ""
        if a.encounter_id:
            _touch_encounter_inputs(s, a.encounter_id)
    return {"artifact_id": aid, "chars": len(text), "latency_ms": ms}


@register("ocr", resource="ocr")
def job_ocr(ctx: JobContext) -> dict:
    aid = ctx.input["artifact_id"]
    with session_scope() as s:
        a = s.get(Artifact, aid)
        if a is None or a.deleted_at is not None:
            raise JobError("artifact_missing", "图片已被删除", retryable=False)
        a.proc_status, a.proc_job_id, a.attempt_count = ProcStatus.PROCESSING.value, ctx.job_id, a.attempt_count + 1
        path, mime = artifact_path(a.thumb_key or a.storage_key), (a.mime or "image/jpeg")
    if not path.exists():
        _fail_artifact(aid, "file_missing", "图片文件不存在")
        raise JobError("file_missing", "图片文件不存在", retryable=False)
    ctx.progress(0.1, "OCR 识别中…")
    try:
        text, engine, ms = client.ocr_image(path.read_bytes(), "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else mime)
    except ModelError as e:
        _fail_artifact(aid, e.code, e.message)
        raise
    items = parse_lab_text(text)
    with session_scope() as s:
        a = s.get(Artifact, aid)
        a.proc_status, a.result_text = ProcStatus.SUCCEEDED.value, text
        a.result_json = {"engine": engine, "latency_ms": ms, "items_found": len(items)}
        a.error_code = a.error_detail = ""
        if a.encounter_id:
            _replace_lab_items(s, a, items)
            _touch_encounter_inputs(s, a.encounter_id)
    return {"artifact_id": aid, "chars": len(text), "items": len(items), "latency_ms": ms}


@register("tongue", resource="llm")
def job_tongue(ctx: JobContext) -> dict:
    aid = ctx.input["artifact_id"]
    with session_scope() as s:
        a = s.get(Artifact, aid)
        if a is None or a.deleted_at is not None:
            raise JobError("artifact_missing", "舌象图片已被删除", retryable=False)
        a.proc_status, a.proc_job_id, a.attempt_count = ProcStatus.PROCESSING.value, ctx.job_id, a.attempt_count + 1
        path = artifact_path(a.thumb_key or a.storage_key)
    try:
        out = ana.run_tongue(path)
    except ModelError as e:
        _fail_artifact(aid, e.code, e.message)
        raise
    with session_scope() as s:
        a = s.get(Artifact, aid)
        a.proc_status, a.result_json = ProcStatus.SUCCEEDED.value, out
        a.result_text = "；".join(out.get("observations", []))[:2000]
        a.error_code = a.error_detail = ""
    return {"artifact_id": aid, "usable": out.get("_usable", True)}


def _fail_artifact(aid: str, code: str, msg: str) -> None:
    with session_scope() as s:
        a = s.get(Artifact, aid)
        if a is not None:
            a.proc_status, a.error_code, a.error_detail = ProcStatus.FAILED.value, code, msg[:1000]


def _touch_encounter_inputs(s, encounter_id: str) -> None:
    enc = s.get(Encounter, encounter_id)
    if enc is not None:
        s.flush()
        enc_svc.refresh_inputs(s, enc)


# ------------------------------------------------------------------ lab text parsing (OCR → structured, conservative)
# A lab row looks like:  [ABBR] <name> <value> [↑|↓] [unit] [ref-range]   — and many reports print TWO such groups per
# physical line (two-column layout). We locate every "[ABBR] name value" group start with a single scan, split the
# line at those boundaries, and parse each group on its own. Anything that doesn't parse stays only in the raw OCR text
# (shown to the doctor next to the image) — rows are never invented and every parsed row starts UNVERIFIED.
_NUM = r"[-+]?\d+(?:\.\d+)?"
_UNIT = r"(?:[A-Za-zμµ%/·\^\-\.0-9]{1,14}|[一-鿿]{1,3}/[A-Za-zL一-鿿]{1,3})"
_REF = rf"(?:{_NUM}\s*[-~–]\s*{_NUM}|[<>≤≥]\s*{_NUM})"
_GROUP = re.compile(
    rf"(?P<abbr>[A-Za-z][A-Za-z0-9/%\-]{{0,9}})?\s*(?P<name>[一-鿿α-ωγ][^\d↑↓\n]{{0,30}}?)\s*[（(]?\s*(?P<val>{_NUM})\s*(?P<arrow>[↑↓]?)\s*(?P<unit>{_UNIT})?\s*(?P<ref>{_REF})?\s*[）)]?\s*(?P<arrow2>[↑↓]?)")
_SKIP = ("姓名", "性别", "年龄", "日期", "科室", "样本", "申请", "检验者", "审核", "病历号", "床号", "医疗类别", "报告", "备注")


def _norm_name(n: str) -> str:
    return re.sub(r"[\s:：]+$", "", n.strip().strip("()（）"))


def parse_lab_text(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"[|｜]", " ", raw).strip()
        if len(line) < 4 or any(k in line[:12] for k in _SKIP):
            continue
        # group boundaries: an ABBR (A-Z start) directly followed by a CJK name begins a new group
        starts = [m.start() for m in re.finditer(r"(?:(?<=\s)|^)[A-Za-z][A-Za-z0-9/%\-]{0,9}\s+(?=[一-鿿α-ωγ])", line)]
        pieces = [line[a:b] for a, b in zip(starts, starts[1:] + [len(line)])] if len(starts) > 1 else [line]
        for piece in pieces:
            m = _GROUP.search(piece.strip())
            if not m or not m.group("val"):
                continue
            name = _norm_name(m.group("name"))
            if not name or len(name) < 2 or name in seen or any(k in name for k in _SKIP):
                continue
            seen.add(name)
            ref = (m.group("ref") or "").replace("–", "-").replace("~", "-").replace(" ", "")
            low = high = None
            mm = re.match(rf"^({_NUM})-({_NUM})$", ref)
            if mm:
                low, high = float(mm.group(1)), float(mm.group(2))
            elif ref.startswith(("<", "≤")):
                high = float(ref[1:])
            elif ref.startswith((">", "≥")):
                low = float(ref[1:])
            val = float(m.group("val"))
            arrow = (m.group("arrow") or m.group("arrow2") or "").replace("↑", "H").replace("↓", "L")
            flag = arrow if arrow in ("H", "L") else ""
            if not flag and (low is not None or high is not None):
                flag = "H" if high is not None and val > high else ("L" if low is not None and val < low else "N")
            unit = (m.group("unit") or "").strip()
            if re.fullmatch(rf"{_NUM}(?:\s*[-~–]\s*{_NUM})?", unit):       # a range/number mis-read as the unit → it is the reference
                if not ref:
                    ref = unit.replace("–", "-").replace("~", "-").replace(" ", "")
                    mm2 = re.match(rf"^({_NUM})-({_NUM})$", ref)
                    if mm2:
                        low, high = float(mm2.group(1)), float(mm2.group(2))
                unit = ""
            if not flag and (low is not None or high is not None):
                flag = "H" if high is not None and val > high else ("L" if low is not None and val < low else "N")
            out.append({"abbr": (m.group("abbr") or "")[:32], "name": name[:128], "value_text": m.group("val"), "value_num": val,
                        "unit": unit[:32], "ref_low": low, "ref_high": high, "ref_text": ref, "flag": flag})
    return out


def _replace_lab_items(s, a: Artifact, items: list[dict]) -> None:
    """Replace only this artifact's UNVERIFIED OCR rows (verified/manual rows are never overwritten)."""
    for old in s.scalars(select(LabItem).where(LabItem.artifact_id == a.id, LabItem.deleted_at.is_(None))):
        if not old.verified and old.source == "ocr":
            old.deleted_at = utcnow()
    base = len(s.scalars(select(LabItem.id).where(LabItem.encounter_id == a.encounter_id)).all())
    for i, it in enumerate(items):
        s.add(LabItem(encounter_id=a.encounter_id, patient_id=a.patient_id, artifact_id=a.id, source="ocr", verified=False,
                      sort_order=base + i, **it))


# =========================================================================== analysis
@register("analysis", resource="llm")
def job_analysis(ctx: JobContext) -> dict:
    aid = ctx.input["analysis_id"]
    with session_scope() as s:
        an = s.get(Analysis, aid)
        if an is None:
            raise JobError("analysis_missing", "分析记录不存在", retryable=False)
        enc = s.get(Encounter, an.encounter_id)
        an.status, an.started_at, an.job_id, an.error_code, an.error_detail = AnalysisStatus.RUNNING.value, utcnow(), ctx.job_id, "", ""
        facts = build_facts(s, enc)
        an.facts_json = [f.as_dict() for f in facts]
        an.inputs_version, an.inputs_hash = enc.inputs_version, enc.inputs_hash
        tongue_art = s.scalars(select(Artifact).where(Artifact.encounter_id == enc.id, Artifact.kind == ArtifactKind.TONGUE_IMAGE.value,
                                                      Artifact.deleted_at.is_(None)).order_by(Artifact.created_at.desc())).first()
        tongue_info = ({"id": tongue_art.id, "path": str(artifact_path(tongue_art.thumb_key or tongue_art.storage_key)),
                        "cached": tongue_art.result_json if tongue_art.proc_status == ProcStatus.SUCCEEDED.value else None}
                       if tongue_art else None)
        for m in ("tongue", "western", "tcm", "synthesis"):
            mod = _module(s, an.id, m)
            mod.status = ModuleStatus.PENDING.value
            mod.error_code = mod.error_detail = ""
        fact_count = len(facts)
    if fact_count < 2:
        _finish_analysis(aid, AnalysisStatus.FAILED, "insufficient_facts", "临床资料过少（需要至少主诉等 2 条事实）才能分析")
        raise JobError("insufficient_facts", "临床资料过少，无法分析", retryable=False)

    results: dict[str, dict] = {}
    failed: dict[str, str] = {}

    def mod_run(name: str, fn, *args):
        ctx.check_cancel()
        t0 = utcnow()
        _set_module(aid, name, ModuleStatus.RUNNING, started=t0)
        try:
            out = fn(*args)
        except ModelError as e:
            _set_module(aid, name, ModuleStatus.FAILED, code=e.code, detail=e.message, finished=utcnow(), started=t0, raw=e.raw)
            failed[name] = e.message
            return None
        except Exception as e:                                         # noqa: BLE001
            _set_module(aid, name, ModuleStatus.FAILED, code="unexpected_error", detail=f"{type(e).__name__}: {e}", finished=utcnow(), started=t0)
            failed[name] = str(e)
            return None
        _set_module(aid, name, ModuleStatus.SUCCEEDED, output=out, finished=utcnow(), started=t0)
        results[name] = out
        return out

    ctx.progress(0.05, "整理临床事实")
    # tongue: reuse the cached image analysis when available, else run (only if an image actually exists)
    tongue = None
    if tongue_info is None:
        _set_module(aid, "tongue", ModuleStatus.SKIPPED, detail="未上传舌象图片", finished=utcnow())
    elif tongue_info["cached"]:
        tongue = tongue_info["cached"]
        _set_module(aid, "tongue", ModuleStatus.SUCCEEDED, output=tongue, finished=utcnow())
    else:
        ctx.progress(0.1, "舌象分析中…")
        tongue = mod_run("tongue", ana.run_tongue, Path(tongue_info["path"]))
    ctx.progress(0.25, "西医分析中…")
    western = mod_run("western", ana.run_western, facts, [])
    ctx.progress(0.55, "中医辨证中…")
    tcm = mod_run("tcm", ana.run_tcm, facts, [], tongue)
    if western or tcm:
        ctx.progress(0.8, "综合评估中…")
        mod_run("synthesis", ana.run_synthesis, facts, western, tcm)
    else:
        _set_module(aid, "synthesis", ModuleStatus.SKIPPED, detail="西医/中医分析均失败，无法综合", finished=utcnow())

    ok = [k for k in ("western", "tcm") if k in results]
    if not ok:
        _finish_analysis(aid, AnalysisStatus.FAILED, "all_modules_failed", "；".join(f"{k}: {v}" for k, v in failed.items()))
        raise JobError("all_modules_failed", "；".join(failed.values()) or "分析失败", retryable=True)
    status = AnalysisStatus.SUCCEEDED if (len(ok) == 2 and not failed) else AnalysisStatus.PARTIAL
    with session_scope() as s:
        an = s.get(Analysis, aid)
        enc = s.get(Encounter, an.encounter_id)
        _materialise_candidates(s, enc, an, results)
        an.summary_json = results.get("synthesis", {})
        an.models_json = {k: v.get("_model", "") for k, v in results.items()}
        an.prompt_versions_json = {k: v.get("_prompt", "") for k, v in results.items()}
        an.status, an.finished_at = status.value, utcnow()
        if failed:
            an.error_code, an.error_detail = "module_failed", "；".join(f"{k}: {v}" for k, v in failed.items())[:2000]
        audit.record(s, Actor.system("ai-analysis"), "analysis.complete", entity_type="analysis", entity_id=an.id, encounter_id=enc.id,
                     patient_id=enc.patient_id, summary=f"AI 预分析完成（{status.value}）", detail={"failed": list(failed), "job": ctx.job_id})
    return {"analysis_id": aid, "status": status.value, "failed": list(failed)}


def _module(s, analysis_id: str, name: str) -> AnalysisModule:
    m = s.scalar(select(AnalysisModule).where(AnalysisModule.analysis_id == analysis_id, AnalysisModule.module == name))
    if m is None:
        order = {"tongue": 0, "western": 1, "tcm": 2, "synthesis": 3}.get(name, 9)
        m = AnalysisModule(analysis_id=analysis_id, module=name, sort_order=order)
        s.add(m)
        s.flush()
    return m


def _set_module(aid, name, status, *, output=None, code="", detail="", started=None, finished=None, raw="") -> None:
    with session_scope() as s:
        m = _module(s, aid, name)
        m.status = status.value
        if output is not None:
            m.output_json = {k: v for k, v in output.items() if not k.startswith("_")}
            m.model, m.prompt_version, m.duration_ms = output.get("_model", ""), output.get("_prompt", ""), output.get("_latency_ms", 0)
        if code or detail:
            m.error_code, m.error_detail = code, detail[:1500]
        if raw:
            m.output_json = {"failure_raw_excerpt": raw[:2000]}          # what the model actually said — so a bad run can be diagnosed afterwards
        if started:
            m.started_at = started
        if finished:
            m.finished_at = finished


def _finish_analysis(aid: str, status: AnalysisStatus, code: str, detail: str) -> None:
    with session_scope() as s:
        an = s.get(Analysis, aid)
        an.status, an.error_code, an.error_detail, an.finished_at = status.value, code, detail[:2000], utcnow()


def _materialise_candidates(s, enc: Encounter, an: Analysis, results: dict) -> None:
    """AI candidates → Diagnosis rows (pending). Idempotent per (analysis, candidate_key). Reviewed rows from an
    earlier analysis are kept; unreviewed pending ones from older analyses become `superseded`."""
    for d in s.scalars(select(Diagnosis).where(Diagnosis.encounter_id == enc.id, Diagnosis.status == DxStatus.PENDING.value,
                                               Diagnosis.analysis_id != an.id)):
        d.status = DxStatus.SUPERSEDED.value
    order = s.scalar(select(Diagnosis.sort_order).where(Diagnosis.encounter_id == enc.id).order_by(Diagnosis.sort_order.desc()).limit(1)) or 0
    for module, key in (("western", "western"), ("tcm", "tcm")):
        for c in (results.get(key) or {}).get("candidates", []):
            exists = s.scalar(select(Diagnosis.id).where(Diagnosis.analysis_id == an.id, Diagnosis.candidate_key == c["key"]))
            if exists:
                continue
            order += 1
            s.add(Diagnosis(encounter_id=enc.id, module=DxModule.WESTERN.value if module == "western" else DxModule.TCM.value,
                            name=c["name"], original_name=c["name"], origin="ai", analysis_id=an.id, candidate_key=c["key"],
                            confidence=c["confidence"], rationale=c["rationale"], sort_order=order,
                            evidence_json={"supporting": c["supporting"], "contradicting": c["contradicting"],
                                           "missing_information": c["missing_information"], "questions_to_ask": c["questions_to_ask"],
                                           "evidence_weak": c["evidence_weak"]}))


# =========================================================================== treatment plan
@register("plan", resource="llm")
def job_plan(ctx: JobContext) -> dict:
    pid = ctx.input["plan_id"]
    with session_scope() as s:
        plan = s.get(TreatmentPlan, pid)
        if plan is None:
            raise JobError("plan_missing", "方案不存在", retryable=False)
        if plan.status == PlanStatus.SUPERSEDED.value:
            raise JobError("plan_superseded", "该方案已被新版本取代，无需生成", retryable=False)
        enc = s.get(Encounter, plan.encounter_id)
        facts = build_facts(s, enc)
        dxs = [{"module": d.module, "name": d.name, "primary": d.is_primary, "doctor_note": d.doctor_note}
               for d in plan_svc.final_diagnoses(s, enc.id)]
        sections = list(plan.sections_requested_json or [])
        spec = enc.special_status_json or {}
        safety_ctx = {"allergies": enc_svc.active_allergy_names(enc.patient), "pregnant": spec.get("pregnant"), "lactating": spec.get("lactating"),
                      "renal_impairment": spec.get("renal"), "hepatic_impairment": spec.get("hepatic"),
                      "current_medications": [m.get("name") for m in (enc.current_meds_json or []) if isinstance(m, dict)],
                      "gender": enc.patient.gender, "age": enc_svc.calc_age(enc.patient)}
        catalog = rx_svc.catalog_index(s)
        from server.db.models import TherapyProgram
        programs = [{"id": p.id, "name": p.name, "category": p.category, "applicable": p.applicable}
                    for p in s.scalars(select(TherapyProgram).where(TherapyProgram.active.is_(True)))]
        doctor = s.get(User, enc.doctor_id) if enc.doctor_id else None
        pref = {k: (doctor.profile_json or {}).get(k, "") for k in ("formula_preference", "herb_preference", "non_drug_preference")} if doctor else {}
        # 经验画像：本次接诊选用的、可用的、已发布且审核放行的条目 → 编号参考块 S1..Sn（没选就完全不改动原有流程）
        from server.style import reference as style_ref
        from server.style import service as style_svc
        style_ids = style_svc.chosen_for_encounter(s, enc.id)
        style_refs = style_ref.gather(s, doctor, style_ids) if (doctor and style_ids) else []
        style_block = style_ref.prompt_block(style_refs)
        fact_ids = {f.id for f in facts}
        plan.sections_state_json = {sec: {"status": "running"} for sec in sections}
    if not dxs:
        _fail_plan(pid, "no_final_diagnoses", "没有最终诊断")
        raise JobError("no_final_diagnoses", "没有最终诊断", retryable=False)
    ctx.progress(0.1, "起草整体治疗方案（约 1–2 分钟）…")
    try:
        raw, meta = plan_gen.generate_raw(facts, dxs, sections, safety_ctx, programs, pref, style_block)
    except ModelError as e:
        _fail_plan(pid, e.code, e.message)
        raise
    content, therapy = plan_gen.normalise(raw, catalog, programs, sections)
    style_snap: dict = {}
    if style_ids:
        clean, vstats = style_ref.validate(raw.get("style"), style_refs, fact_ids)
        style_snap = style_ref.snapshot(style_refs, clean, vstats, style_ids, {f.id: f.as_dict() for f in facts})
        if not style_refs:
            style_snap["note"] = "所选画像当前没有可供 AI 参考的已审核条目（可能已停用、撤回授权或条目含未放行的风险提示），本方案未使用经验参考"
    with session_scope() as s:
        plan = s.get(TreatmentPlan, pid)
        if plan.status == PlanStatus.SUPERSEDED.value:                    # superseded while the model was running
            return {"plan_id": pid, "discarded": True}
        plan.style_json = style_snap
        plan.content_json, plan.ai_original_json = content, {"raw": raw, "normalised": content}
        plan.models_json, plan.prompt_versions_json = {"plan": meta["model"]}, {"plan": meta["prompt"]}
        plan.sections_state_json = {sec: {"status": "succeeded"} for sec in plan.sections_requested_json}
        plan.status, plan.error_code, plan.error_detail, plan.job_id = PlanStatus.DRAFT.value, "", "", ctx.job_id
        plan.row_version += 1
        for t in list(plan.therapy_items):
            s.delete(t)
        s.flush()
        for i, t in enumerate(therapy):
            plan.therapy_items.append(TherapyItem(encounter_id=plan.encounter_id, sort_order=i, category=t["category"], name=t["name"],
                                                  source_type=t["source_type"], program_id=t["program_id"], acupoints_json=t["acupoints"],
                                                  detail_json=t["detail"], indications=t["indications"], contraindications=t["contraindications"],
                                                  needs_confirmation_json=t["needs_confirmation"], rationale=t["rationale"], status="pending",
                                                  original_json={"name": t["name"], "category": t["category"], "acupoints": t["acupoints"],
                                                                 "detail": t["detail"], "indications": t["indications"],
                                                                 "contraindications": t["contraindications"]}))
        audit.record(s, Actor.system("ai-plan"), "plan.generated", entity_type="plan", entity_id=plan.id, encounter_id=plan.encounter_id,
                     summary=f"AI 起草治疗方案 v{plan.version_no}", detail={"model": meta, "job": ctx.job_id})
    return {"plan_id": pid, "herbal": len(content["herbal"]), "therapy": len(therapy)}


def _fail_plan(pid: str, code: str, msg: str) -> None:
    with session_scope() as s:
        plan = s.get(TreatmentPlan, pid)
        if plan is not None and plan.status != PlanStatus.SUPERSEDED.value:
            plan.status, plan.error_code, plan.error_detail = PlanStatus.FAILED.value, code, msg[:2000]
            plan.sections_state_json = {k: {"status": "failed", "error": msg[:200]} for k in (plan.sections_requested_json or [])}
            plan.row_version += 1


# =========================================================================== terminal sync
def _on_terminal(job_id: str) -> None:
    """Make sure domain rows never stay in an 'in progress' state after their job ended abnormally."""
    from server.db.models import Job
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None or j.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            return
        key = {"asr": "artifact_id", "ocr": "artifact_id", "tongue": "artifact_id", "analysis": "analysis_id", "plan": "plan_id"}.get(j.kind)
        ref = (j.input_json or {}).get(key) if key else None
        if not ref:
            return
        msg = j.error_detail or "任务已取消"
        if key == "artifact_id":
            a = s.get(Artifact, ref)
            if a is not None and a.proc_status in (ProcStatus.PENDING.value, ProcStatus.PROCESSING.value):
                a.proc_status, a.error_code, a.error_detail = ProcStatus.FAILED.value, j.error_code or "cancelled", msg[:1000]
        elif key == "analysis_id":
            an = s.get(Analysis, ref)
            if an is not None and an.status in (AnalysisStatus.QUEUED.value, AnalysisStatus.RUNNING.value):
                an.status = AnalysisStatus.CANCELLED.value if j.status == JobStatus.CANCELLED.value else AnalysisStatus.FAILED.value
                an.error_code, an.error_detail, an.finished_at = j.error_code or "cancelled", msg[:2000], utcnow()
        elif key == "plan_id":
            pl = s.get(TreatmentPlan, ref)
            if pl is not None and pl.status == PlanStatus.GENERATING.value:
                pl.status, pl.error_code, pl.error_detail = PlanStatus.FAILED.value, j.error_code or "cancelled", msg[:2000]
                pl.row_version += 1


from server.jobs.engine import on_terminal  # noqa: E402

on_terminal(_on_terminal)
