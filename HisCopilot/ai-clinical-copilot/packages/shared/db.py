"""Shared DB layer — SQLAlchemy async models + session.

instruct #35: core tables. MVP uses SQLite+sqlite-vec; connection string from settings.
Allergy/medication/lab/tongue/ai_run/ai_output/evidence/feedback/knowledge/prescription/
followup/audit tables declared here. Server-controlled status transitions only
(instruct #66).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _uid() -> str:
    return uuid.uuid4().hex[:24]


class Base(DeclarativeBase):
    pass


class RoleEnum(str, enum.Enum):
    ADMIN = "ADMIN"
    DOCTOR = "DOCTOR"
    NURSE = "NURSE"
    PHARMACIST = "PHARMACIST"
    PATIENT = "PATIENT"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="DOCTOR")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(64))
    gender: Mapped[str] = mapped_column(String(8), default="")
    age: Mapped[int] = mapped_column(Integer, default=0)
    phone: Mapped[str] = mapped_column(String(20), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)  # instruct #51 DEMO ONLY
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict)  # V1.0: allergies/insurance/id_card/address
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Allergy(Base):
    __tablename__ = "allergies"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    allergen: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16), default="")


class Medication(Base):
    __tablename__ = "medications"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    drug_name: Mapped[str] = mapped_column(String(128))
    dose: Mapped[str] = mapped_column(String(64), default="")
    frequency: Mapped[str] = mapped_column(String(64), default="")
    start_time: Mapped[str] = mapped_column(String(32), default="")


class Encounter(Base):
    __tablename__ = "encounters"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), default="")
    encounter_no: Mapped[str] = mapped_column(String(32), default="")
    chief_complaint: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/IN_PROGRESS/AI_REVIEW/SIGNED/SUBMITTED
    clinical_data: Mapped[dict] = mapped_column(JSON, default=dict)  # V1.0: history/raw_text/medications/allergies/labs/case_type/source
    # updatev2_3 §2c: link encounter to its final (signed/submitted) prescription.
    # The column migration already exists (_migrate_add_columns); this is the
    # ORM-side declaration that was missing so the ORM can read/write it.
    final_prescription_id: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class MedicalRecord(Base):
    __tablename__ = "medical_records"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)  # structured medical history
    raw_text: Mapped[str] = mapped_column(Text, default="")  # instruct #12 keep raw
    # updatev2_2 §6: final-record status + finalized metadata (additive columns)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/finalized
    finalized_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    doctor_id: Mapped[str] = mapped_column(String(24), default="")
    signature_id: Mapped[str] = mapped_column(String(24), default="")
    followup_plan_id: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Symptom(Base):
    __tablename__ = "symptoms"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    duration: Mapped[str] = mapped_column(String(32), default="")
    severity: Mapped[str] = mapped_column(String(16), default="")


class LabReport(Base):
    __tablename__ = "lab_reports"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    source_image_id: Mapped[str] = mapped_column(String(24), default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    test_time: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class LabResult(Base):
    __tablename__ = "lab_results"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    lab_report_id: Mapped[str] = mapped_column(String(24), ForeignKey("lab_reports.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    test_name: Mapped[str] = mapped_column(String(128))
    canonical_name: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(32), default="")
    reference_low: Mapped[str] = mapped_column(String(32), default="")
    reference_high: Mapped[str] = mapped_column(String(32), default="")
    abnormal_flag: Mapped[str] = mapped_column(String(16), default="")
    test_time: Mapped[str] = mapped_column(String(32), default="")
    source_image_id: Mapped[str] = mapped_column(String(24), default="")
    ocr_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    normalization_confidence: Mapped[float] = mapped_column(Float, default=0.0)


class TongueImage(Base):
    __tablename__ = "tongue_images"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class TongueObservation(Base):
    __tablename__ = "tongue_observations"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    tongue_image_id: Mapped[str] = mapped_column(String(24), ForeignKey("tongue_images.id"), index=True)
    features_json: Mapped[dict] = mapped_column(JSON, default=dict)
    observations: Mapped[list] = mapped_column(JSON, default=list)
    not_observed: Mapped[list] = mapped_column(JSON, default=list)
    uncertain: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    model_name: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")


# ---------------------------------------------------------------------------
# AI run / output / evidence / feedback (instruct #36, #37)
# ---------------------------------------------------------------------------
class AIRun(Base):
    __tablename__ = "ai_runs"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    model_name: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    knowledge_version: Mapped[str] = mapped_column(String(32), default="")
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    output_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/RUNNING/SUCCEEDED/PARTIAL/FAILED/TIMEOUT
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # V1.2 §7: per-module timing {module: {start_ms, end_ms, duration_ms}}
    module_timings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AIOutput(Base):
    __tablename__ = "ai_outputs"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    ai_run_id: Mapped[str] = mapped_column(String(24), ForeignKey("ai_runs.id"), index=True)
    output_type: Mapped[str] = mapped_column(String(32))  # western/tcm/tongue/synthesis/prescription
    structured_json: Mapped[dict] = mapped_column(JSON, default=dict)
    display_text: Mapped[str] = mapped_column(Text, default="")


class AIEvidence(Base):
    __tablename__ = "ai_evidence"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    ai_output_id: Mapped[str] = mapped_column(String(24), ForeignKey("ai_outputs.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    trust_tier: Mapped[str] = mapped_column(String(32), default="TIER_E_OPEN_CORPUS")
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)


class AIFeedback(Base):
    __tablename__ = "ai_feedback"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    ai_output_id: Mapped[str] = mapped_column(String(24), ForeignKey("ai_outputs.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))  # accept/odify/reject
    original_content: Mapped[str] = mapped_column(Text, default="")
    edited_content: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------
class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(32))  # textbook/guideline/expert/hospital...
    domain: Mapped[str] = mapped_column(String(32), default="western")  # tcm/western/...
    trust_tier: Mapped[str] = mapped_column(String(32), default="TIER_E_OPEN_CORPUS")
    version: Mapped[str] = mapped_column(String(32), default="")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    source_id: Mapped[str] = mapped_column(String(24), ForeignKey("knowledge_sources.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    author: Mapped[str] = mapped_column(String(128), default="")
    book: Mapped[str] = mapped_column(String(128), default="")
    chapter: Mapped[str] = mapped_column(String(128), default="")
    year: Mapped[int] = mapped_column(Integer, default=0)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    object_key: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    document_id: Mapped[str] = mapped_column(String(24), index=True)
    source_id: Mapped[str] = mapped_column(String(24), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # vec0 rowid for the side-table vector (vec0 only accepts INTEGER primary keys)
    vec_rowid: Mapped[int] = mapped_column(Integer, index=True, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Drug / Formula (instruct #41/#42)
# ---------------------------------------------------------------------------
class Drug(Base):
    __tablename__ = "drugs"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(128), index=True)
    generic_name: Mapped[str] = mapped_column(String(128), default="")
    spec: Mapped[str] = mapped_column(String(128), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    form: Mapped[str] = mapped_column(String(32), default="")
    # V1.2 §3: usage (用法) + source (来源) + category (western/tcm-herb)
    usage: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="demo")  # demo/hospital/imported
    category: Mapped[str] = mapped_column(String(32), default="western", index=True)  # western/tcm-herb
    his_code: Mapped[str] = mapped_column(String(32), default="")
    insurance_code: Mapped[str] = mapped_column(String(32), default="")
    contraindication: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")


class Formula(Base):
    __tablename__ = "formula"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32), default="classic")  # classic/hospital/ai_generated
    ingredients_json: Mapped[dict] = mapped_column(JSON, default=dict)
    indication: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewer: Mapped[str] = mapped_column(String(64), default="")
    effective_time: Mapped[str] = mapped_column(String(32), default="")


# ---------------------------------------------------------------------------
# Doctor profile (instruct_1.md §3) — lightweight personalization, no per-doctor model.
# ---------------------------------------------------------------------------
class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), primary_key=True)
    dept: Mapped[str] = mapped_column(String(64), default="")
    specialties: Mapped[list] = mapped_column(JSON, default=list)  # 擅长领域
    syndrome_preference: Mapped[str] = mapped_column(Text, default="")  # 辨证偏好
    formula_preference: Mapped[str] = mapped_column(Text, default="")  # 经方/协定方偏好
    herb_preference: Mapped[str] = mapped_column(Text, default="")  # 组方加减偏好
    non_drug_preference: Mapped[str] = mapped_column(Text, default="")  # 非药物组合偏好
    analysis_focus: Mapped[str] = mapped_column(Text, default="")  # 分析重点偏好
    experience_notes: Mapped[list] = mapped_column(JSON, default=list)  # 个人经验条目
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Therapy programs (instruct_1.md §4) — independent from formula library.
# 推拿/针灸/艾灸/理疗/康复, structured: 项目名称/适用场景/治疗目的/频次时长/禁忌/院内编码/来源.
# source_type: hospital (院内现有) / knowledge (知识库). ai_generated candidates are
# produced at runtime by the therapy module, never stored as catalog rows.
# ---------------------------------------------------------------------------
class TherapyProgram(Base):
    __tablename__ = "therapy_programs"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)  # 推拿/针灸/艾灸/理疗/康复
    applicable_scenarios: Mapped[str] = mapped_column(Text, default="")
    therapeutic_goal: Mapped[str] = mapped_column(Text, default="")
    frequency_duration: Mapped[str] = mapped_column(String(128), default="")
    contraindications: Mapped[str] = mapped_column(Text, default="")
    hospital_code: Mapped[str] = mapped_column(String(32), default="")  # 院内项目编码 (only if hospital)
    source_type: Mapped[str] = mapped_column(String(16), default="hospital")  # hospital/knowledge
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PrescriptionDraft(Base):
    __tablename__ = "prescription_drafts"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), default="")
    ai_run_id: Mapped[str] = mapped_column(String(24), ForeignKey("ai_runs.id"), default="")
    structured_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # instruct #66 server-controlled status
    status: Mapped[str] = mapped_column(String(24), default="AI_GENERATED")
    # AI_GENERATED/DOCTOR_EDITING/DOCTOR_CONFIRMED/SIGNED/SUBMITTED_TO_HIS/HIS_ACCEPTED/CANCELLED
    # V1.2 §2: link prescription to a specific diagnosis (empty = comprehensive)
    target_diagnosis_id: Mapped[str] = mapped_column(String(24), default="", index=True)
    # updatev2_3 §1c: stale flag — when diagnoses change, mark old drafts stale.
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    # updatev2_3 §1c: diagnosis snapshot hash — guards against silent reuse of
    # mismatched prescriptions. Generated from the confirmed diagnoses that were
    # used as input when the plan was generated.
    diagnosis_snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PrescriptionReview(Base):
    __tablename__ = "prescription_reviews"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    prescription_id: Mapped[str] = mapped_column(String(24), ForeignKey("prescription_drafts.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# updatev2_3 §2a: E-Prescription — formal, persisted prescription document.
# Generated from a confirmed TreatmentPlan (PrescriptionDraft) after doctor
# sign + submit. Survives API restart (unlike MockHIS in-memory list). One
# encounter can have multiple e-prescriptions (western drugs, herbal decoction,
# etc.) per applicable norms. Supports query, print/PDF export, pharmacy review.
# ---------------------------------------------------------------------------
class EPrescription(Base):
    __tablename__ = "e_prescriptions"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), index=True)
    doctor_id: Mapped[str] = mapped_column(String(24), default="")
    # link back to the TreatmentPlan that produced this formal prescription
    prescription_draft_id: Mapped[str] = mapped_column(String(24), default="", index=True)
    # which document type: western_drugs / herbal_decoction / patent_medicine / therapy_orders
    doc_type: Mapped[str] = mapped_column(String(32), default="western_drugs")
    # human-readable diagnosis summary this prescription targets
    diagnosis_summary: Mapped[str] = mapped_column(Text, default="")
    # structured: drugs with spec/dose/usage/quantity, or herbs with doses
    prescription_json: Mapped[dict] = mapped_column(JSON, default=dict)
    signature_id: Mapped[str] = mapped_column(String(24), default="")
    doctor_name: Mapped[str] = mapped_column(String(64), default="")
    his_status: Mapped[str] = mapped_column(String(16), default="ACCEPTED")
    request_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Followup (instruct #31-#34)
# ---------------------------------------------------------------------------
class FollowupPlan(Base):
    __tablename__ = "followup_plans"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    template: Mapped[str] = mapped_column(String(32), default="custom")
    days: Mapped[int] = mapped_column(Integer, default=14)
    questions_json: Mapped[dict] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class FollowupResponse(Base):
    __tablename__ = "followup_responses"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    followup_plan_id: Mapped[str] = mapped_column(String(24), ForeignKey("followup_plans.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    answers_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class FollowupRiskEvent(Base):
    __tablename__ = "followup_risk_events"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    followup_response_id: Mapped[str] = mapped_column(String(24), ForeignKey("followup_responses.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    rule_triggered: Mapped[str] = mapped_column(String(255), default="")
    risk_level: Mapped[str] = mapped_column(String(16), default="normal")
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Audit (instruct #38) — DB-mirrored index; canonical store is audit.ndjson
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    actor: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(128), default="")
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# V1.2 §2: Diagnosis record — AI candidate + doctor review + final diagnosis
# ---------------------------------------------------------------------------
class DiagnosisRecord(Base):
    __tablename__ = "diagnosis_records"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    encounter_id: Mapped[str] = mapped_column(String(24), ForeignKey("encounters.id"), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), default="")
    ai_output_id: Mapped[str] = mapped_column(String(24), default="", index=True)  # link to ai_outputs.id
    module: Mapped[str] = mapped_column(String(16), default="western")  # western/tcm
    western_diagnosis: Mapped[str] = mapped_column(Text, default="")
    tcm_syndrome: Mapped[str] = mapped_column(Text, default="")
    main_evidence: Mapped[str] = mapped_column(Text, default="")
    treatment_goal: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/accepted/modified/rejected
    doctor_notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(8), default="ai")  # ai/doctor
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# V1.2 §6: Doctor access token for QR code → patient intake binding
# ---------------------------------------------------------------------------
class DoctorAccessToken(Base):
    __tablename__ = "doctor_access_tokens"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    used_count: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# V1.2 §5: Case import for doctor experience mining
# ---------------------------------------------------------------------------
class CaseImport(Base):
    __tablename__ = "case_imports"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/processing/done/failed
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    cases_json: Mapped[dict] = mapped_column(JSON, default=dict)  # input cases
    structured_json: Mapped[dict] = mapped_column(JSON, default=dict)  # structured results
    patterns_json: Mapped[dict] = mapped_column(JSON, default=dict)  # mined patterns
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ExperiencePattern(Base):
    __tablename__ = "experience_patterns"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    doctor_id: Mapped[str] = mapped_column(String(24), ForeignKey("users.id"), index=True)
    case_import_id: Mapped[str] = mapped_column(String(24), default="", index=True)
    category: Mapped[str] = mapped_column(String(32), default="syndrome")  # disease/syndrome/herb/method
    pattern_text: Mapped[str] = mapped_column(Text, default="")
    source_cases: Mapped[list] = mapped_column(JSON, default=list)  # traceable case IDs
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    conditions: Mapped[str] = mapped_column(Text, default="")
    exceptions: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# updatev2_2 §5: Therapy item review — per-item accept/edit/reject status.
# Each therapy candidate from AI gets its own status, separate from the
# prescription draft it belongs to. Original + edited content preserved.
# ---------------------------------------------------------------------------
class TherapyItemReview(Base):
    __tablename__ = "therapy_item_reviews"
    id: Mapped[str] = mapped_column(String(24), primary_key=True, default=_uid)
    prescription_draft_id: Mapped[str] = mapped_column(String(24), ForeignKey("prescription_drafts.id"), index=True)
    encounter_id: Mapped[str] = mapped_column(String(24), index=True)
    patient_id: Mapped[str] = mapped_column(String(24), default="")
    item_index: Mapped[int] = mapped_column(Integer, default=0)  # index in therapy.candidates[]
    status: Mapped[str] = mapped_column(String(16), default="AI_GENERATED")  # AI_GENERATED/ACCEPTED/EDITED/REJECTED
    original_content: Mapped[dict] = mapped_column(JSON, default=dict)
    edited_content: Mapped[dict] = mapped_column(JSON, default=dict)
    doctor_id: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))


_engine = None
_sessionmaker = None


def _migrate_add_columns(connection):
    """V1.0: add clinical_data/extra_json columns to existing SQLite tables.

    create_all only creates NEW tables — it does NOT add columns to existing
    tables. For existing DBs, we ALTER TABLE ADD COLUMN if the column is missing.
    SQLite supports this; check via PRAGMA table_info first.
    """
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(connection)

    def _has_col(table: str, col: str) -> bool:
        try:
            return any(c["name"] == col for c in insp.get_columns(table))
        except Exception:
            return False

    try:
        if not _has_col("encounters", "clinical_data"):
            connection.execute(text("ALTER TABLE encounters ADD COLUMN clinical_data TEXT DEFAULT '{}'"))
        if not _has_col("patients", "extra_json"):
            connection.execute(text("ALTER TABLE patients ADD COLUMN extra_json TEXT DEFAULT '{}'"))
        # V1.2 migrations: add new columns to existing tables
        if not _has_col("drugs", "usage"):
            connection.execute(text("ALTER TABLE drugs ADD COLUMN usage TEXT DEFAULT ''"))
        if not _has_col("drugs", "source"):
            connection.execute(text("ALTER TABLE drugs ADD COLUMN source VARCHAR(32) DEFAULT 'demo'"))
        if not _has_col("drugs", "category"):
            connection.execute(text("ALTER TABLE drugs ADD COLUMN category VARCHAR(32) DEFAULT 'western'"))
        if not _has_col("ai_runs", "module_timings_json"):
            connection.execute(text("ALTER TABLE ai_runs ADD COLUMN module_timings_json TEXT DEFAULT '{}'"))
        if not _has_col("prescription_drafts", "target_diagnosis_id"):
            connection.execute(text("ALTER TABLE prescription_drafts ADD COLUMN target_diagnosis_id VARCHAR(24) DEFAULT ''"))
        # updatev2_2 §6: medical_records status columns
        if not _has_col("medical_records", "status"):
            connection.execute(text("ALTER TABLE medical_records ADD COLUMN status VARCHAR(16) DEFAULT 'draft'"))
        if not _has_col("medical_records", "finalized_at"):
            connection.execute(text("ALTER TABLE medical_records ADD COLUMN finalized_at DATETIME"))
        if not _has_col("medical_records", "doctor_id"):
            connection.execute(text("ALTER TABLE medical_records ADD COLUMN doctor_id VARCHAR(24) DEFAULT ''"))
        if not _has_col("medical_records", "signature_id"):
            connection.execute(text("ALTER TABLE medical_records ADD COLUMN signature_id VARCHAR(24) DEFAULT ''"))
        if not _has_col("medical_records", "followup_plan_id"):
            connection.execute(text("ALTER TABLE medical_records ADD COLUMN followup_plan_id VARCHAR(24) DEFAULT ''"))
        if not _has_col("medical_records", "updated_at"):
            connection.execute(text("ALTER TABLE medical_records ADD COLUMN updated_at DATETIME"))
        # updatev2_2 §3: ai_runs stale flag
        if not _has_col("ai_runs", "is_stale"):
            connection.execute(text("ALTER TABLE ai_runs ADD COLUMN is_stale BOOLEAN DEFAULT 0"))
        # updatev2_2 §4: encounters final prescription link
        if not _has_col("encounters", "final_prescription_id"):
            connection.execute(text("ALTER TABLE encounters ADD COLUMN final_prescription_id VARCHAR(24) DEFAULT ''"))
        # updatev2_3 §1c: prescription_drafts stale flag + diagnosis snapshot hash
        if not _has_col("prescription_drafts", "is_stale"):
            connection.execute(text("ALTER TABLE prescription_drafts ADD COLUMN is_stale BOOLEAN DEFAULT 0"))
        if not _has_col("prescription_drafts", "diagnosis_snapshot_hash"):
            connection.execute(text("ALTER TABLE prescription_drafts ADD COLUMN diagnosis_snapshot_hash VARCHAR(64) DEFAULT ''"))
    except Exception as e:
        print(f"[db] migration (clinical_data/extra_json) skipped: {e}", flush=True)


async def init_engine(database_url: str | None = None):
    from .config import get_settings
    global _engine, _sessionmaker
    url = database_url or get_settings().database_url
    _engine = create_async_engine(url, echo=False, future=True)
    # NOTE: sqlite-vec is NOT loaded on the SQLAlchemy aiosqlite connection here.
    # The aiosqlite DBAPI wrapper exposes load_extension via an async queue that
    # deadlocks when called from SQLAlchemy's sync "connect" event (which runs
    # inside aiosqlite's own background thread). Instead, the vec0 virtual table
    # for knowledge chunks is created and used through standalone aiosqlite
    # connections in services/knowledge/rag.py (KnowledgeStore), which loads
    # sqlite_vec directly. The SQLAlchemy engine only manages the relational
    # tables (patients, encounters, ai_runs, audit, ...).

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # V1.0 migration: add clinical_data/extra_json columns to existing SQLite DBs.
        # create_all only creates new tables; it does NOT add columns to existing tables.
        # SQLite supports ALTER TABLE ADD COLUMN — use PRAGMA to check first.
        await conn.run_sync(_migrate_add_columns)
    # vector virtual table via a dedicated aiosqlite connection (best-effort;
    # if sqlite_vec is unavailable, RAG falls back to keyword-only search).
    if url.startswith("sqlite"):
        try:
            await _init_vec0_table(url)
        except Exception as e:
            print(f"[db] knowledge_chunk_vec create skipped (sqlite_vec): {e}", flush=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def _init_vec0_table(url: str) -> None:
    """Create the vec0 virtual table on a raw aiosqlite connection that loads
    sqlite_vec itself. SQLAlchemy's aiosqlite wrapper can't load extensions
    reliably from its connect hook (thread/queue deadlock), so the vector table
    lives outside the ORM engine and is touched only by KnowledgeStore."""
    import sqlite_vec, aiosqlite
    from .config import REPO_ROOT
    path = url.replace("sqlite+aiosqlite:///", "")
    if not path.startswith("/"):
        path = str(REPO_ROOT / path)
    # vec0 dim = 4096 (Qwen3-Embedding-8B native dim). 8192 was wrong.
    async with aiosqlite.connect(path) as db:
        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        await db.enable_load_extension(False)
        await db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunk_vec "
            "USING vec0(embedding float[4096])"
        )
        await db.commit()


async def get_session() -> AsyncSession:
    if _sessionmaker is None:
        await init_engine()
    async with _sessionmaker() as s:
        yield s


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the module-level async session factory, initializing if needed.

    Used by the repository layer (packages/shared/repo.py) for short-lived
    sessions outside the FastAPI Depends flow. Each repo call opens its own
    session via `async with get_sessionmaker()() as s`.
    """
    if _sessionmaker is None:
        raise RuntimeError("db engine not initialized; call init_engine() first")
    return _sessionmaker
