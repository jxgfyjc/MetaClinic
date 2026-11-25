"""医师经验画像（风格学习）API：画像 / 导入批次 / 病例校验 / 经验条目审核 / 版本 / 共享授权 / 接诊选用。业务规则在 server/style/service.py。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.api import serializers as S0                      # noqa: F401  (kept for parity with sibling routers)
from server.db.base import get_db
from server.db.models import (Job, StyleAuthorization, StyleCase, StyleEntry, StyleEntryEvidence, StyleImport, StyleProfile, StyleSource, StyleVersion, User)
from server.deps import Ctx, actor_of, get_ctx, require
from server.domain.enums import StyleAuthStatus, StyleCaseStatus, StyleImportStatus, StyleReview, StyleVersionStatus
from server.errors import Conflict, Forbidden, NotFound, Unprocessable
from server.jobs import engine as jobs
from server.security import audit
from server.style import pipeline as PL
from server.style import serialize as Z
from server.style import service as svc

router = APIRouter(prefix="/style", tags=["style"])


def _profile_dict(db: Session, user: User, p: StyleProfile) -> dict:
    active = db.get(StyleVersion, p.active_version_id) if p.active_version_id else None
    draft = svc.draft_version(db, p) if svc.can_manage(user, p) else None
    return Z.profile_out(p, auth=svc.active_authorization(db, p) if p.kind == "master" else None, active=active, draft=draft,
                         counts=svc.case_counts(db, p.id) if svc.entries_visible(user, p) and svc.can_manage(user, p) else None,
                         can_manage=svc.can_manage(user, p), can_review=svc.can_review(user, p), usable=svc.usable_by(db, user, p))


class ProfileIn(BaseModel):
    kind: str
    subject_name: str = Field(min_length=1, max_length=64)
    subject_title: str = ""
    subject_dept: str = ""
    intro: str = Field(default="", max_length=2000)
    owner_user_id: str | None = None
    reviewer_id: str | None = None


class ProfilePatch(BaseModel):
    subject_title: str | None = None
    subject_dept: str | None = None
    intro: str | None = Field(default=None, max_length=2000)
    reviewer_id: str | None = None
    row_version: int | None = None


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=300)


def _local_available(s) -> bool:
    import ipaddress
    from urllib.parse import urlparse
    if s.demo_local_persona or bool(s.llm_api_key):
        return True
    host = (urlparse(s.qwen_base_url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


@router.get("/options")
def style_options(db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    """What the import wizard may offer. Deliberately minimal: whether the external-model option exists, never any endpoint/key detail."""
    from server.config import get_settings
    s = get_settings()
    return {"allow_external": svc.external_allowed(db), "external_configured": bool(s.style_ext_base_url and s.style_ext_model),
            "local_available": _local_available(s),
            "max_file_mb": s.max_style_file_mb, "accepted": ["txt", "csv", "json", "docx", "xlsx", "pdf（文字版）", "jpg/png/webp（OCR）"]}


@router.get("/profiles")
def list_profiles(kind: str | None = Query(default=None), db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    return {"profiles": [_profile_dict(db, user, p) for p in svc.list_visible(db, user, kind=kind)], "disclaimer": svc.disclaimer()}


@router.post("/profiles", status_code=201)
def create_profile(body: ProfileIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.create_profile(db, actor_of(user, ctx), user, kind=body.kind, subject_name=body.subject_name, subject_title=body.subject_title, subject_dept=body.subject_dept,
                           intro=body.intro, owner_user_id=body.owner_user_id, reviewer_id=body.reviewer_id)
    db.commit()
    return {"profile": _profile_dict(db, user, p)}


@router.get("/profiles/{pid}")
def get_profile(pid: str, db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    p = svc.require_visible(db, user, pid)
    auths = []
    if svc.can_manage(user, p) or svc.is_admin(user):
        auths = [Z.auth_out(a) for a in db.scalars(select(StyleAuthorization).where(StyleAuthorization.profile_id == p.id).order_by(StyleAuthorization.created_at.desc()))]
    return {"profile": _profile_dict(db, user, p), "authorizations": auths, "disclaimer": svc.disclaimer()}


@router.patch("/profiles/{pid}")
def patch_profile(pid: str, body: ProfilePatch, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_manage(db, user, pid)
    svc.update_profile(db, actor_of(user, ctx), p, subject_title=body.subject_title, subject_dept=body.subject_dept, intro=body.intro, reviewer_id=body.reviewer_id,
                       expected_version=body.row_version)
    db.commit()
    return {"profile": _profile_dict(db, user, p)}


@router.post("/profiles/{pid}/disable")
def disable_profile(pid: str, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_manage(db, user, pid)
    svc.disable_profile(db, actor_of(user, ctx), p, body.reason)
    db.commit()
    return {"profile": _profile_dict(db, user, p)}


@router.post("/profiles/{pid}/enable")
def enable_profile(pid: str, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_manage(db, user, pid)
    svc.enable_profile(db, actor_of(user, ctx), p)
    db.commit()
    return {"profile": _profile_dict(db, user, p)}

# ----------------------------------------------------------------------------- imports (批次)
MAX_PASTE_CHARS = 200_000
MAX_NOTE_CHARS = 20_000


def _job_dict(db: Session, job_id: str | None) -> dict | None:
    j = db.get(Job, job_id) if job_id else None
    if j is None:
        return None
    return {"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.progress_msg, "error_code": j.error_code, "error_detail": (j.error_detail or "")[:300]}


def _import_dict(db: Session, imp: StyleImport) -> dict:
    srcs = db.scalars(select(StyleSource).where(StyleSource.import_id == imp.id).order_by(StyleSource.created_at)).all()
    return Z.import_out(imp, srcs, _job_dict(db, imp.job_id))


def _load_import(db: Session, user: User, iid: str) -> tuple[StyleImport, StyleProfile]:
    imp = db.get(StyleImport, iid)
    if imp is None:
        raise NotFound("导入批次不存在", code="import_not_found")
    return imp, svc.require_manage(db, user, imp.profile_id)


def _busy(db: Session, imp: StyleImport) -> bool:
    j = db.get(Job, imp.job_id) if imp.job_id else None
    return j is not None and j.status in ("queued", "running")


def _enqueue(db: Session, imp: StyleImport, user: User, kind: str, payload: dict, *, timeout_s: int, attempts: int) -> Job:
    if _busy(db, imp):
        raise Conflict("该批次正在处理中，请等当前任务结束", code="import_busy")
    j = jobs.enqueue(kind, {"import_id": imp.id, **payload}, subject_type="style_import", subject_id=imp.id, created_by=user.id, timeout_s=timeout_s, max_attempts=attempts, session=db)
    imp.job_id = j.id
    return j

@router.post("/profiles/{pid}/imports", status_code=201)
async def create_import(pid: str, label: str = Form(default=""), text: str = Form(default=""), as_note: bool = Form(default=False), mapping: str = Form(default=""),
                        files: list[UploadFile] = File(default=[]), db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    """上传文件和/或粘贴文字。粘贴文字在本请求内就脱敏入库；文件交给 style_parse 任务（解析/OCR）。单个文件有问题不影响其他文件。"""
    import json as _json
    p = svc.require_manage(db, user, pid)
    if p.status != "active":
        raise Conflict("该画像已停用，不能再导入资料", code="profile_disabled")
    text = (text or "").strip()
    if not files and not text:
        raise Unprocessable("请上传文件或粘贴文字", code="nothing_to_import")
    if len(text) > (MAX_NOTE_CHARS if as_note else MAX_PASTE_CHARS):
        raise Unprocessable(f"粘贴文字过长（上限 {MAX_NOTE_CHARS if as_note else MAX_PASTE_CHARS} 字），请拆分或改用文件上传", code="text_too_long")
    cfg: dict = {}
    if mapping.strip():
        try:
            m = _json.loads(mapping)
            if not isinstance(m, dict):
                raise ValueError
            cfg["mapping"] = {str(k)[:40]: str(v)[:40] for k, v in m.items()}
        except ValueError:
            raise Unprocessable("列映射必须是 JSON 对象，如 {\"症状\":\"主诉\"}", code="bad_mapping") from None
    imp = StyleImport(profile_id=p.id, label=(label or "").strip()[:120], status=StyleImportStatus.PARSING.value, config_json=cfg, created_by=user.id)
    db.add(imp)
    db.flush()
    limit = PL.get_settings().max_style_file_mb * 1024 * 1024 + 1
    rejected, accepted = [], 0
    for f in files:
        data = await f.read(limit)
        try:
            PL.store_source(db, imp, data, f.filename or "")
            accepted += 1
        except Unprocessable as e:
            rejected.append({"name": f.filename or "", "code": e.code, "message": e.message})
    made = 0
    if text:
        src = StyleSource(import_id=imp.id, kind="note" if as_note else "paste", original_name="医师经验笔记" if as_note else "粘贴文字", size_bytes=len(text.encode()),
                          parse_status="parsed")
        db.add(src)
        db.flush()
        made = PL.ingest(db, imp, src, None, text_blocks=[text], origin="paste", kind_override="note" if as_note else None, label=src.original_name)
    if accepted == 0 and made == 0:
        db.rollback()
        raise Unprocessable("没有可导入的内容：" + "；".join(f"{r['name']}：{r['message']}" for r in rejected)[:400], code="nothing_importable", details={"rejected": rejected})
    if accepted:
        _enqueue(db, imp, user, "style_parse", {}, timeout_s=3600, attempts=2)
    else:
        PL.finish_parsing(db, imp)
    audit.record(db, actor_of(user, ctx), "style.import.create", entity_type="style_import", entity_id=imp.id, summary=f"创建导入批次：{accepted} 个文件 + {'1 段文字' if text else '无文字'}",
                 detail={"profile": p.id, "files": accepted, "rejected": len(rejected), "paste_cases": made})
    db.commit()
    return {"import": _import_dict(db, imp), "rejected": rejected}


@router.get("/profiles/{pid}/imports")
def list_imports(pid: str, db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    p = svc.require_manage(db, user, pid)
    rows = db.scalars(select(StyleImport).where(StyleImport.profile_id == p.id).order_by(StyleImport.created_at.desc()).limit(50)).all()
    return {"imports": [Z.import_out(i, None, _job_dict(db, i.job_id)) for i in rows]}


@router.get("/imports/{iid}")
def get_import(iid: str, db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    imp, p = _load_import(db, user, iid)
    if not svc.entries_visible(user, p):
        raise Forbidden("无权查看该画像的资料", code="style_forbidden")
    return {"import": _import_dict(db, imp)}


@router.post("/imports/{iid}/cancel")
def cancel_import(iid: str, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    imp, p = _load_import(db, user, iid)
    if imp.status in (StyleImportStatus.EXTRACTED.value, StyleImportStatus.CANCELLED.value):
        raise Conflict("该批次已完成或已取消", code="import_final")
    if imp.job_id:
        jobs.cancel(imp.job_id)
    imp.status = StyleImportStatus.CANCELLED.value
    PL.purge_sources(db, imp)
    audit.record(db, actor_of(user, ctx), "style.import.cancel", entity_type="style_import", entity_id=imp.id, summary="取消导入批次并清除源文件", detail={"profile": p.id})
    db.commit()
    return {"import": _import_dict(db, imp)}

# ----------------------------------------------------------------------------- cases (人工校验)
class CasePatch(BaseModel):
    text: str | None = Field(default=None, max_length=30000)
    status: str | None = None                   # confirmed | excluded | pending_review
    row_version: int | None = None


class BulkIn(BaseModel):
    action: str                                 # confirm | exclude | restore
    case_ids: list[str] | None = None           # None = 按 only_clean 过滤整个批次
    only_clean: bool = True                     # confirm 且未指定 ids 时：只确认“没有警告、脱敏风险低”的病例


def _case_of(db: Session, user: User, cid: str) -> tuple[StyleCase, StyleProfile]:
    c = db.get(StyleCase, cid)
    if c is None:
        raise NotFound("病例不存在", code="case_not_found")
    p = svc.require_manage(db, user, c.profile_id)
    if not svc.entries_visible(user, p):
        raise Forbidden("无权查看该画像的资料", code="style_forbidden")
    return c, p


def _is_clean(c: StyleCase) -> bool:
    return (c.redaction_json or {}).get("risk", "low") == "low" and not any(a.get("severity") in ("warn", "error") for a in (c.anomaly_json or []))


@router.get("/imports/{iid}/cases")
def list_cases(iid: str, status: str | None = Query(default=None), flagged: bool = Query(default=False), page: int = Query(default=1, ge=1), size: int = Query(default=30, ge=1, le=100),
               db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    imp, p = _load_import(db, user, iid)
    if not svc.entries_visible(user, p):
        raise Forbidden("无权查看该画像的资料", code="style_forbidden")
    q = select(StyleCase).where(StyleCase.import_id == imp.id)
    if status:
        q = q.where(StyleCase.status == status)
    rows = db.scalars(q.order_by(StyleCase.seq)).all()
    if flagged:
        rows = [c for c in rows if not _is_clean(c) or c.dup_of_id]
    total = len(rows)
    page_rows = rows[(page - 1) * size: page * size]
    return {"cases": [Z.case_out(c) for c in page_rows], "total": total, "page": page, "size": size}


@router.patch("/cases/{cid}")
def patch_case(cid: str, body: CasePatch, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    from server.deps import check_version
    from server.domain.ids import utcnow
    from server.style import cases as C
    from server.style.redact import redact
    c, p = _case_of(db, user, cid)
    imp = db.get(StyleImport, c.import_id)
    if imp.status in (StyleImportStatus.EXTRACTING.value, StyleImportStatus.EXTRACTED.value):
        raise Conflict("该批次已进入抽取阶段，不能再修改病例", code="import_locked")
    check_version(c.row_version, body.row_version, "病例", db=db, obj=c)
    if body.text is not None:
        new = body.text.strip()
        if len(new) < 10:
            raise Unprocessable("病例文本过短", code="text_too_short")
        r = redact(new)                                          # 医师手改的文本同样过脱敏，防止把原名字改回去
        c.text, c.content_hash = r.text, C.content_hash(r.text)
        c.redaction_json = {"counts": r.counts, "risk": r.risk, "residual": r.residual}
        c.herbs_json = C.parse_herbs(r.text) if c.kind == "case" else []
        c.struct_status, c.structured_json = "pending", {}
        if c.status == StyleCaseStatus.CONFIRMED.value:
            c.status, c.reviewed_by, c.reviewed_at = StyleCaseStatus.PENDING_REVIEW.value, None, None     # 改文本后须重新确认
    if body.status is not None:
        if body.status not in ("confirmed", "excluded", "pending_review"):
            raise Unprocessable("状态不正确", code="bad_status")
        if body.status == "confirmed":
            if (c.redaction_json or {}).get("risk") == "high":
                raise Conflict("该病例脱敏后仍有高风险个人信息，请先修改文本遮盖后再确认", code="redaction_high")
            c.status, c.reviewed_by, c.reviewed_at = StyleCaseStatus.CONFIRMED.value, user.id, utcnow()
        else:
            c.status, c.reviewed_by, c.reviewed_at = body.status, (user.id if body.status == "excluded" else None), (utcnow() if body.status == "excluded" else None)
    c.row_version += 1
    PL.refresh_stats(db, imp)
    audit.record(db, actor_of(user, ctx), "style.case.update", entity_type="style_case", entity_id=c.id, summary=f"校验病例 #{c.seq}：{c.status}",
                 detail={"profile": p.id, "text_edited": body.text is not None})
    db.commit()
    return {"case": Z.case_out(c), "stats": imp.stats_json}


@router.post("/imports/{iid}/cases/bulk")
def bulk_cases(iid: str, body: BulkIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    from server.domain.ids import utcnow
    imp, p = _load_import(db, user, iid)
    if imp.status not in (StyleImportStatus.NEEDS_REVIEW.value, StyleImportStatus.EXTRACTED.value, StyleImportStatus.FAILED.value) or _busy(db, imp):
        raise Conflict("当前状态不能批量处理病例", code="import_state")
    if body.action not in ("confirm", "exclude", "restore"):
        raise Unprocessable("未知操作", code="bad_action")
    rows = db.scalars(select(StyleCase).where(StyleCase.import_id == imp.id)).all()
    if body.case_ids is not None:
        want = set(body.case_ids)
        rows = [c for c in rows if c.id in want]
    skipped_why: dict[str, int] = {}
    if body.case_ids is None and body.action == "confirm":
        keep = []
        for c in rows:
            if c.status != StyleCaseStatus.PENDING_REVIEW.value:
                continue
            if c.dup_of_id:
                skipped_why["疑似重复录入（请核对后手动确认或排除）"] = skipped_why.get("疑似重复录入（请核对后手动确认或排除）", 0) + 1
            elif body.only_clean and not _is_clean(c):
                skipped_why["有警告或脱敏风险（请逐份核对）"] = skipped_why.get("有警告或脱敏风险（请逐份核对）", 0) + 1
            else:
                keep.append(c)
        rows = keep
    n = skipped = 0
    for c in rows:
        if body.action == "confirm":
            if (c.redaction_json or {}).get("risk") == "high" or c.status == StyleCaseStatus.EXCLUDED.value and c.dup_score >= 0.999:
                skipped += 1
                continue
            c.status, c.reviewed_by, c.reviewed_at = StyleCaseStatus.CONFIRMED.value, user.id, utcnow()
        elif body.action == "exclude":
            c.status, c.reviewed_by, c.reviewed_at = StyleCaseStatus.EXCLUDED.value, user.id, utcnow()
        else:
            c.status, c.reviewed_by, c.reviewed_at = StyleCaseStatus.PENDING_REVIEW.value, None, None
        c.row_version += 1
        n += 1
    st = PL.refresh_stats(db, imp)
    audit.record(db, actor_of(user, ctx), "style.case.bulk", entity_type="style_import", entity_id=imp.id, summary=f"批量{body.action}：{n} 份病例（跳过 {skipped}）",
                 detail={"profile": p.id, "action": body.action, "count": n, "skipped": skipped})
    db.commit()
    return {"changed": n, "skipped": skipped + sum(skipped_why.values()), "skipped_reasons": skipped_why, "stats": st}


# ----------------------------------------------------------------------------- extraction
class ExtractIn(BaseModel):
    mode: str = "local"                         # local | rules | external
    external_ack: bool = False                  # external：确认“已核对脱敏结果并同意外发”


@router.post("/imports/{iid}/extract", status_code=202)
def start_extract(iid: str, body: ExtractIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    from server.domain.ids import utcnow
    imp, p = _load_import(db, user, iid)
    if imp.status not in (StyleImportStatus.NEEDS_REVIEW.value, StyleImportStatus.FAILED.value) or (imp.status == StyleImportStatus.FAILED.value and not imp.stats_json.get("cases")):
        raise Conflict("当前状态不能开始抽取（需要先完成解析并校验病例）", code="import_state")
    if body.mode not in ("local", "rules", "external"):
        raise Unprocessable("抽取方式不正确", code="bad_mode")
    if body.mode == "local":
        from server.config import get_settings
        if not _local_available(get_settings()):
            raise Conflict("当前部署未连接本机模型，请选择其他抽取方式", code="local_unavailable")
    st = PL.refresh_stats(db, imp)
    if st["confirmed"] == 0:
        raise Conflict("还没有确认任何病例：请先逐份核对脱敏后的文本并确认", code="no_confirmed_cases")
    if body.mode == "external":
        if not svc.external_allowed(db):
            raise Forbidden("管理员尚未开启“允许外部模型抽取”", code="external_disabled")
        if not body.external_ack:
            raise Unprocessable("请勾选确认：已核对脱敏结果，并同意将脱敏后的文本发送给外部模型", code="ack_required")
        imp.ext_ack_by, imp.ext_ack_at = user.id, utcnow()
    j = _enqueue(db, imp, user, "style_extract", {"mode": body.mode}, timeout_s=14400, attempts=1)
    imp.status = StyleImportStatus.EXTRACTING.value
    audit.record(db, actor_of(user, ctx), "style.import.extract_start", entity_type="style_import", entity_id=imp.id, summary=f"开始抽取（{body.mode}）：{st['confirmed']} 份已确认病例",
                 detail={"profile": p.id, "mode": body.mode, "confirmed": st["confirmed"], "external_ack": body.mode == "external"})
    db.commit()
    return {"import": _import_dict(db, imp), "job_id": j.id}

# ----------------------------------------------------------------------------- entries (条目审核) & versions
class ReviewIn(BaseModel):
    action: str                                 # approve | approve_no_ai | reject | revert | edit
    note: str = Field(default="", max_length=300)
    ack_reason: str = Field(default="", max_length=300)
    edits: dict | None = None
    row_version: int | None = None


class ManualEntryIn(BaseModel):
    category: str
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=6, max_length=1500)
    group_key: str = Field(default="", max_length=80)
    conditions: str = Field(default="", max_length=1500)
    exceptions: str = Field(default="", max_length=1500)


class PublishIn(BaseModel):
    note: str = Field(default="", max_length=400)


def _version_for_read(db: Session, user: User, p: StyleProfile, which: str) -> StyleVersion:
    """`active` is readable by anyone who can see the profile (approved entries only); `draft`/<id> only by managers."""
    if which == "active":
        v = db.get(StyleVersion, p.active_version_id) if p.active_version_id else None
        if v is None:
            raise NotFound("该画像还没有发布的版本", code="no_active_version")
        return v
    if not svc.can_manage(user, p):
        raise Forbidden("只有画像的管理者可以查看草稿或历史版本", code="style_forbidden")
    v = svc.draft_version(db, p) if which == "draft" else db.get(StyleVersion, which)
    if v is None or v.profile_id != p.id:
        raise NotFound("版本不存在", code="version_not_found")
    return v


@router.get("/profiles/{pid}/versions")
def list_versions(pid: str, db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    p = svc.require_manage(db, user, pid)
    if not svc.entries_visible(user, p):
        raise Forbidden("无权查看该画像的资料", code="style_forbidden")
    rows = db.scalars(select(StyleVersion).where(StyleVersion.profile_id == p.id).order_by(StyleVersion.version_no.desc())).all()
    return {"versions": [Z.version_out(v) for v in rows], "active_version_id": p.active_version_id}


@router.get("/profiles/{pid}/entries")
def list_entries(pid: str, version: str = Query(default="active"), category: str | None = Query(default=None), review: str | None = Query(default=None),
                 group: str | None = Query(default=None), db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    p = svc.require_visible(db, user, pid)
    if not svc.entries_visible(user, p):
        raise Forbidden("无权查看该画像的内容", code="style_forbidden")
    if version == "draft" and svc.can_manage(user, p) and svc.draft_version(db, p) is None:
        # "no draft right now" is a NORMAL state (e.g. right after publishing) — answer with an empty draft, never a 404 the UI has to special-case
        return {"version": None, "entries": [], "review_counts": {}, "disclaimer": svc.disclaimer(), "authorization": None}
    v = _version_for_read(db, user, p, version)
    q = select(StyleEntry).where(StyleEntry.version_id == v.id)
    if v.status != StyleVersionStatus.DRAFT.value or not svc.can_manage(user, p):
        q = q.where(StyleEntry.review_status == StyleReview.APPROVED.value)      # 使用者只会看到已审核条目
    if category:
        q = q.where(StyleEntry.category == category)
    if review and svc.can_manage(user, p):
        q = q.where(StyleEntry.review_status == review)
    if group:
        q = q.where(StyleEntry.group_key == group)
    rows = db.scalars(q.order_by(StyleEntry.category, StyleEntry.group_key, StyleEntry.sort_order)).all()
    counts = dict(db.execute(select(StyleEntry.review_status, func.count()).where(StyleEntry.version_id == v.id).group_by(StyleEntry.review_status)).all()) if svc.can_manage(user, p) else {}
    return {"version": Z.version_out(v), "entries": [Z.entry_out(e) for e in rows], "review_counts": counts, "disclaimer": svc.disclaimer(),
            "authorization": Z.auth_out(a) if (a := svc.active_authorization(db, p)) and p.kind == "master" else None}


@router.get("/entries/{eid}")
def get_entry(eid: str, db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    e = db.get(StyleEntry, eid)
    if e is None:
        raise NotFound("经验条目不存在", code="entry_not_found")
    p = svc.require_visible(db, user, e.profile_id)
    if not svc.entries_visible(user, p):
        raise Forbidden("无权查看该画像的内容", code="style_forbidden")
    v = db.get(StyleVersion, e.version_id)
    manage = svc.can_manage(user, p)
    if not manage and not (v.id == p.active_version_id and e.review_status == StyleReview.APPROVED.value):
        raise NotFound("经验条目不存在", code="entry_not_found")
    ev = db.scalars(select(StyleEntryEvidence).where(StyleEntryEvidence.entry_id == e.id).order_by(StyleEntryEvidence.kind, StyleEntryEvidence.id)).all()
    out = Z.entry_out(e, ev)
    if manage:                                                 # 管理者可以点进原病例（已脱敏）；共享使用者只看逐字片段
        cases = {c.id: c for c in db.scalars(select(StyleCase).where(StyleCase.id.in_([x.case_id for x in ev])))} if ev else {}
        for item, x in zip(out["evidence"], ev):
            c = cases.get(x.case_id)
            item["case"] = {"seq": c.seq, "source_label": c.source_label, "visit_date": c.visit_date, "text": c.text} if c else None
    return {"entry": out}


class BulkReviewItem(BaseModel):
    id: str = Field(min_length=1, max_length=60)
    row_version: int | None = None


class BulkReviewIn(BaseModel):
    action: str = "approve"
    items: list[BulkReviewItem] = Field(min_length=1, max_length=200)
    request_id: str = Field(default="", max_length=80)


@router.post("/profiles/{pid}/entries/bulk-review")
def bulk_review_entries(pid: str, body: BulkReviewIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_review(db, user, pid)
    res = svc.bulk_review_entries(db, actor_of(user, ctx), p, [it.model_dump() for it in body.items], action=body.action, request_id=body.request_id)
    db.commit()
    return res


@router.post("/entries/{eid}/review")
def review_entry(eid: str, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    e = db.get(StyleEntry, eid)
    if e is None:
        raise NotFound("经验条目不存在", code="entry_not_found")
    p = svc.require_review(db, user, e.profile_id)             # 只有所有者医师/指定审核医师；管理员不能审核临床内容
    actor = actor_of(user, ctx)
    if body.action == "approve_no_ai":
        svc.approve_without_ai(db, actor, p, e, body.note)
    else:
        svc.review_entry(db, actor, p, e, action=body.action, note=body.note, ack_reason=body.ack_reason, edits=body.edits, expected_version=body.row_version)
    db.commit()
    return {"entry": Z.entry_out(e)}


@router.post("/profiles/{pid}/entries", status_code=201)
def add_entry(pid: str, body: ManualEntryIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_review(db, user, pid)
    v = svc.new_draft_version(db, actor_of(user, ctx), p, note="手工补充经验")
    e = svc.add_manual_entry(db, actor_of(user, ctx), p, v, category=body.category, title=body.title, summary=body.summary, group_key=body.group_key,
                             conditions=body.conditions, exceptions=body.exceptions)
    db.commit()
    return {"entry": Z.entry_out(e)}


@router.post("/profiles/{pid}/publish")
def publish(pid: str, body: PublishIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_review(db, user, pid)
    v = svc.draft_version(db, p)
    if v is None:
        raise NotFound("没有待发布的草稿版本", code="no_draft")
    svc.publish_version(db, actor_of(user, ctx), p, v, note=body.note)
    db.commit()
    return {"profile": _profile_dict(db, user, p)}


@router.post("/profiles/{pid}/versions/{vid}/rollback")
def rollback(pid: str, vid: str, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_review(db, user, pid)
    t = db.get(StyleVersion, vid)
    if t is None or t.profile_id != p.id:
        raise NotFound("版本不存在", code="version_not_found")
    v = svc.rollback_to(db, actor_of(user, ctx), p, t, body.reason)
    db.commit()
    return {"draft_version": Z.version_out(v)}


@router.delete("/profiles/{pid}/draft")
def discard_draft(pid: str, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    p = svc.require_review(db, user, pid)
    v = svc.draft_version(db, p)
    if v is None:
        return {"discarded": False}
    n = db.scalar(select(func.count()).select_from(StyleEntry).where(StyleEntry.version_id == v.id)) or 0
    db.delete(v)
    audit.record(db, actor_of(user, ctx), "style.version.discard", entity_type="style_version", entity_id=v.id, summary=f"丢弃草稿版本 v{v.version_no}（{n} 条）", detail={"profile": p.id})
    db.commit()
    return {"discarded": True, "entries": n}


# ----------------------------------------------------------------------------- authorization (共享授权)
class AuthIn(BaseModel):
    basis_type: str
    basis_text: str = Field(min_length=1, max_length=400)
    authorizer_name: str = Field(min_length=1, max_length=64)
    scope: str = "hospital"
    scope_dept: str = ""
    valid_until: str | None = None              # YYYY-MM-DD


@router.post("/profiles/{pid}/authorizations", status_code=201)
def declare_auth(pid: str, body: AuthIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    from datetime import datetime
    p = svc.require_manage(db, user, pid)
    vu = None
    if body.valid_until:
        try:
            vu = datetime.strptime(body.valid_until, "%Y-%m-%d")
        except ValueError:
            raise Unprocessable("有效期格式应为 YYYY-MM-DD", code="bad_date") from None
    a = svc.declare_authorization(db, actor_of(user, ctx), user, p, basis_type=body.basis_type, basis_text=body.basis_text, authorizer_name=body.authorizer_name,
                                  scope=body.scope, scope_dept=body.scope_dept, valid_until=vu)
    db.commit()
    return {"authorization": Z.auth_out(a)}


@router.post("/authorizations/{aid}/approve")
def approve_auth(aid: str, db: Session = Depends(get_db), user: User = Depends(require("style.admin")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(StyleAuthorization, aid)
    if a is None:
        raise NotFound("授权记录不存在", code="auth_not_found")
    svc.approve_authorization(db, actor_of(user, ctx), user, a)
    db.commit()
    return {"authorization": Z.auth_out(a)}


@router.post("/authorizations/{aid}/revoke")
def revoke_auth(aid: str, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("style.write")), ctx: Ctx = Depends(get_ctx)):
    a = db.get(StyleAuthorization, aid)
    if a is None:
        raise NotFound("授权记录不存在", code="auth_not_found")
    svc.revoke_authorization(db, actor_of(user, ctx), user, a, body.reason)
    db.commit()
    return {"authorization": Z.auth_out(a)}


@router.get("/admin/pending-authorizations")
def pending_auths(db: Session = Depends(get_db), user: User = Depends(require("style.admin"))):
    rows = db.scalars(select(StyleAuthorization).where(StyleAuthorization.status == StyleAuthStatus.PENDING.value).order_by(StyleAuthorization.created_at)).all()
    out = []
    for a in rows:
        p = db.get(StyleProfile, a.profile_id)
        out.append({**Z.auth_out(a), "profile": {"id": p.id, "subject_name": p.subject_name, "subject_title": p.subject_title, "proxy_created": p.proxy_created}})
    return {"authorizations": out}


class ExtSwitchIn(BaseModel):
    allow: bool


@router.get("/admin/external")
def get_external(db: Session = Depends(get_db), user: User = Depends(require("style.admin"))):
    from server.config import get_settings
    s = get_settings()
    return {"allow_external": svc.external_allowed(db), "configured": bool(getattr(s, "style_ext_base_url", "") and getattr(s, "style_ext_model", ""))}


@router.put("/admin/external")
def set_external(body: ExtSwitchIn, db: Session = Depends(get_db), user: User = Depends(require("style.admin")), ctx: Ctx = Depends(get_ctx)):
    svc.set_external_allowed(db, actor_of(user, ctx), body.allow)
    db.commit()
    return {"allow_external": svc.external_allowed(db)}


# ----------------------------------------------------------------------------- encounter choice (按接诊选用)
class ChooseIn(BaseModel):
    profile_ids: list[str] = Field(default_factory=list, max_length=5)


@router.get("/encounters/{eid}/profiles")
def get_encounter_choice(eid: str, db: Session = Depends(get_db), user: User = Depends(require("style.read"))):
    from server.deps import load_encounter_for
    if user.role != "DOCTOR":
        raise Forbidden("只有接诊医师可以为接诊选用经验画像", code="style_forbidden")
    e = load_encounter_for(db, user, eid)
    chosen = svc.chosen_for_encounter(db, e.id)
    usable = [p for p in svc.list_visible(db, user) if svc.usable_by(db, user, p)]
    return {"chosen": chosen, "available": [_profile_dict(db, user, p) for p in usable], "disclaimer": svc.disclaimer()}


@router.put("/encounters/{eid}/profiles")
def set_encounter_choice(eid: str, body: ChooseIn, db: Session = Depends(get_db), user: User = Depends(require("style.read")), ctx: Ctx = Depends(get_ctx)):
    if user.role != "DOCTOR":
        raise Forbidden("只有接诊医师可以为接诊选用经验画像", code="style_forbidden")
    from server.deps import load_encounter_for
    e = load_encounter_for(db, user, eid, write=True)
    if e.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("就诊已结束，不能更改参考画像", code="encounter_closed")
    ids = svc.choose_for_encounter(db, actor_of(user, ctx), user, e.id, body.profile_ids)
    db.commit()
    return {"chosen": ids}


# ----------------------------------------------------------------------------- doctor's decision on the comparison (第④栏：医师最终决策)
class DecisionIn(BaseModel):
    index: int = Field(ge=0, le=200)              # style.comparison 的下标
    decision: str                                  # adopt | skip | clear
    note: str = Field(default="", max_length=200)
    row_version: int | None = None


@router.post("/plans/{plan_id}/decision")
def plan_decision(plan_id: str, body: DecisionIn, db: Session = Depends(get_db), user: User = Depends(require("plan.write")), ctx: Ctx = Depends(get_ctx)):
    """The attending physician records, per comparison row, whether THEY adopt the historical practice. The system never decides for them;
    this is documentation (and audited) — it does not change the plan content, the safety checks, or plan confirmation."""
    from server.db.models import Encounter, TreatmentPlan
    from server.deps import check_version, load_encounter_for
    from server.domain.enums import PlanStatus
    from server.style import reference as ref
    pl = db.get(TreatmentPlan, plan_id)
    if pl is None:
        raise NotFound("方案不存在", code="plan_not_found")
    e = load_encounter_for(db, user, pl.encounter_id, write=True)
    if pl.status != PlanStatus.DRAFT.value:
        raise Conflict("只有草稿状态的方案可以记录对经验参考的取舍（已确认的方案请先撤回确认/重新编辑）", code="plan_not_editable")
    st = pl.style_json or {}
    if not st.get("comparison") or body.index >= len(st["comparison"]):
        raise Unprocessable("没有这一条对照记录", code="no_such_row")
    if body.decision not in ("adopt", "skip", "clear"):
        raise Unprocessable("决策必须是 adopt / skip / clear", code="bad_decision")
    check_version(pl.row_version, body.row_version, "治疗方案", db=db, obj=pl)
    pl.style_json = ref.apply_decision(st, body.index, body.decision, body.note)
    pl.row_version += 1
    row = st["comparison"][body.index]
    audit.record(db, actor_of(user, ctx), "style.plan.decision", entity_type="plan", entity_id=pl.id, encounter_id=e.id, patient_id=e.patient_id,
                 summary=f"对经验参考第 {body.index + 1} 条的取舍：{body.decision}", detail={"refs": row.get("refs"), "item": row.get("item"), "decision": body.decision})
    db.commit()
    return {"style": pl.style_json, "row_version": pl.row_version}
