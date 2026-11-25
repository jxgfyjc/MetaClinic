"""AI Orchestrator — instruct #43.

ClinicalAnalysisWorkflow:
  Load Encounter → Build Clinical Context → Parallel(Western|TCM|Tongue|Knowledge)
  → Medication Safety → Prescription Draft → Synthesis → Store AI Run → Doctor Workbench

instruct #17/#44: parallel via asyncio.gather, never serial.
instruct #61: per-module failure → PARTIAL, never whole-failure.
instruct #84: result caching by (encounter, model, prompt_version, knowledge_version, input_hash).
instruct #21: model reasons autonomously; RAG provides evidence only.
"""
from __future__ import annotations

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', '..', 'packages'))
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', '..', 'packages', 'clinical-schemas'))

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from clinical_schemas import (
    AIPanelStatus, AIRunStatus, ClinicalSynthesis, EvidenceChunk, FollowupSummary,
    ModuleStatus, PrescriptionDraft, RiskAssessment, TCMAnalysis, TongueAnalysis,
    WesternAnalysis,
)

# shared package on path via services/api sys.path; fall back to relative
try:
    from packages.shared.config import get_model
    from packages.shared.providers import AIUnavailable, LLMProvider
    from packages.shared.cache import ai_cache_key, get_cache
    from packages.shared.audit import audit_log
except ImportError:  # allow direct run within service layout
    import os, sys
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, _root)
    from packages.shared.config import get_model  # type: ignore
    from packages.shared.providers import AIUnavailable, LLMProvider  # type: ignore
    from packages.shared.cache import ai_cache_key, get_cache  # type: ignore
    from packages.shared.audit import audit_log  # type: ignore

# Progress callback signature (demo.md 第六节: module status由真实模型调用决定).
# (run_id, module, status, payload) -> None
ProgressCb = Callable[[str, str, str, dict], Awaitable[None]]


PROMPT_VERSIONS = {
    "western": "western_analysis_v1.1",
    "tcm": "tcm_analysis_v1.1",
    "tongue": "tongue_analysis_v1",
    "synthesis": "synthesis_v1.1",
    "prescription": "prescription_draft_v1.1",
    "therapy": "therapy_programs_v1",
    "followup": "followup_v1",
    "risk_triage": "risk_triage_v1",
}
KNOWLEDGE_VERSION = "seed-v1"


def _load_prompt(name: str) -> str:
    import os
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    p = os.path.join(_root, "prompts", f"{name}.md")
    with open(p, encoding="utf-8") as f:
        return f.read()


def _strip_fence(text: str) -> str:
    """Strip a single markdown code fence (```json ... ``` or ``` ... ```) if present."""
    t = text.strip()
    if t.startswith("```"):
        # drop the opening fence line
        nl = t.find("\n")
        if nl < 0:
            return t
        t = t[nl + 1:].strip()
        # drop a trailing fence
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


def _safe_json(text: str) -> dict:
    """Extract the LAST balanced JSON object from LLM output; return {} on failure.

    Qwen3.8-27B emits chain-of-thought reasoning before its final JSON answer
    (thinking leaks into the output stream). The real answer is the last complete
    {...} block. We strip a surrounding markdown fence, then pair each '{' with the
    final '}' and return the first (== earliest, longest) substring that parses.
    """
    if not text:
        return {}
    text = _strip_fence(text)
    try:
        return json.loads(text)
    except Exception:
        pass
    j = text.rfind("}")
    if j <= 0:
        return {}
    start = 0
    while True:
        i = text.find("{", start)
        if i < 0 or i >= j:
            break
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            start = i + 1
    return {}


def _slim_patient(p: dict) -> dict:
    """Patient fields downstream modules actually need (instruct_1.md §2).
    Drops free-text history blobs already given to western/tcm directly."""
    if not p:
        return {}
    return {
        "name": p.get("name", ""), "gender": p.get("sex", p.get("gender", "")),
        "age": p.get("age", 0), "allergies": p.get("allergies", []),
    }


def _slim_western(w: dict) -> dict:
    """Condensed western output for downstream prompts (synthesis/prescription).

    instruct_1.md §2 精简冗余上下文: the full western dict (supporting_evidence,
    contradicting_evidence, clinical_reasoning arrays) is verbose and is already
    stored + displayed to the doctor verbatim. Downstream modules only need the
    diagnostic basis (problems, diagnoses, red flags, findings), so we drop the
    verbose arrays from the INPUT prompt — we do NOT truncate the stored OUTPUT."""
    if not w:
        return {}
    return {
        "summary": w.get("summary", ""),
        "problem_list": w.get("problem_list", []),
        "clinical_findings": w.get("clinical_findings", []),
        "red_flags": w.get("red_flags", []),
        "differential_diagnoses": [
            {"diagnosis": d.get("diagnosis", ""), "confidence_level": d.get("confidence_level", "")}
            for d in w.get("differential_diagnoses", []) if isinstance(d, dict)
        ],
    }


