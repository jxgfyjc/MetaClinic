"""医师经验画像：服务层（权限判断、状态机、版本、授权、审计）。API 层只做参数校验，规则全部在这里。

权限与职责分离
  * style.read   所有医师/管理员：浏览、选用「可见」画像（自己的 + 已备案共享的 master）。personal 画像只有所有者能看。
  * style.write  医师：给**自己的**画像导入/校验/审核/发布；ADMIN 也有此权限但**不能审核临床条目、不能发布**（职责分离）。
  * style.admin  管理员：代建画像、备案/撤回共享授权、外发开关。管理员代建的画像由 `reviewer_id`（一位医师）负责临床审核与发布。
  * 医师不能给别人的画像做临床审核；没有所有者的代建画像，只有被指定的 reviewer 可审核。

不可变性
  * ACTIVE 版本不能被改；要改就新建 DRAFT 版本（复制已审核条目 + 新增候选条目），审核后再发布，旧版本转 RETIRED。
  * 新导入的资料只会生成**候选条目**进入 DRAFT 版本；永远不会自动改动 ACTIVE 版本。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from server.db.models import (EncounterStyleChoice, StyleAuthorization, StyleCase, StyleEntry, StyleEntryEvidence, StyleImport, StyleProfile, StyleVersion, User)
from server.domain.enums import (Role, StyleAuthStatus, StyleCaseStatus, StyleKind, StyleProfileStatus, StyleReview, StyleVersionStatus)
from server.domain.ids import hash_obj, utcnow
from server.errors import Conflict, Forbidden, NotFound, Unprocessable
from server.security import audit
from server.security.audit import Actor
from server.style import DISCLAIMER

EXT_SETTING = "style.allow_external"


# --------------------------------------------------------------------------- access
def is_admin(u: User) -> bool:
    return u.role == Role.ADMIN.value


def can_review(u: User, p: StyleProfile) -> bool:
    """Clinical review/publish: the owner physician, or (for proxy-created profiles without an owner account) the designated reviewer physician."""
    if u.role != Role.DOCTOR.value:
        return False
    return u.id == p.owner_user_id or (p.owner_user_id is None and u.id == p.reviewer_id) or (p.reviewer_id == u.id)


def can_manage(u: User, p: StyleProfile) -> bool:
    """Import material / edit drafts: owner, designated reviewer, or ADMIN (proxy creation)."""
    return can_review(u, p) or (is_admin(u) and p.proxy_created) or (is_admin(u) and p.kind == StyleKind.MASTER.value)


def active_authorization(db: Session, p: StyleProfile) -> StyleAuthorization | None:
    a = db.scalar(select(StyleAuthorization).where(StyleAuthorization.profile_id == p.id, StyleAuthorization.status == StyleAuthStatus.APPROVED.value)
                  .order_by(StyleAuthorization.created_at.desc()).limit(1))
    if a and a.valid_until and a.valid_until < utcnow():
        return None
    return a


def visible_to(db: Session, u: User, p: StyleProfile) -> bool:
    if p.owner_user_id == u.id or p.reviewer_id == u.id or p.created_by == u.id:
        return True
    if is_admin(u):
        return True                                  # 管理员可见全部画像的元数据（用于备案管理）；不含 personal 的条目内容，见 entries_visible
    if p.kind == StyleKind.MASTER.value and p.status == StyleProfileStatus.ACTIVE.value and p.active_version_id:
        a = active_authorization(db, p)
        if a and (a.scope == "hospital" or (a.scope == "dept" and a.scope_dept and a.scope_dept == u.dept)):
            return True
    return False


def usable_by(db: Session, u: User, p: StyleProfile) -> bool:
    """May `u` pick this profile as a reference for an encounter? (active + a published version + (own OR approved shared))."""
    if p.status != StyleProfileStatus.ACTIVE.value or not p.active_version_id:
        return False
    if p.owner_user_id == u.id:
        return True
    if p.kind == StyleKind.PERSONAL.value:
        return False
    a = active_authorization(db, p)
    return bool(a and (a.scope == "hospital" or (a.scope == "dept" and a.scope_dept == u.dept)))


def entries_visible(u: User, p: StyleProfile) -> bool:
    """May `u` read this profile's entries / cases? ADMIN sees personal profiles' metadata only, never their content."""
    if p.kind == StyleKind.PERSONAL.value and is_admin(u) and p.owner_user_id != u.id:
        return False
    return True


def get_profile(db: Session, pid: str) -> StyleProfile:
    p = db.get(StyleProfile, pid)
    if p is None:
        raise NotFound("经验画像不存在", code="style_not_found")
    return p


def require_visible(db: Session, u: User, pid: str) -> StyleProfile:
    p = get_profile(db, pid)
    if not visible_to(db, u, p):
        raise NotFound("经验画像不存在", code="style_not_found")      # 不泄露存在性
    return p


def require_manage(db: Session, u: User, pid: str) -> StyleProfile:
    p = require_visible(db, u, pid)
    if not can_manage(u, p):
        raise Forbidden("您无权管理该画像", code="style_forbidden")
    return p


def require_review(db: Session, u: User, pid: str) -> StyleProfile:
    p = require_visible(db, u, pid)
    if not can_review(u, p):
        raise Forbidden("只有该画像的所有者医师或指定的临床审核医师可以审核/发布条目（管理员不能审核临床内容）", code="style_review_forbidden")
    return p


# --------------------------------------------------------------------------- settings
def external_allowed(db: Session) -> bool:
    from server.db.models import AppSetting
    row = db.get(AppSetting, EXT_SETTING)
    return bool(row and row.value_json is True)


def set_external_allowed(db: Session, actor: Actor, value: bool) -> None:
    from server.db.models import AppSetting
    row = db.get(AppSetting, EXT_SETTING)
    before = bool(row and row.value_json is True)
    if row is None:
        db.add(AppSetting(key=EXT_SETTING, value_json=bool(value), updated_by=actor.id))
    else:
        row.value_json, row.updated_by = bool(value), actor.id
    audit.record(db, actor, "style.external_switch", entity_type="setting", entity_id=EXT_SETTING, summary=f"外部模型抽取开关：{before} → {bool(value)}",
                 detail={"from": before, "to": bool(value)})


# --------------------------------------------------------------------------- profiles
def create_profile(db: Session, actor: Actor, user: User, *, kind: str, subject_name: str, subject_title: str = "", subject_dept: str = "", intro: str = "",
                   owner_user_id: str | None = None, reviewer_id: str | None = None, proxy: bool = False) -> StyleProfile:
    subject_name = subject_name.strip()
    if not subject_name:
        raise Unprocessable("请填写经验所属医师的姓名", code="name_required")
    if kind not in StyleKind.values():
        raise Unprocessable("画像类型必须是 master 或 personal", code="bad_kind")
    if kind == StyleKind.PERSONAL.value:
        if is_admin(user):
            raise Forbidden("个人画像只能由医师本人创建", code="style_forbidden")
        owner_user_id, reviewer_id, proxy = user.id, None, False
        existing = db.scalar(select(StyleProfile).where(StyleProfile.kind == "personal", StyleProfile.owner_user_id == user.id, StyleProfile.status == "active"))
        if existing:
            raise Conflict("您已有一份个人画像，请直接在其中导入资料", code="personal_exists", details={"id": existing.id})
    else:
        if is_admin(user):
            proxy = True                                           # 管理员建 master = 代建
            if owner_user_id:
                o = db.get(User, owner_user_id)
                if not o or o.role != Role.DOCTOR.value:
                    raise Unprocessable("所有者账号必须是一位医师", code="bad_owner")
            if not owner_user_id and not reviewer_id:
                raise Unprocessable("代建的名医画像必须指定一位负责临床审核的医师（所有者账号或审核医师）", code="reviewer_required")
        else:
            owner_user_id, proxy = user.id, False
        if reviewer_id:
            r = db.get(User, reviewer_id)
            if not r or r.role != Role.DOCTOR.value or not r.active:
                raise Unprocessable("审核医师必须是在职的医师账号", code="bad_reviewer")
    p = StyleProfile(kind=kind, subject_name=subject_name[:64], subject_title=subject_title[:64], subject_dept=subject_dept[:64], intro=intro[:2000],
                     owner_user_id=owner_user_id, reviewer_id=reviewer_id, proxy_created=proxy, created_by=user.id)
    db.add(p)
    db.flush()
    new_draft_version(db, actor, p, note="初始草稿")
    audit.record(db, actor, "style.profile.create", entity_type="style_profile", entity_id=p.id,
                 summary=f"创建{'名医经验库' if kind == 'master' else '个人画像'}：{p.subject_name}" + ("（管理员代建）" if proxy else ""),
                 detail={"kind": kind, "proxy": proxy, "owner": owner_user_id, "reviewer": reviewer_id})
    return p


def update_profile(db: Session, actor: Actor, p: StyleProfile, *, subject_title=None, subject_dept=None, intro=None, reviewer_id=None, expected_version: int | None = None) -> StyleProfile:
    from server.deps import check_version
    check_version(p.row_version, expected_version, "经验画像", db=db, obj=p)
    if subject_title is not None:
        p.subject_title = subject_title[:64]
    if subject_dept is not None:
        p.subject_dept = subject_dept[:64]
    if intro is not None:
        p.intro = intro[:2000]
    if reviewer_id is not None and reviewer_id != p.reviewer_id:
        r = db.get(User, reviewer_id) if reviewer_id else None
        if reviewer_id and (not r or r.role != Role.DOCTOR.value or not r.active):
            raise Unprocessable("审核医师必须是在职的医师账号", code="bad_reviewer")
        p.reviewer_id = reviewer_id or None
        audit.record(db, actor, "style.profile.reviewer", entity_type="style_profile", entity_id=p.id, summary=f"变更临床审核医师：{p.subject_name}", detail={"reviewer": reviewer_id})
    p.row_version += 1
    audit.record(db, actor, "style.profile.update", entity_type="style_profile", entity_id=p.id, summary=f"更新画像资料：{p.subject_name}")
    return p


def disable_profile(db: Session, actor: Actor, p: StyleProfile, reason: str) -> None:
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise Unprocessable("请填写停用原因（至少 4 个字）", code="reason_required")
    if p.status == StyleProfileStatus.DISABLED.value:
        return
    p.status, p.disabled_reason = StyleProfileStatus.DISABLED.value, reason[:200]
    p.row_version += 1
    audit.record(db, actor, "style.profile.disable", entity_type="style_profile", entity_id=p.id, summary=f"停用画像：{p.subject_name}（{reason[:60]}）",
                 detail={"reason": reason})


def enable_profile(db: Session, actor: Actor, p: StyleProfile) -> None:
    if p.status == StyleProfileStatus.ACTIVE.value:
        return
    p.status, p.disabled_reason = StyleProfileStatus.ACTIVE.value, ""
    p.row_version += 1
    audit.record(db, actor, "style.profile.enable", entity_type="style_profile", entity_id=p.id, summary=f"重新启用画像：{p.subject_name}")


# --------------------------------------------------------------------------- versions
def draft_version(db: Session, p: StyleProfile) -> StyleVersion | None:
    return db.scalar(select(StyleVersion).where(StyleVersion.profile_id == p.id, StyleVersion.status == StyleVersionStatus.DRAFT.value))


def next_version_no(db: Session, p: StyleProfile) -> int:
    return (db.scalar(select(func.max(StyleVersion.version_no)).where(StyleVersion.profile_id == p.id)) or 0) + 1


def new_draft_version(db: Session, actor: Actor, p: StyleProfile, *, note: str = "") -> StyleVersion:
    """One DRAFT per profile. If an ACTIVE version exists its approved entries are carried into the draft (so the doctor edits a copy)."""
    ex = draft_version(db, p)
    if ex:
        return ex
    prev = db.get(StyleVersion, p.active_version_id) if p.active_version_id else None
    v = StyleVersion(profile_id=p.id, version_no=next_version_no(db, p), status=StyleVersionStatus.DRAFT.value, parent_version_id=prev.id if prev else None,
                     note=note[:400], created_by=actor.id)
    db.add(v)
    db.flush()
    if prev:
        for e in db.scalars(select(StyleEntry).where(StyleEntry.version_id == prev.id, StyleEntry.review_status == StyleReview.APPROVED.value)
                            .order_by(StyleEntry.sort_order)):
            ne = StyleEntry(version_id=v.id, profile_id=p.id, category=e.category, group_key=e.group_key, title=e.title, body_json=e.body_json, summary=e.summary,
                            conditions=e.conditions, exceptions=e.exceptions, support_count=e.support_count, group_total=e.group_total, confidence=e.confidence,
                            flags_json=e.flags_json, ai_eligible=e.ai_eligible, risk_ack_reason=e.risk_ack_reason, origin="carried", origin_entry_id=e.id,
                            review_status=StyleReview.APPROVED.value, review_note=e.review_note, reviewed_by=e.reviewed_by, reviewed_at=e.reviewed_at,
                            sort_order=e.sort_order)
            db.add(ne)
            db.flush()
            for ev in db.scalars(select(StyleEntryEvidence).where(StyleEntryEvidence.entry_id == e.id)):
                db.add(StyleEntryEvidence(entry_id=ne.id, case_id=ev.case_id, kind=ev.kind, quote=ev.quote, detail_json=ev.detail_json))
    audit.record(db, actor, "style.version.create", entity_type="style_version", entity_id=v.id, summary=f"新建草稿版本 v{v.version_no}：{p.subject_name}",
                 detail={"parent": prev.id if prev else None})
    return v


def ai_eligible_for(flags: list[dict], review: str, ack_reason: str) -> bool:
    """An entry may feed the AI prompt only if approved AND (no critical flag OR the reviewer gave a written reason)."""
    if review != StyleReview.APPROVED.value:
        return False
    crit = any(f.get("severity") == "critical" for f in (flags or []))
    return (not crit) or len((ack_reason or "").strip()) >= 8


def review_entry(db: Session, actor: Actor, p: StyleProfile, e: StyleEntry, *, action: str, note: str = "", ack_reason: str = "", edits: dict | None = None,
                 expected_version: int | None = None) -> StyleEntry:
    from server.deps import check_version
    v = db.get(StyleVersion, e.version_id)
    if v.status != StyleVersionStatus.DRAFT.value:
        raise Conflict("已发布的版本不可修改；请新建草稿版本后再编辑", code="version_immutable")
    check_version(e.row_version, expected_version, "经验条目", db=db, obj=e)
    if action not in ("approve", "reject", "revert", "edit"):
        raise Unprocessable("未知操作", code="bad_action")
    if action == "edit":
        edits = edits or {}
        for k in ("title", "summary", "conditions", "exceptions"):
            if k in edits and isinstance(edits[k], str):
                setattr(e, k, edits[k].strip()[:1500 if k in ("summary", "conditions", "exceptions") else 200])
        if e.review_status == StyleReview.APPROVED.value:
            e.review_status, e.reviewed_by, e.reviewed_at = StyleReview.PENDING.value, None, None        # 改动后需重新审核
        e.ai_eligible = False
    elif action == "approve":
        if e.origin in ("auto", "carried") and e.origin != "note":
            n_ev = db.scalar(select(func.count()).select_from(StyleEntryEvidence).where(StyleEntryEvidence.entry_id == e.id)) or 0
            if n_ev == 0 and e.category != "note":
                raise Conflict("没有病例证据的条目不能通过审核", code="no_evidence")
        crit = any(f.get("severity") == "critical" for f in (e.flags_json or []))
        if crit and len((ack_reason or "").strip()) < 8:
            raise Conflict("该条目命中了严重安全提示（如毒性药/十八反/妊娠禁忌/明显超量）。可以通过审核留作资料，但默认不会进入 AI 提示词；"
                           "如确需让 AI 参考，请填写至少 8 个字的书面理由。仅作资料保存请使用“通过（不供 AI 使用）”。", code="risk_ack_required",
                           details={"flags": [f for f in e.flags_json if f.get("severity") == "critical"]})
        e.review_status, e.review_note, e.reviewed_by, e.reviewed_at = StyleReview.APPROVED.value, note[:300], actor.id, utcnow()
        e.risk_ack_reason = (ack_reason or "").strip()[:300]
        e.ai_eligible = ai_eligible_for(e.flags_json, e.review_status, e.risk_ack_reason)
    elif action == "reject":
        e.review_status, e.review_note, e.reviewed_by, e.reviewed_at, e.ai_eligible = StyleReview.REJECTED.value, note[:300], actor.id, utcnow(), False
    elif action == "revert":
        e.review_status, e.reviewed_by, e.reviewed_at, e.ai_eligible = StyleReview.PENDING.value, None, None, False
    e.row_version += 1
    audit.record(db, actor, f"style.entry.{action}", entity_type="style_entry", entity_id=e.id, summary=f"{action}：{e.title[:60]}",
                 detail={"profile": p.id, "version": e.version_id, "ai_eligible": e.ai_eligible, "ack": bool(ack_reason)})
    return e


def approve_without_ai(db: Session, actor: Actor, p: StyleProfile, e: StyleEntry, note: str = "") -> StyleEntry:
    """Keep a risky entry as reference material for humans only (never fed to the model)."""
    v = db.get(StyleVersion, e.version_id)
    if v.status != StyleVersionStatus.DRAFT.value:
        raise Conflict("已发布的版本不可修改", code="version_immutable")
    e.review_status, e.review_note, e.reviewed_by, e.reviewed_at, e.ai_eligible = StyleReview.APPROVED.value, (note or "仅作资料")[:300], actor.id, utcnow(), False
    e.row_version += 1
    audit.record(db, actor, "style.entry.approve_no_ai", entity_type="style_entry", entity_id=e.id, summary=f"通过（不供 AI 使用）：{e.title[:60]}", detail={"profile": p.id})
    return e


_BULK_LOCKS: dict[str, object] = {}


def _bulk_lock(pid: str):
    import threading
    lk = _BULK_LOCKS.get(pid)
    if lk is None:
        lk = threading.Lock()
        _BULK_LOCKS[pid] = lk
    return lk


def bulk_review_entries(db: Session, actor: Actor, p: StyleProfile, items: list[dict], *, action: str = "approve", request_id: str = "") -> dict:
    """Batch-approve eligible pending entries in a draft version with per-item outcomes and audit."""
    from server.domain.ids import iso
    from server.errors import ApiError
    if action != "approve":
        raise Unprocessable("批量操作仅支持 approve", code="bad_action")
    lk = _bulk_lock(p.id)
    if not lk.acquire(blocking=False):
        raise Conflict("该画像正在执行批量审核，请勿重复提交", code="bulk_in_progress")
    try:
        results: list[dict] = []
        approved_ids: list[str] = []
        for it in items:
            eid = str(it.get("id") or "")
            rv = it.get("row_version")
            e = db.get(StyleEntry, eid)
            if e is None or e.profile_id != p.id:
                results.append({"id": eid, "title": "", "outcome": "skipped", "code": "not_found", "message": "经验条目不存在", "review_status": "", "reviewed_by": None, "reviewed_at": None})
                continue
            v = db.get(StyleVersion, e.version_id)
            if v is None or v.status != StyleVersionStatus.DRAFT.value:
                results.append({"id": e.id, "title": e.title, "outcome": "skipped", "code": "version_immutable", "message": "已发布的版本不可修改", "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
                continue
            if e.review_status != StyleReview.PENDING.value:
                results.append({"id": e.id, "title": e.title, "outcome": "skipped", "code": "not_pending", "message": f"当前状态为 {e.review_status}，非待审核", "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
                continue
            if rv is not None and e.row_version != rv:
                results.append({"id": e.id, "title": e.title, "outcome": "skipped", "code": "stale", "message": "条目已被更新，请刷新后重试", "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
                continue
            if any(f.get("severity") == "critical" for f in (e.flags_json or [])):
                results.append({"id": e.id, "title": e.title, "outcome": "skipped", "code": "critical_needs_individual_review", "message": "含严重安全提示，需逐条填写书面理由后审核", "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
                continue
            if e.origin in ("auto", "carried") and e.category != "note":
                n_ev = db.scalar(select(func.count()).select_from(StyleEntryEvidence).where(StyleEntryEvidence.entry_id == e.id)) or 0
                if n_ev == 0:
                    results.append({"id": e.id, "title": e.title, "outcome": "skipped", "code": "no_evidence", "message": "没有病例证据的条目不能通过审核", "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
                    continue
            try:
                with db.begin_nested():
                    review_entry(db, actor, p, e, action="approve", expected_version=rv)
                approved_ids.append(e.id)
                results.append({"id": e.id, "title": e.title, "outcome": "approved", "code": "ok", "message": "已通过", "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
            except ApiError as exc:
                results.append({"id": e.id, "title": e.title, "outcome": "skipped", "code": exc.code, "message": exc.message, "review_status": e.review_status, "reviewed_by": e.reviewed_by, "reviewed_at": iso(e.reviewed_at)})
        skipped_list = [{"id": r["id"], "code": r["code"]} for r in results if r["outcome"] == "skipped"]
        audit.record(db, actor, "style.entry.bulk_approve", entity_type="style_profile", entity_id=p.id,
                     summary=f"批量通过 {len(approved_ids)} 条（跳过 {len(skipped_list)}）",
                     detail={"request_id": request_id, "approved_ids": approved_ids, "skipped": skipped_list})
        return {"request_id": request_id, "summary": {"requested": len(items), "approved": len(approved_ids), "skipped": len(skipped_list)}, "results": results}
    finally:
        lk.release()


def add_manual_entry(db: Session, actor: Actor, p: StyleProfile, v: StyleVersion, *, category: str, title: str, summary: str, group_key: str = "", conditions: str = "",
                     exceptions: str = "") -> StyleEntry:
    from server.domain.enums import STYLE_CATEGORIES
    if v.status != StyleVersionStatus.DRAFT.value:
        raise Conflict("已发布的版本不可修改", code="version_immutable")
    if category not in STYLE_CATEGORIES:
        raise Unprocessable("条目类别不正确", code="bad_category")
    title, summary = title.strip(), summary.strip()
    if not title or len(summary) < 6:
        raise Unprocessable("请填写标题和至少 6 个字的内容", code="content_required")
    n = db.scalar(select(func.max(StyleEntry.sort_order)).where(StyleEntry.version_id == v.id)) or 0
    e = StyleEntry(version_id=v.id, profile_id=p.id, category=category, group_key=group_key.strip()[:80], title=title[:200], body_json={"text": summary},
                   summary=summary[:1500], conditions=conditions[:1500], exceptions=exceptions[:1500], origin="manual", confidence="low",
                   flags_json=[{"code": "self_reported", "severity": "info", "message": "医师手工填写，没有病例统计证据"}], sort_order=n + 1)
    db.add(e)
    db.flush()
    audit.record(db, actor, "style.entry.add", entity_type="style_entry", entity_id=e.id, summary=f"手工新增经验条目：{title[:60]}", detail={"profile": p.id})
    return e


def snapshot_hash(db: Session, v: StyleVersion) -> str:
    rows = db.scalars(select(StyleEntry).where(StyleEntry.version_id == v.id, StyleEntry.review_status == StyleReview.APPROVED.value).order_by(StyleEntry.id)).all()
    return hash_obj([[e.category, e.group_key, e.title, e.summary, e.body_json, e.conditions, e.exceptions, e.ai_eligible] for e in rows])


def publish_version(db: Session, actor: Actor, p: StyleProfile, v: StyleVersion, *, note: str = "") -> StyleVersion:
    if v.status != StyleVersionStatus.DRAFT.value:
        raise Conflict("只有草稿版本可以发布", code="version_not_draft")
    pend = db.scalar(select(func.count()).select_from(StyleEntry).where(StyleEntry.version_id == v.id, StyleEntry.review_status == StyleReview.PENDING.value)) or 0
    appr = db.scalar(select(func.count()).select_from(StyleEntry).where(StyleEntry.version_id == v.id, StyleEntry.review_status == StyleReview.APPROVED.value)) or 0
    if appr == 0:
        raise Conflict("至少需要一条已通过审核的条目才能发布", code="nothing_approved")
    if pend:
        raise Conflict(f"还有 {pend} 条候选条目未处理（通过/驳回）。请先审核完再发布，未审核的内容不会自动进入已发布版本。", code="entries_pending", details={"pending": pend})
    prev = db.get(StyleVersion, p.active_version_id) if p.active_version_id else None
    if prev:
        prev.status, prev.retired_at = StyleVersionStatus.RETIRED.value, utcnow()
    v.status, v.activated_by, v.activated_at = StyleVersionStatus.ACTIVE.value, actor.id, utcnow()
    v.snapshot_hash = snapshot_hash(db, v)
    if note:
        v.note = note[:400]
    p.active_version_id = v.id
    p.row_version += 1
    audit.record(db, actor, "style.version.publish", entity_type="style_version", entity_id=v.id, summary=f"发布 {p.subject_name} 经验画像 v{v.version_no}（{appr} 条）",
                 detail={"profile": p.id, "entries": appr, "snapshot_hash": v.snapshot_hash, "replaces": prev.id if prev else None})
    return v


def rollback_to(db: Session, actor: Actor, p: StyleProfile, target: StyleVersion, reason: str) -> StyleVersion:
    """Re-publish an older version's content as a NEW version (history is never rewritten)."""
    if len((reason or "").strip()) < 4:
        raise Unprocessable("请填写回退原因", code="reason_required")
    if target.profile_id != p.id or target.status == StyleVersionStatus.DRAFT.value:
        raise Unprocessable("只能回退到已发布过的历史版本", code="bad_target")
    ex = draft_version(db, p)
    if ex:
        pend = db.scalar(select(func.count()).select_from(StyleEntry).where(StyleEntry.version_id == ex.id)) or 0
        if pend:
            raise Conflict("当前草稿版本中还有未发布的内容，请先发布或丢弃该草稿再回退", code="draft_exists")
        db.delete(ex)
        db.flush()
    v = StyleVersion(profile_id=p.id, version_no=next_version_no(db, p), status=StyleVersionStatus.DRAFT.value, parent_version_id=target.id,
                     note=f"回退到 v{target.version_no}：{reason.strip()[:200]}", created_by=actor.id)
    db.add(v)
    db.flush()
    for e in db.scalars(select(StyleEntry).where(StyleEntry.version_id == target.id, StyleEntry.review_status == StyleReview.APPROVED.value).order_by(StyleEntry.sort_order)):
        ne = StyleEntry(version_id=v.id, profile_id=p.id, category=e.category, group_key=e.group_key, title=e.title, body_json=e.body_json, summary=e.summary, conditions=e.conditions,
                        exceptions=e.exceptions, support_count=e.support_count, group_total=e.group_total, confidence=e.confidence, flags_json=e.flags_json,
                        ai_eligible=e.ai_eligible, risk_ack_reason=e.risk_ack_reason, origin="carried", origin_entry_id=e.id, review_status=StyleReview.APPROVED.value,
                        review_note=e.review_note, reviewed_by=e.reviewed_by, reviewed_at=e.reviewed_at, sort_order=e.sort_order)
        db.add(ne)
        db.flush()
        for ev in db.scalars(select(StyleEntryEvidence).where(StyleEntryEvidence.entry_id == e.id)):
            db.add(StyleEntryEvidence(entry_id=ne.id, case_id=ev.case_id, kind=ev.kind, quote=ev.quote, detail_json=ev.detail_json))
    audit.record(db, actor, "style.version.rollback", entity_type="style_version", entity_id=v.id, summary=f"回退 {p.subject_name} 画像到 v{target.version_no}（生成新草稿 v{v.version_no}）",
                 detail={"profile": p.id, "target": target.id, "reason": reason})
    return v


