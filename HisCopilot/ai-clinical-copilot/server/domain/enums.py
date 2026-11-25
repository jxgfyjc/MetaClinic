"""Closed vocabularies shared by models, services and API schemas.

Statuses are stored as plain strings (with CHECK constraints in the baseline
migration) so they are readable in SQL and robust against enum-order drift.
"""
from __future__ import annotations

from enum import Enum


class S(str, Enum):
    """str-enum base: members compare equal to their value and JSON-serialise as str."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class Role(S):
    ADMIN = "ADMIN"
    DOCTOR = "DOCTOR"
    NURSE = "NURSE"
    PHARMACIST = "PHARMACIST"


class EncounterStatus(S):
    WAITING = "WAITING"                  # 待接诊
    IN_PROGRESS = "IN_PROGRESS"          # 接诊中
    PENDING_CONFIRM = "PENDING_CONFIRM"  # 待确认（治疗计划已确认，等待定稿病历）
    COMPLETED = "COMPLETED"              # 已完成（病历已定稿）
    CANCELLED = "CANCELLED"


class VisitType(S):
    INITIAL = "initial"
    REVISIT = "revisit"                  # 复诊（关联上次就诊）
    FOLLOWUP_VISIT = "followup_visit"    # 由随访预警触发


class EncounterSource(S):
    PATIENT_INTAKE = "patient_intake"
    STAFF = "staff"
    APPOINTMENT = "appointment"


class JobStatus(S):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisStatus(S):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModuleStatus(S):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"                  # 无输入（例如未上传舌象）
    UNAVAILABLE = "unavailable"          # 服务/知识库不可用


class ProcStatus(S):
    """Processing state of an uploaded artifact."""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"


class ArtifactKind(S):
    AUDIO = "audio"
    LAB_IMAGE = "lab_image"
    TONGUE_IMAGE = "tongue_image"
    IMAGE = "image"


class DxStatus(S):
    PENDING = "pending"
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"              # 医师自行添加后撤销
    SUPERSEDED = "superseded"            # 新一轮分析后不再出现且未审核


class DxModule(S):
    WESTERN = "western"
    TCM = "tcm"


class PlanStatus(S):
    GENERATING = "generating"
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class RxType(S):
    WESTERN = "western"
    PATENT = "patent"
    HERBAL = "herbal"


class RxStatus(S):
    DRAFT = "draft"
    SIGNED = "signed"                    # 医师已签署（等待药房）
    PHARMACY_REVIEW = "pharmacy_review"
    APPROVED = "approved"
    DISPENSED = "dispensed"
    RETURNED = "returned"                # 药房退回，医师需修改
    VOIDED = "voided"


class RecordStatus(S):
    DRAFT = "draft"
    FINALIZED = "finalized"


class ApptStatus(S):
    BOOKED = "booked"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class FuTaskStatus(S):
    SCHEDULED = "scheduled"
    ANSWERED = "answered"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class FuPlanStatus(S):
    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RiskLevel(S):
    NORMAL = "normal"
    ATTENTION = "attention"
    HIGH = "high_risk"


class Severity(S):
    INFO = "info"            # shown only
    WARNING = "warning"      # must be acknowledged before signing
    CRITICAL = "critical"    # must be acknowledged WITH a written reason before signing
    BLOCK = "block"          # hard validation error — signing impossible until fixed


class TokenKind(S):
    INTAKE_LINK = "intake_link"          # 医师二维码（可多人扫描）
    INTAKE_SESSION = "intake_session"    # 单个患者填写会话
    PORTAL = "portal"                    # 患者随访入口


class IntakeStatus(S):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# --------------------------------------------------------------------------- 医师经验画像（风格学习）
class StyleKind(S):
    MASTER = "master"                    # 名医/资深医师经验库（可经授权共享）
    PERSONAL = "personal"                # 医师个人诊疗习惯（永远私有）


class StyleProfileStatus(S):
    ACTIVE = "active"
    DISABLED = "disabled"                # 停用：不再被任何新接诊选用，已生成的方案保留当时的引用快照


class StyleVersionStatus(S):
    DRAFT = "draft"                      # 可编辑/审核
    ACTIVE = "active"                    # 已发布，不可变快照
    RETIRED = "retired"                  # 被新版本取代，保留供追溯


class StyleImportStatus(S):
    PARSING = "parsing"                  # 文件解析 / OCR 中
    NEEDS_REVIEW = "needs_review"        # 已拆分为病例、已脱敏，等待人工校验
    EXTRACTING = "extracting"            # 结构化抽取 + 规律归纳中
    EXTRACTED = "extracted"              # 候选经验已写入草稿版本，等待审核
    FAILED = "failed"
    CANCELLED = "cancelled"


class StyleCaseStatus(S):
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"              # 医师已核对脱敏后的文本，允许进入抽取
    EXCLUDED = "excluded"                # 排除（重复/异常/无关）


class StyleReview(S):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class StyleAuthStatus(S):
    PENDING = "pending"                  # 所有者自行声明，待管理员备案
    APPROVED = "approved"
    REVOKED = "revoked"


STYLE_CATEGORIES = ("reasoning", "principle", "formula", "herb_pair", "modification", "preference", "outcome", "followup", "note")