def _slim_tcm(t: dict) -> dict:
    """Condensed TCM output for downstream prompts (see _slim_western rationale)."""
    if not t:
        return {}
    return {
        "syndrome_candidates": [
            {"name": s.get("name", ""), "confidence_level": s.get("confidence_level", "")}
            for s in t.get("syndrome_candidates", []) if isinstance(s, dict)
        ],
        "treatment_principles": t.get("treatment_principles", []),
        "formula_candidates": t.get("formula_candidates", []),
    }


# ---------------------------------------------------------------------------
@dataclass
class ClinicalContext:
    encounter_id: str
    patient_id: str
    patient: dict = field(default_factory=dict)
    medical_history: dict = field(default_factory=dict)
    labs: list[dict] = field(default_factory=list)
    medications: list[dict] = field(default_factory=list)
    allergies: list[dict] = field(default_factory=list)
    tongue_features: dict = field(default_factory=dict)
    tongue_image_key: str = ""
    raw_text: str = ""
    evidence: list[dict] = field(default_factory=list)  # RAG chunks
    doctor_id: str = ""
    doctor_profile: dict = field(default_factory=dict)  # instruct_1.md §3
    hospital_programs: list[dict] = field(default_factory=list)  # instruct_1.md §4 catalog


@dataclass
class AnalysisResult:
    run_id: str
    status: AIRunStatus
    western: dict = field(default_factory=dict)
    tcm: dict = field(default_factory=dict)
    tongue: dict = field(default_factory=dict)
    synthesis: dict = field(default_factory=dict)
    prescription: dict = field(default_factory=dict)
    therapy: dict = field(default_factory=dict)  # instruct_1.md §4 综合治疗方案
    evidence: list[dict] = field(default_factory=list)
    panel_status: dict = field(default_factory=dict)
    latency_ms: int = 0