# --------------------------------------------------------------------------- authorization (共享授权)
def declare_authorization(db: Session, actor: Actor, user: User, p: StyleProfile, *, basis_type: str, basis_text: str, authorizer_name: str, scope: str = "hospital",
                          scope_dept: str = "", valid_until: datetime | None = None) -> StyleAuthorization:
    if p.kind != StyleKind.MASTER.value:
        raise Unprocessable("个人画像不能共享", code="personal_not_shareable")
    if basis_type not in ("self", "family", "institution", "publication", "other"):
        raise Unprocessable("授权依据类型不正确", code="bad_basis")
    basis_text = (basis_text or "").strip()
    if len(basis_text) < 6:
        raise Unprocessable("请填写授权来源说明（授权文件编号/出处，至少 6 个字）", code="basis_required")
    if not (authorizer_name or "").strip():
        raise Unprocessable("请填写授权人（本人/家属/单位名称）", code="authorizer_required")
    if scope not in ("hospital", "dept") or (scope == "dept" and not scope_dept.strip()):
        raise Unprocessable("请选择共享范围（全院或指定科室）", code="bad_scope")
    if basis_type == "self" and (p.owner_user_id != user.id):
        raise Unprocessable("“本人授权”只能由画像所有者本人声明；管理员代建请选择家属/单位/出版物等依据并填写文件编号", code="self_basis_mismatch")
    a = StyleAuthorization(profile_id=p.id, status=StyleAuthStatus.PENDING.value, basis_type=basis_type, basis_text=basis_text[:400], authorizer_name=authorizer_name.strip()[:64],
                           scope=scope, scope_dept=scope_dept.strip()[:64], valid_until=valid_until, declared_by=user.id, declared_by_role=user.role)
    db.add(a)
    db.flush()
    audit.record(db, actor, "style.auth.declare", entity_type="style_authorization", entity_id=a.id, summary=f"声明共享授权：{p.subject_name}（{basis_type}）",
                 detail={"profile": p.id, "basis": basis_text[:120], "scope": scope})
    return a


