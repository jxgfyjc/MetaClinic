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
    "synthesis": "synthesis_v2",
    "prescription": "prescription_draft_v1.1",
    "therapy": "therapy_programs_v2",
    "treatment_for_diagnosis": "treatment_for_diagnosis_v2",
    "treatment_for_encounter": "treatment_for_encounter_v1",
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
    module_timings: dict = field(default_factory=dict)  # V1.2 §7


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
        ], json_mode=True, timeout=300, max_tokens=1200)
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
        ], json_mode=True, timeout=180, max_tokens=1000)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["tcm"])
        d.setdefault("knowledge_version", KNOWLEDGE_VERSION)
        return ModuleStatus.OK, d

    async def _tongue(self, ctx: ClinicalContext) -> tuple[ModuleStatus, dict]:
        # V1.2 §1: SKIPPED (no input) vs FAILED (real error)
        if not ctx.tongue_image_key:
            return ModuleStatus.SKIPPED, {"note": "no tongue image"}
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("tongue_analysis")
        import os
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        obj_dir = os.path.join(_root, "data", "objects")
        # V1.2 §1: prefer compressed copy for model inference; keep original
        # for doctor viewing. Compressed key convention: tongue_comp_{uuid}.jpg
        orig_key = ctx.tongue_image_key
        # Derive compressed key from original key
        comp_key = orig_key.replace("tongue_orig_", "tongue_comp_")
        # Change extension to .jpg for compressed copy
        comp_base = os.path.splitext(comp_key)[0]
        comp_key = comp_base + ".jpg"
        img_path = os.path.join(obj_dir, comp_key)
        if not os.path.exists(img_path):
            # Fallback to original
            img_path = os.path.join(obj_dir, orig_key)
        if not os.path.exists(img_path):
            return ModuleStatus.FAILED, {"note": "image file not found on disk"}
        try:
            import base64, mimetypes
            mime = mimetypes.guess_type(img_path)[0] or "image/jpeg"
            with open(img_path, "rb") as f:
                img_bytes = f.read()
            b64 = base64.b64encode(img_bytes).decode()
        except Exception as e:
            return ModuleStatus.FAILED, {"note": f"image load failed: {e}"}
        resp = await llm.chat_vision(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": "分析这张舌象图片。"}],
            image_url=f"data:{mime};base64,{b64}", json_mode=True, timeout=300, max_tokens=800,
        )
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["tongue"])
        d.setdefault("image_key", orig_key)
        d.setdefault("used_compressed", comp_key != orig_key)
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
        ], json_mode=True, timeout=300, max_tokens=1000)
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
        ], json_mode=True, timeout=300, max_tokens=600)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["therapy"])
        return ModuleStatus.OK, d

    async def generate_prescription_for_diagnosis(self, ctx: ClinicalContext,
                                                   diagnosis: dict) -> tuple[ModuleStatus, dict]:
        """V1.2 §2: generate prescription for a SINGLE diagnosis (not all-at-once).

        Only called when a doctor accepts a specific diagnosis. Input is the
        diagnosis record (western_diagnosis/tcm_syndrome/main_evidence/
        treatment_goal), NOT the full western+tcm output.
        """
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("prescription_draft")
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
            "target_diagnosis": {
                "western_diagnosis": diagnosis.get("western_diagnosis", ""),
                "tcm_syndrome": diagnosis.get("tcm_syndrome", ""),
                "main_evidence": diagnosis.get("main_evidence", ""),
                "treatment_goal": diagnosis.get("treatment_goal", ""),
                "module": diagnosis.get("module", "western"),
            },
            "doctor_preference": doc_pref,
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300, max_tokens=600)
        d = _safe_json(resp.text)
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["prescription"])
        d.setdefault("target_diagnosis_id", diagnosis.get("id", ""))
        return ModuleStatus.OK, d

    async def generate_treatment_for_diagnosis(self, ctx: ClinicalContext,
                                                diagnosis: dict) -> tuple[ModuleStatus, dict]:
        """Combined prescription + therapy generation for a SINGLE confirmed diagnosis.

        ONE LLM call produces both sections → halves GPU calls (1 vs 2) → ~20-30s
        with capped max_tokens=1000 and small per-diagnosis prompt. This is the
        accelerated path replacing the old all-at-once prescription/therapy in run().
        """
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("treatment_for_diagnosis")
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
            "target_diagnosis": {
                "western_diagnosis": diagnosis.get("western_diagnosis", ""),
                "tcm_syndrome": diagnosis.get("tcm_syndrome", ""),
                "main_evidence": diagnosis.get("main_evidence", ""),
                "treatment_goal": diagnosis.get("treatment_goal", ""),
                "module": diagnosis.get("module", "western"),
            },
            "hospital_programs": [
                {"name": p.get("name", ""), "category": p.get("category", ""),
                 "hospital_code": p.get("hospital_code", ""),
                 "applicable_scenarios": p.get("applicable_scenarios", "")}
                for p in ctx.hospital_programs
            ],
            "doctor_preference": doc_pref,
        }, ensure_ascii=False)
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300, max_tokens=3500)
        d = _safe_json(resp.text)
        # Normalize: the model sometimes inlines prescription fields at the top
        # level alongside "therapy" instead of nesting them under "prescription".
        # Move any inlined prescription fields into a "prescription" key so that
        # finalize + PDF export + the frontend 【综合治疗方案】 block all find them.
        _PRESC_KEYS = ("western_medications", "patent_medications",
                       "patent_medicines", "herbal_drafts", "safety_notes")
        if "prescription" not in d:
            presc_inline = {k: d[k] for k in _PRESC_KEYS if k in d}
            if presc_inline:
                d["prescription"] = presc_inline
        if "therapy" not in d and "prescription" not in d:
            # single therapy-like object with no wrapper
            if any(k in d for k in ("category", "applicable_scenario",
                                     "therapeutic_goal", "acupoints")):
                d = {"therapy": {"candidates": [d]}}
        d.setdefault("unified_plan_summary", "")
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["treatment_for_diagnosis"])
        d.setdefault("target_diagnosis_id", diagnosis.get("id", ""))
        return ModuleStatus.OK, d

    async def generate_treatment_for_encounter(self, ctx: ClinicalContext,
                                                diagnoses: list[dict]) -> tuple[ModuleStatus, dict]:
        """updatev2_3 §1: unified treatment plan for ALL confirmed compatible
        diagnoses of one encounter.

        Replaces the per-diagnosis path: one LLM call produces a single
        comprehensive plan integrating western drugs, patent medicines, herbal
        decoctions, classic formulas, and applicable therapy/rehab — merging
        overlapping medications and flagging contradictions across diagnoses.
        ``diagnoses`` is the list of confirmed (accepted/modified) diagnosis
        records; the resulting plan is encounter-scoped
        (target_diagnosis_id="" sentinel = comprehensive).
        """
        try:
            llm = LLMProvider("clinical_main")
        except AIUnavailable:
            return ModuleStatus.UNAVAILABLE, {}
        sys_p = _load_prompt("treatment_for_encounter")
        doc_pref = ""
        if ctx.doctor_profile:
            doc_pref = json.dumps({
                "formula_preference": ctx.doctor_profile.get("formula_preference", ""),
                "herb_preference": ctx.doctor_profile.get("herb_preference", ""),
                "non_drug_preference": ctx.doctor_profile.get("non_drug_preference", ""),
            }, ensure_ascii=False)
        confirmed_diagnoses = [
            {
                "module": d.get("module", "western"),
                "western_diagnosis": d.get("western_diagnosis", ""),
                "tcm_syndrome": d.get("tcm_syndrome", ""),
                "main_evidence": d.get("main_evidence", ""),
                "treatment_goal": d.get("treatment_goal", ""),
                "review_status": d.get("review_status", "accepted"),
            }
            for d in diagnoses
        ]
        user = json.dumps({
            "patient": _slim_patient(ctx.patient), "allergies": ctx.allergies,
            "medications": ctx.medications, "labs": ctx.labs,
            "confirmed_diagnoses": confirmed_diagnoses,
            "hospital_programs": [
                {"name": p.get("name", ""), "category": p.get("category", ""),
                 "hospital_code": p.get("hospital_code", ""),
                 "applicable_scenarios": p.get("applicable_scenarios", "")}
                for p in ctx.hospital_programs
            ],
            "doctor_preference": doc_pref,
        }, ensure_ascii=False)
        # Scale max_tokens with diagnosis count to avoid truncating full
        # herbal formulas (a 10-12 herb formula with doses + therapy block +
        # acupoints needs well above 2200 tokens). Cap at 4500.
        max_tok = min(4500, 3500 + 500 * max(0, len(confirmed_diagnoses) - 1))
        resp = await llm.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user},
        ], json_mode=True, timeout=300, max_tokens=max_tok)
        d = _safe_json(resp.text)
        # Truncation recovery: the prompt asks for top-level "prescription" and
        # "therapy" keys, but the model sometimes inlines prescription fields
        # (western_medications, herbal_drafts, etc.) at the top level alongside
        # "therapy". Normalize so prescription fields are always nested under a
        # "prescription" key — otherwise finalize + PDF export can't find them.
        _PRESC_KEYS = ("western_medications", "patent_medications",
                       "patent_medicines", "herbal_drafts", "safety_notes")
        if "prescription" not in d:
            presc_inline = {k: d[k] for k in _PRESC_KEYS if k in d}
            if presc_inline:
                d["prescription"] = presc_inline
        if "therapy" not in d and "prescription" not in d:
            # single therapy-like object with no wrapper
            if any(k in d for k in ("category", "applicable_scenario",
                                    "therapeutic_goal", "acupoints")):
                d = {"therapy": {"candidates": [d]}}
        d.setdefault("unified_plan_summary", "")
        d.setdefault("model_name", llm.model_id)
        d.setdefault("prompt_version", PROMPT_VERSIONS["treatment_for_encounter"])
        # encounter-scoped comprehensive plan: empty sentinel, NOT a per-diagnosis id
        d.setdefault("target_diagnosis_id", "")
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
        # V1.2 §7: per-module timing
        module_timings: dict[str, dict] = {}

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
            # prescription/therapy are now deferred to per-diagnosis path
            await emit("prescription", "SKIPPED", {"note": "per-diagnosis path"})
            await emit("therapy", "SKIPPED", {"note": "per-diagnosis path"})
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
        # prescription/therapy deferred — generated per-diagnosis after doctor review
        await emit("prescription", "SKIPPED", {"note": "per-diagnosis path"})
        await emit("therapy", "SKIPPED", {"note": "per-diagnosis path"})

        async def run_module(name: str, coro):
            """Wrap a runner: emit RUNNING, await, emit COMPLETE/FAILED,
            persist the output via on_progress. V1.2 §7: record per-module timing."""
            t_start = time.time()
            await emit(name, "RUNNING")
            status, data = await coro
            t_end = time.time()
            duration_ms = int((t_end - t_start) * 1000)
            module_timings[name] = {
                "start_ms": int(t_start * 1000) % 1000000,
                "end_ms": int(t_end * 1000) % 1000000,
                "duration_ms": duration_ms,
            }
            if status == ModuleStatus.OK:
                await emit(name, "COMPLETE", data)
            elif status == ModuleStatus.SKIPPED:
                await emit(name, "SKIPPED", data)
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

        # synthesis needs western+tcm. prescription and therapy are now deferred
        # to the per-diagnosis path (generate_treatment_for_diagnosis) — the
        # doctor accepts/rejects specific diagnoses first, then we generate a
        # combined treatment for only the confirmed diagnosis. This drops 2 of
        # 3 serial clinical_main GPU calls from the main run (~100s saved).
        s_task = asyncio.create_task(
            run_module("synthesis", self._synthesis(ctx, w_data, t_data, tg_data)))
        s_status, s_data = await s_task
        # prescription/therapy are deferred — not run in main flow
        p_status = ModuleStatus.SKIPPED
        p_data = {"note": "generated per-diagnosis after doctor review"}
        th_status = ModuleStatus.SKIPPED
        th_data = {"note": "generated per-diagnosis after doctor review"}

        panel = {
            "western": w_status.value, "tcm": t_status.value,
            "tongue": tg_status.value, "knowledge": ModuleStatus.OK.value,
            "synthesis": s_status.value,
            "prescription": p_status.value,
            "therapy": th_status.value,
        }

        # instruct #61 status — SKIPPED modules don't count as failures
        real_modules = {k: v for k, v in panel.items()
                        if v not in (ModuleStatus.SKIPPED.value,)}
        oks = sum(1 for v in real_modules.values() if v == ModuleStatus.OK.value)
        if oks == len(real_modules):
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
        total_ms = int((time.time()-t0)*1000)
        await emit("run", "COMPLETE", {"status": status.value, "panel_status": panel,
                   "latency_ms": total_ms, "module_timings": module_timings})
        await audit_log(actor=actor, action="ai.run", target=ctx.encounter_id,
                        meta={"run_id": run_id, "status": status.value,
                              "panel": panel, "latency_ms": total_ms,
                              "module_timings": module_timings})
        return AnalysisResult(
            run_id=run_id, status=status, latency_ms=total_ms,
            module_timings=module_timings, **result,
        )