# ---------------------------------------------------------------------------
class ClinicalAnalysisWorkflow:
    """instruct #43 orchestrator."""

    def __init__(self) -> None:
        self.cache = get_cache()

    def _ctx_hash(self, ctx: ClinicalContext) -> str:
        # instruct_1.md §3: doctor_id MUST be part of the cache key — different
        # doctors get different personalized outputs, so a cache hit for dr-001
        # must NOT be served to dr-002. hospital_programs catalog affects therapy.
        blob = json.dumps({
            "p": ctx.patient, "h": ctx.medical_history, "l": ctx.labs,
            "m": ctx.medications, "a": ctx.allergies, "t": ctx.tongue_features,
            "r": ctx.raw_text, "d": ctx.doctor_id,
            "hp": len(ctx.hospital_programs),
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    # -- per-module runners (each isolated; failure → module UNAVAILABLE) -----
    async def _western(self, ctx: ClinicalContext) -> tuple[ModuleStatus, dict]:
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("western_analysis")
        # instruct_1.md §3: doctor profile shapes analysis focus / presentation.
        # Preferences do NOT override patient condition (hard constraint).
        doc_hint = ""
        if ctx.doctor_profile:
            doc_hint = (ctx.doctor_profile.get("analysis_focus", "") or "") + " " + \
                       (ctx.doctor_profile.get("syndrome_preference", "") or "")
        user = json.dumps({
            "patient": ctx.patient, "history": ctx.medical_history, "labs": ctx.labs,
            "medications": ctx.medications, "allergies": ctx.allergies,
            "tongue": ctx.tongue_features, "raw_text": ctx.raw_text,
            "evidence": [{"source_id": e.get("source_id"), "title": e.get("title"),
                          "excerpt": e.get("excerpt")} for e in ctx.evidence],
            "doctor_focus": doc_hint.strip(),
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["western"])
        d.setdefault("knowledge_version", KNOWLEDGE_VERSION)
        return ModuleStatus.OK, d

    async def _tcm(self, ctx: ClinicalContext) -> tuple[ModuleStatus, dict]:
        try:
            llm = LLMProvider("tcm")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("tcm_analysis")
        # instruct_1.md §3: doctor syndrome/formula/herb preferences shape candidate ranking.
        doc_pref = ""
        if ctx.doctor_profile:
            doc_pref = json.dumps({
                "syndrome_preference": ctx.doctor_profile.get("syndrome_preference", ""),
                "formula_preference": ctx.doctor_profile.get("formula_preference", ""),
                "herb_preference": ctx.doctor_profile.get("herb_preference", ""),
            }, ensure_ascii=False)
        user = json.dumps({
            "symptoms": ctx.medical_history.get("symptoms", []),
            "tongue": ctx.tongue_features, "history": ctx.medical_history,
            "raw_text": ctx.raw_text,
            "evidence": [{"source_id": e.get("source_id"), "excerpt": e.get("excerpt")}
                         for e in ctx.evidence if e.get("domain") in ("", "tcm")],
            "doctor_preference": doc_pref,
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=180)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["tcm"])
        d.setdefault("knowledge_version", KNOWLEDGE_VERSION)
        return ModuleStatus.OK, d

    async def _tongue(self, ctx: ClinicalContext) -> tuple[ModuleStatus, dict]:
        if not ctx.tongue_image_key:
            return ModuleStatus.UNAVAILABLE, {"note": "no tongue image"}
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("tongue_analysis")
        # VLM call with image
        import os
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        img_path = os.path.join(_root, "data", "objects", ctx.tongue_image_key)
        if not os.path.exists(img_path):
            return ModuleStatus.UNAVAILABLE, {"note": "image not found"}
        import base64, mimetypes
        mime = mimetypes.guess_type(img_path)[0] or "image/png"
        b64 = base64.b64encode(open(img_path, "rb").read()).decode()
        resp = await llm.chat_vision(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": "分析这张舌象图片。"}],
            image_url=f"data:{mime};base64,{b64}", json_mode=True, timeout=300,
        )
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["tongue"])
        return ModuleStatus.OK, d

    async def _synthesis(self, ctx: ClinicalContext, western: dict, tcm: dict, tongue: dict) -> tuple[ModuleStatus, dict]:
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("synthesis")
        user = json.dumps({
            "western": _slim_western(western), "tcm": _slim_tcm(tcm),
            "tongue": tongue,
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["synthesis"])
        return ModuleStatus.OK, d

    async def _prescription(self, ctx: ClinicalContext, western: dict, tcm: dict) -> tuple[ModuleStatus, dict]:
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("prescription_draft")
        # instruct_1.md §3: doctor formula/herb preferences shape candidate selection.
        doc_pref = ""
        if ctx.doctor_profile:
            doc_pref = json.dumps({
                "formula_preference": ctx.doctor_profile.get("formula_preference", ""),
                "herb_preference": ctx.doctor_profile.get("herb_preference", ""),
                "non_drug_preference": ctx.doctor_profile.get("non_drug_preference", ""),
            }, ensure_ascii=False)
        user = json.dumps({
            "patient": _slim_patient(ctx.patient), "allergies": ctx.allergies,
            "medications": ctx.medications, "labs": ctx.labs,
            "western": _slim_western(western), "tcm": _slim_tcm(tcm),
            "doctor_preference": doc_pref,
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["prescription"])
        return ModuleStatus.OK, d

    async def _therapy(self, ctx: ClinicalContext, western: dict, tcm: dict) -> tuple[ModuleStatus, dict]:
        """instruct_1.md §4 综合治疗方案 (推拿/针灸/艾灸/理疗/康复).

        Independent from the formula library. Distinguishes hospital / knowledge /
        ai_generated source_type. Reuses clinical_main (no new model). Fails isolated.
        """
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("therapy_programs")
        user = json.dumps({
            "patient": _slim_patient(ctx.patient), "allergies": ctx.allergies,
            "medications": ctx.medications,
            "western": _slim_western(western), "tcm": _slim_tcm(tcm),
            "hospital_programs": [
                {"name": p.get("name", ""), "category": p.get("category", ""),
                 "hospital_code": p.get("hospital_code", ""),
                 "applicable_scenarios": p.get("applicable_scenarios", "")}
                for p in ctx.hospital_programs
            ],
            "doctor_preference": ctx.doctor_profile,
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["therapy"])
        return ModuleStatus.OK, d

    # -- main entry -----------------------------------------------------------
    async def run(self, ctx: ClinicalContext, *, actor: str = "system",
                  run_id: str | None = None,
                  on_progress: ProgressCb | None = None) -> AnalysisResult:
        """instruct #43 orchestrator.

        run_id / on_progress support the background-task + polling pattern
        (demo.md 第六节): the API handler creates an AIRun row + run_id, then
        schedules this via asyncio.create_task. on_progress persists per-module
        PENDING/RUNNING/COMPLETE/FAILED to the DB so the frontend polling sees
        real status driven by real model calls — no fake progress (demo.md #6).
        """
        t0 = time.time()
        run_id = run_id or uuid.uuid4().hex[:24]
        input_hash = self._ctx_hash(ctx)

        async def emit(module: str, status: str, payload: dict | None = None):
            if on_progress is not None:
                try:
                    await on_progress(run_id, module, status, payload or {})
                except Exception:
                    pass  # progress persistence must never break the run

        # instruct #84 cache
        ckey = ai_cache_key(
            encounter_id=ctx.encounter_id, model="orchestrator",
            prompt_version="v1.1", knowledge_version=KNOWLEDGE_VERSION,
            clinical_input=input_hash,
        )
        cached = await self.cache.get(ckey)
        if cached:
            # cache hit: emit synthetic COMPLETE for each module + a final run
            # event, so the polling consumer sees a uniform sequence. Do NOT
            # re-append outputs (the original run's rows already exist).
            for mod in ("western", "tcm", "tongue", "knowledge", "synthesis"):
                await emit(mod, "COMPLETE", cached.get(mod, {}))
            await emit("prescription", "COMPLETE", cached.get("prescription", {}))
            await emit("therapy", "COMPLETE", cached.get("therapy", {}))
            await emit("run", "COMPLETE", {"status": "SUCCEEDED",
                       "panel_status": cached.get("panel_status", {}),
                       "cached": True, "latency_ms": 0})
            return AnalysisResult(run_id=run_id, status=AIRunStatus.SUCCEEDED, **cached)

        # announce all modules PENDING before scheduling (demo.md #6)
        await emit("western", "PENDING")
        await emit("tcm", "PENDING")
        await emit("tongue", "PENDING")
        await emit("knowledge", "PENDING")
        await emit("synthesis", "PENDING")
        await emit("prescription", "PENDING")
        await emit("therapy", "PENDING")

        async def run_module(name: str, coro):
            """Wrap a runner: emit RUNNING, await, emit COMPLETE/FAILED,
            persist the output via on_progress."""
            await emit(name, "RUNNING")
            status, data = await coro
            if status == ModuleStatus.OK:
                await emit(name, "COMPLETE", data)
            else:
                reason = data.get("note", "module unavailable") if isinstance(data, dict) else "module unavailable"
                await emit(name, "FAILED", {"reason": reason})
            return status, data

        # instruct #44: PARALLEL — gather per-module runners so they run concurrently.
        # Each runner isolates its own failures (returns ModuleStatus.UNAVAILABLE, never raises).
        w_task = asyncio.create_task(run_module("western", self._western(ctx)))
        t_task = asyncio.create_task(run_module("tcm", self._tcm(ctx)))
        tg_task = asyncio.create_task(run_module("tongue", self._tongue(ctx)))
        w_status, w_data = await w_task
        t_status, t_data = await t_task
        tg_status, tg_data = await tg_task
        # knowledge retrieval handled by caller (ctx.evidence already populated)
        await emit("knowledge", "COMPLETE", {"evidence": ctx.evidence})

        # synthesis needs western+tcm. prescription also needs western+tcm.
        # instruct_1.md §2: run them CONCURRENTLY (both depend on the same
        # parallel-phase outputs; no data dependency between the two). On a
        # single GPU1 the two clinical_main calls time-share, but overlap still
        # hides one model's queue/decode latency behind the other's.
        s_task = asyncio.create_task(
            run_module("synthesis", self._synthesis(ctx, w_data, t_data, tg_data)))
        p_task = asyncio.create_task(
            run_module("prescription", self._prescription(ctx, w_data, t_data)))
        th_task = asyncio.create_task(
            run_module("therapy", self._therapy(ctx, w_data, t_data)))
        s_status, s_data = await s_task
        p_status, p_data = await p_task
        th_status, th_data = await th_task

        panel = {
            "western": w_status.value, "tcm": t_status.value,
            "tongue": tg_status.value, "knowledge": ModuleStatus.OK.value,
            "synthesis": s_status.value, "prescription": p_status.value,
            "therapy": th_status.value,
        }

        # instruct #61 status
        oks = sum(1 for v in panel.values() if v == ModuleStatus.OK.value)
        if oks == len(panel):
            status = AIRunStatus.SUCCEEDED
        elif oks == 0:
            status = AIRunStatus.FAILED
        else:
            status = AIRunStatus.PARTIAL

        result = {
            "western": w_data, "tcm": t_data, "tongue": tg_data,
            "synthesis": s_data, "prescription": p_data,
            "therapy": th_data,
            "evidence": ctx.evidence, "panel_status": panel,
        }
        await self.cache.set(ckey, result, ttl=3600)
        await emit("run", "COMPLETE", {"status": status.value, "panel_status": panel,
                   "latency_ms": int((time.time()-t0)*1000)})
        await audit_log(actor=actor, action="ai.run", target=ctx.encounter_id,
                        meta={"run_id": run_id, "status": status.value,
                              "panel": panel, "latency_ms": int((time.time()-t0)*1000)})
        return AnalysisResult(
            run_id=run_id, status=status, latency_ms=int((time.time()-t0)*1000), **result,
        )