def approve_authorization(db: Session, actor: Actor, user: User, a: StyleAuthorization) -> StyleAuthorization:
    if not is_admin(user):
        raise Forbidden("只有管理员可以备案共享授权", code="style_forbidden")
    if a.status != StyleAuthStatus.PENDING.value:
        raise Conflict("该授权不是待备案状态", code="auth_state")
    for o in db.scalars(select(StyleAuthorization).where(StyleAuthorization.profile_id == a.profile_id, StyleAuthorization.status == StyleAuthStatus.APPROVED.value)):
        o.status, o.revoked_by, o.revoked_at, o.revoke_reason = StyleAuthStatus.REVOKED.value, user.id, utcnow(), "被新的授权备案取代"
    a.status, a.approved_by, a.approved_at = StyleAuthStatus.APPROVED.value, user.id, utcnow()
    audit.record(db, actor, "style.auth.approve", entity_type="style_authorization", entity_id=a.id, summary="备案共享授权", detail={"profile": a.profile_id, "basis": a.basis_text[:120]})
    return a


def revoke_authorization(db: Session, actor: Actor, user: User, a: StyleAuthorization, reason: str) -> StyleAuthorization:
    p = get_profile(db, a.profile_id)
    if not (is_admin(user) or can_review(user, p)):
        raise Forbidden("无权撤回该授权", code="style_forbidden")
    if len((reason or "").strip()) < 4:
        raise Unprocessable("请填写撤回原因", code="reason_required")
    if a.status == StyleAuthStatus.REVOKED.value:
        return a
    a.status, a.revoked_by, a.revoked_at, a.revoke_reason = StyleAuthStatus.REVOKED.value, user.id, utcnow(), reason.strip()[:300]
    audit.record(db, actor, "style.auth.revoke", entity_type="style_authorization", entity_id=a.id, summary=f"撤回共享授权：{reason.strip()[:60]}", detail={"profile": a.profile_id})
    return a


