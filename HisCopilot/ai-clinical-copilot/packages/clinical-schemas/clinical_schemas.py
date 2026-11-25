"""Clinical Schemas — Pydantic models for structured AI output.

instruct #46: 所有核心 AI 模块必须使用 Pydantic Schema。
instruct #15/#16: 西医/中医分析的结构化输出契约。
instruct #21: RAG 提供证据，模型自主推理；不伪造来源。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 通用枚举
# ---------------------------------------------------------------------------
class ConfidenceLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class EvidenceStatus(str, Enum):
    observed = "observed"
    not_observed = "not_observed"
    uncertain = "uncertain"


class AIRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class TrustTier(str, Enum):
    """instruct #19 知识库分层"""
    A = "TIER_A_HOSPITAL"
    B = "TIER_B_OFFICIAL"
    C = "TIER_C_TEXTBOOK"
    D = "TIER_D_SPECIALIST_MODEL"
    E = "TIER_E_OPEN_CORPUS"


# ---------------------------------------------------------------------------
# 患者结构化病历（instruct #12）
# ---------------------------------------------------------------------------
class StructuredMedicalHistory(BaseModel):
    chief_complaint: str = ""
    history_of_present_illness: str = ""
    past_medical_history: list[str] = Field(default_factory=list)
    medication_history: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    family_history: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    duration: str = ""
    patient_raw_text: str = ""  # instruct #12 禁止丢失原始文本


# ---------------------------------------------------------------------------
# 舌象（instruct #14）
# ---------------------------------------------------------------------------
class TongueImageQuality(BaseModel):
    score: float = 0.0
    passed: bool = True
    issues: list[str] = Field(default_factory=list)


class TongueFeatures(BaseModel):
    tongue_color: str = ""
    tongue_shape: str = ""
    tongue_body: str = ""
    coating_color: str = ""
    coating_thickness: str = ""
    coating_texture: str = ""
    greasy: bool = False
    teeth_marks: bool = False
    cracks: bool = False
    moisture: str = ""


class TongueAnalysis(BaseModel):
    """instruct #14: observed / not_observed / uncertain 三态"""
    image_quality: TongueImageQuality = Field(default_factory=TongueImageQuality)
    tongue_features: TongueFeatures = Field(default_factory=TongueFeatures)
    observations: list[str] = Field(default_factory=list)
    not_observed: list[str] = Field(default_factory=list)
    uncertain: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    model_name: str = ""
    prompt_version: str = ""


# ---------------------------------------------------------------------------
# 西医分析（instruct #15）
# ---------------------------------------------------------------------------
class DifferentialDiagnosis(BaseModel):
    diagnosis: str
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    additional_information_needed: list[str] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.low


class WesternAnalysis(BaseModel):
    summary: str = ""
    problem_list: list[str] = Field(default_factory=list)
    clinical_findings: list[str] = Field(default_factory=list)
    differential_diagnoses: list[DifferentialDiagnosis] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    suggested_examinations: list[str] = Field(default_factory=list)
    clinical_reasoning: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)  # RAG source ids
    model_name: str = ""
    prompt_version: str = ""
    knowledge_version: str = ""


# ---------------------------------------------------------------------------
# 中医分析（instruct #16）
# ---------------------------------------------------------------------------
class TCMFourDiagnoses(BaseModel):
    wang: dict[str, list[str]] = Field(default_factory=lambda: {
        "tongue": [], "facial": [], "other": []
    })
    wen: list[str] = Field(default_factory=list)  # 闻
    wen_questionnaire: list[str] = Field(default_factory=list)  # 问
    qie: list[str] = Field(default_factory=list)  # 切


class TCMSyndromeCandidate(BaseModel):
    name: str
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.low


class TCMAnalysis(BaseModel):
    four_diagnoses: TCMFourDiagnoses = Field(default_factory=TCMFourDiagnoses)
    syndrome_candidates: list[TCMSyndromeCandidate] = Field(default_factory=list)
    treatment_principles: list[str] = Field(default_factory=list)
    formula_candidates: list[str] = Field(default_factory=list)
    herb_candidates: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    model_name: str = ""
    prompt_version: str = ""
    knowledge_version: str = ""


# ---------------------------------------------------------------------------
# 处方草稿（instruct #23/#24/#25）
# ---------------------------------------------------------------------------
class WesternMedicationDraft(BaseModel):
    medication: str
    reason: str = ""
    dose: str = ""
    frequency: str = ""
    duration: str = ""
    considerations: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    in_hospital_dict: Optional[bool] = None  # None=未知, True=院内, False=院内未配置


class HerbalDraft(BaseModel):
    formula_type: Literal["classic", "hospital", "ai_generated"]
    name: str
    herbs: list[str] = Field(default_factory=list)
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class PrescriptionDraft(BaseModel):
    western_medications: list[WesternMedicationDraft] = Field(default_factory=list)
    patent_medicines: list[WesternMedicationDraft] = Field(default_factory=list)
    herbal_drafts: list[HerbalDraft] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    model_name: str = ""
    prompt_version: str = ""