# --------------------------------------------------------------------------- encounter choice (按接诊选用)
def choose_for_encounter(db: Session, actor: Actor, user: User, enc_id: str, profile_ids: list[str]) -> list[str]:
    """Replace the set of profiles consulted for this encounter. Only profiles the doctor may use are accepted; the doctor's own personal profile is allowed."""
    ids = list(dict.fromkeys(profile_ids))[:5]
    if len(profile_ids) > 5:
        raise Unprocessable("每次接诊最多参考 5 份画像", code="too_many")
    ok: list[str] = []
    for pid in ids:
        p = db.get(StyleProfile, pid)
        if p is None or not usable_by(db, user, p):
            raise Unprocessable("所选画像不可用（已停用、尚未发布版本、或未获共享授权）", code="style_unusable", details={"profile_id": pid})
        ok.append(pid)
    cur = {c.profile_id: c for c in db.scalars(select(EncounterStyleChoice).where(EncounterStyleChoice.encounter_id == enc_id))}
    for pid, c in cur.items():
        if pid not in ok:
            db.delete(c)
    for pid in ok:
        if pid not in cur:
            db.add(EncounterStyleChoice(encounter_id=enc_id, profile_id=pid, chosen_by=user.id))
    audit.record(db, actor, "style.encounter.choose", entity_type="encounter", entity_id=enc_id, encounter_id=enc_id, summary=f"接诊选用经验画像：{len(ok)} 份", detail={"profiles": ok})
    return ok


def chosen_for_encounter(db: Session, enc_id: str) -> list[str]:
    return [c.profile_id for c in db.scalars(select(EncounterStyleChoice).where(EncounterStyleChoice.encounter_id == enc_id).order_by(EncounterStyleChoice.id))]


def disclaimer() -> str:
    return DISCLAIMER


# --------------------------------------------------------------------------- listing helpers
def list_visible(db: Session, u: User, *, kind: str | None = None) -> list[StyleProfile]:
    q = select(StyleProfile)
    if kind:
        q = q.where(StyleProfile.kind == kind)
    rows = db.scalars(q.order_by(StyleProfile.created_at.desc())).all()
    return [p for p in rows if visible_to(db, u, p)]


def case_counts(db: Session, profile_id: str) -> dict:
    rows = db.execute(select(StyleCase.status, func.count()).where(StyleCase.profile_id == profile_id).group_by(StyleCase.status)).all()
    return {s: n for s, n in rows}