# ---------------------------------------------------------------------------
# 理疗方案 (updatev2_1 §1: 专业结构化)
# ---------------------------------------------------------------------------
class TherapyCandidate(BaseModel):
    category: str = ""  # 推拿/针灸/艾灸/理疗/康复训练
    name: str = ""
    source_type: str = ""  # hospital/knowledge/ai_generated
    hospital_code: str = ""
    acupoints: list[str] = Field(default_factory=list)  # 穴位名称
    location_basis: str = ""  # 定位依据
    manipulation: str = ""  # 操作手法
    direction_or_stimulation: str = ""  # 方向或刺激方式
    duration_or_count: str = ""  # 时长/次数
    frequency: str = ""  # 频率
    course: str = ""  # 疗程
    applicable_scenario: str = ""
    therapeutic_goal: str = ""
    contraindications: str = ""
    rationale: str = ""


class TherapyProgram(BaseModel):
    candidates: list[TherapyCandidate] = Field(default_factory=list)
    lifestyle_advice: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    model_name: str = ""
    prompt_version: str = ""


# ---------------------------------------------------------------------------
# 综合分析（instruct #17/#49）
# ---------------------------------------------------------------------------
class ModelConflict(BaseModel):
    western_vs_tcm: str = ""
    notes: list[str] = Field(default_factory=list)


class UnifiedDiagnosis(BaseModel):
    """updatev2_1 §1: 整合中西医的统一诊断评估"""
    western_diagnosis: str = ""
    tcm_syndrome: str = ""
    confidence_level: str = "low"
    conflict_notes: str = ""


class ClinicalSynthesis(BaseModel):
    summary: str = ""
    unified_diagnosis: Optional[UnifiedDiagnosis] = None
    key_findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    coordination_notes: list[str] = Field(default_factory=list)
    model_conflicts: Optional[ModelConflict] = None
    confidence_level: ConfidenceLevel = ConfidenceLevel.low
    model_name: str = ""
    prompt_version: str = ""


# ---------------------------------------------------------------------------
# RAG 证据（instruct #20）
# ---------------------------------------------------------------------------
class EvidenceChunk(BaseModel):
    source_id: str
    title: str = ""
    excerpt: str = ""
    source_type: str = ""
    trust_tier: TrustTier = TrustTier.E
    relevance_score: float = 0.0
    chapter: str = ""
    updated_at: str = ""
    knowledge_version: str = ""


# ---------------------------------------------------------------------------
# 随访与风险（instruct #32/#33）
# ---------------------------------------------------------------------------
class FollowupSummary(BaseModel):
    symptom_changes: list[str] = Field(default_factory=list)
    trend: Literal["improving", "stable", "worsening", "unknown"] = "unknown"
    adherence: str = ""
    adverse_reactions: list[str] = Field(default_factory=list)
    risk_level: Literal["normal", "attention", "high_risk"] = "normal"
    ai_summary: str = ""
    model_name: str = ""
    prompt_version: str = ""


class RiskAssessment(BaseModel):
    risk_level: Literal["normal", "attention", "high_risk"] = "normal"
    triggered_rules: list[str] = Field(default_factory=list)
    triggered_llm_signals: list[str] = Field(default_factory=list)
    escalation: bool = False
    rule_version: str = ""


# ---------------------------------------------------------------------------
# AI Run 元数据（instruct #36）
# ---------------------------------------------------------------------------
class AIRunMeta(BaseModel):
    id: str
    patient_id: str
    encounter_id: str
    model_name: str
    model_version: str
    prompt_version: str
    knowledge_version: str
    input_hash: str
    output_hash: str = ""
    status: AIRunStatus = AIRunStatus.PENDING
    latency_ms: int = 0
    created_at: str = ""


# ---------------------------------------------------------------------------
# Doctor feedback（instruct #37）
# ---------------------------------------------------------------------------
class DoctorFeedback(BaseModel):
    ai_output_id: str
    doctor_id: str
    action: Literal["accept", "modify", "reject"]
    original_content: str = ""
    edited_content: str = ""
    reason: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# 模块状态（instruct #62）
# ---------------------------------------------------------------------------
class ModuleStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    SKIPPED = "skipped"      # V1.2 §1: no input (e.g. no tongue image) — distinct from FAILED
    FAILED = "failed"        # V1.2 §1: real processing error (image corrupt, model error)


class AIPanelStatus(BaseModel):
    western: ModuleStatus = ModuleStatus.OK
    tcm: ModuleStatus = ModuleStatus.OK
    tongue: ModuleStatus = ModuleStatus.OK
    knowledge: ModuleStatus = ModuleStatus.OK
    synthesis: ModuleStatus = ModuleStatus.OK
