"""后台任务：style_parse（解析/OCR → 脱敏入库）与 style_extract（结构化抽取 → 归纳 → 候选条目写入草稿版本）。

沿用 v3 任务引擎的约定：先在短事务里读取纯数据 → 调模型时不持有数据库会话 → 再在短事务里写回。
资源池 `style`（单 worker）：几百份病例的批处理不会堵住患者的分析/方案任务；本地模型的每次调用仍通过 LLM_LOCK 串行。
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select

from server.ai.analysis import LLM_LOCK
from server.ai.client import ModelError
from server.db.base import session_scope
from server.db.models import Job, StyleCase, StyleEntry, StyleEntryEvidence, StyleImport, StyleProfile, StyleSource, StyleVersion
from server.domain.enums import StyleCaseStatus, StyleImportStatus, StyleReview
from server.domain.ids import hash_obj, utcnow
from server.errors import Unprocessable
from server.jobs.engine import JobContext, JobError, on_terminal, register
from server.security import audit
from server.security.audit import Actor
from server.style import extract as X
from server.style import induce as I
from server.style import parse as P
from server.style import pipeline as PL
from server.style import service as svc

log = logging.getLogger("xy.style")
_UNSUPPORTED = {"unsupported_file", "legacy_office", "pdf_scanned", "pdf_encrypted", "bad_file", "zip_bomb", "empty_content", "bad_json"}


# =========================================================================== parse
@register("style_parse", resource="style")
def job_style_parse(ctx: JobContext) -> dict:
    iid = ctx.input["import_id"]
    with session_scope() as s:
        imp = s.get(StyleImport, iid)
        if imp is None:
            raise JobError("import_missing", "导入批次不存在", retryable=False)
        imp.status, imp.error_code, imp.error_detail = StyleImportStatus.PARSING.value, "", ""
        todo = [(x.id, x.kind, x.original_name, x.storage_key) for x in s.scalars(
            select(StyleSource).where(StyleSource.import_id == iid, StyleSource.parse_status.in_(("pending", "failed"))).order_by(StyleSource.created_at))]
    total = max(1, len(todo))
    made = 0
    for i, (sid, kind, name, key) in enumerate(todo):
        ctx.progress(0.05 + 0.85 * i / total, f"解析 {name or '文件'}（{i + 1}/{len(todo)}）")
        path = PL.source_path(key) if key else None
        err, status, parsed, ocr_text = "", "parsed", None, None
        if path is None or not path.exists():
            err, status = "file_missing: 源文件已被清除，无法解析", "failed"
        else:
            data = path.read_bytes()
            try:
                if kind == "image":
                    ocr_text, _engine = PL.ocr_bytes(data)
                else:
                    parsed = P.parse(data, name)
            except Unprocessable as e:
                err, status = f"{e.code}: {e.message}", ("unsupported" if e.code in _UNSUPPORTED else "failed")
            except ModelError as e:
                err, status = f"{e.code}: {e.message}", "failed"
        with session_scope() as s:
            imp, src = s.get(StyleImport, iid), s.get(StyleSource, sid)
            if err:
                src.parse_status, src.parse_error = status, err[:300]
            else:
                try:
                    if ocr_text is not None:
                        made += PL.ingest(s, imp, src, None, text_blocks=[ocr_text], origin="ocr", label=name)
                    else:
                        made += PL.ingest(s, imp, src, parsed, origin="table" if parsed.rows else "text")
                    src.parse_status, src.parse_error = "parsed", ""
                except Unprocessable as e:
                    src.parse_status, src.parse_error = "failed", f"{e.code}: {e.message}"[:300]
    with session_scope() as s:
        imp = s.get(StyleImport, iid)
        PL.finish_parsing(s, imp)
        status = imp.status
        audit.record(s, Actor.system("style-parse"), "style.import.parsed", entity_type="style_import", entity_id=iid,
                     summary=f"导入批次解析完成：{imp.stats_json.get('cases', 0)} 份病例 / {imp.stats_json.get('notes', 0)} 条笔记", detail=imp.stats_json)
    return {"import_id": iid, "cases_added": made, "status": status}


# =========================================================================== extract + induce
@register("style_extract", resource="style")
def job_style_extract(ctx: JobContext) -> dict:
    iid, mode = ctx.input["import_id"], ctx.input.get("mode", "local")
    with session_scope() as s:
        imp = s.get(StyleImport, iid)
        if imp is None:
            raise JobError("import_missing", "导入批次不存在", retryable=False)
        if mode == "external":
            if not svc.external_allowed(s):
                raise JobError("ext_disabled", "管理员尚未开启外部模型抽取", retryable=False)
            if not imp.ext_ack_at:
                raise JobError("ext_not_acked", "尚未确认“已核对脱敏并同意外发”", retryable=False)
        imp.status, imp.extract_mode, imp.error_code, imp.error_detail = StyleImportStatus.EXTRACTING.value, mode, "", ""
        q = select(StyleCase).where(StyleCase.import_id == iid, StyleCase.status == StyleCaseStatus.CONFIRMED.value, StyleCase.struct_status.in_(("pending", "failed")))
        cases = [(c.id, c.kind, c.text, c.herbs_json or [], (c.redaction_json or {}).get("risk", "low")) for c in s.scalars(q.order_by(StyleCase.seq))]
    n, done, fails, streak = len(cases), 0, 0, 0
    role = {"local": "main", "external": "ext"}.get(mode, "main")
    for i, (cid, kind, text, herbs, risk) in enumerate(cases):
        ctx.progress(0.05 + 0.8 * i / max(1, n), f"抽取 {i + 1}/{n}")
        if kind == "note":
            res = {"fields": {}, "herbs": [], "status": "skipped", "model": "", "dropped": 0, "error": ""}
        elif mode == "external" and risk == "high":
            res = {"fields": {}, "herbs": herbs, "status": "skipped", "model": "", "dropped": 0, "error": "脱敏残留风险高，未外发；已用规则抽取"}
            res = {**X.extract_rules(text, herbs), "error": res["error"]}
        elif mode == "rules":
            res = X.extract_rules(text, herbs)
        else:
            try:
                if role == "ext":
                    res = X.extract(text, herbs, role="ext")
                else:
                    with LLM_LOCK:
                        res = X.extract(text, herbs, role="main")
            except ModelError as e:                                  # extract() 内部已吞掉大部分，这里兜底
                res = {**X.extract_rules(text, herbs), "error": f"{e.code}: {e.message}"}
            if res["status"] == "rules_only" and res["error"]:
                fails += 1
                streak += 1
                if "ext_not_configured" in res["error"]:
                    raise JobError("ext_not_configured", res["error"], retryable=False)
                if streak >= 3 and done == 0:
                    raise ModelError("model_unreachable", "连续 3 份病例调用模型失败，已停止；请检查模型服务后重试（已完成的不会重复）", retryable=True)
            else:
                streak = 0
        with session_scope() as s:
            c = s.get(StyleCase, cid)
            c.structured_json = {"fields": res["fields"], "dropped": res["dropped"]}
            c.struct_status, c.struct_model, c.struct_error = res["status"], res["model"], res["error"][:300]
        done += 1
    ctx.progress(0.9, "归纳经验条目…")
    with session_scope() as s:
        imp = s.get(StyleImport, iid)
        p = s.get(StyleProfile, imp.profile_id)
        summary = build_draft(s, imp, p)
        PL.refresh_stats(s, imp)
        imp.status = StyleImportStatus.EXTRACTED.value
        PL.purge_sources(s, imp)                                     # 原始文件含未脱敏信息：抽取完成后立即清除
        imp.stats_json = {**imp.stats_json, **summary, "extract_failures": fails}
        audit.record(s, Actor.system("style-extract"), "style.import.extracted", entity_type="style_import", entity_id=iid,
                     summary=f"抽取与归纳完成：新增候选 {summary['new']}、更新候选 {summary['updates']}、刷新 {summary['refreshed']}", detail={**summary, "mode": mode})
    return {"import_id": iid, **summary}


# --------------------------------------------------------------------------- draft building
def _case_dicts(s, profile_id: str) -> list[dict]:
    out = []
    for c in s.scalars(select(StyleCase).where(StyleCase.profile_id == profile_id, StyleCase.status == StyleCaseStatus.CONFIRMED.value).order_by(StyleCase.created_at, StyleCase.seq)):
        fields = (c.structured_json or {}).get("fields") or {}
        out.append({"id": c.id, "kind": c.kind, "text": c.text, "fields": fields, "herbs": c.herbs_json or [], "visit_date": c.visit_date,
                    "section": c.section or "", "group_key": X.group_key(fields) if c.kind == "case" else ""})
    return out


def material_hash(e: dict) -> str:
    return hash_obj([e["category"], e["group_key"], e["key"], e["support"], e["total"], e["summary"]])[:16]


def build_draft(s, imp: StyleImport, p: StyleProfile) -> dict:
    """把归纳出的条目作为**候选**写入草稿版本。永远不改动 ACTIVE 版本，也不静默改动已审核通过的条目（见 service.review_entry 的“替换”语义）。"""
    actor = Actor.system("style-induce")
    v = svc.new_draft_version(s, actor, p, note=f"导入：{imp.label or imp.id}")
    entries, stats = I.build(_case_dicts(s, p.id), today=date.today())
    existing: dict[str, list[StyleEntry]] = {}
    for e in s.scalars(select(StyleEntry).where(StyleEntry.version_id == v.id)):
        existing.setdefault(f"{e.category}|{e.group_key}|{(e.body_json or {}).get('_key', e.id)}", []).append(e)
    new = upd = refreshed = skipped = 0
    for cand in entries:
        mk = I.entry_match_key(cand)
        h = material_hash(cand)
        same = existing.get(mk, [])
        if any((x.body_json or {}).get("_hash") == h for x in same):
            skipped += 1
            continue
        body = {**cand["body"], "_key": cand["key"], "_hash": h}
        pending = [x for x in same if x.review_status == StyleReview.PENDING.value and x.origin in ("auto", "note")]
        approved = [x for x in same if x.review_status == StyleReview.APPROVED.value]
        if pending:                                                 # 候选尚未审核：就地刷新为最新统计
            e = pending[0]
            e.title, e.body_json, e.summary, e.support_count, e.group_total, e.confidence = cand["title"], body, cand["summary"], cand["support"], cand["total"], cand["confidence"]
            e.flags_json, e.conditions, e.exceptions = cand["flags"], cand["conditions"], cand["exceptions"]
            e.row_version += 1
            for ev in s.scalars(select(StyleEntryEvidence).where(StyleEntryEvidence.entry_id == e.id)):
                s.delete(ev)
            s.flush()
            refreshed += 1
        else:
            flags = list(cand["flags"])
            origin_id = None
            if approved:
                origin_id = approved[0].id
                flags.append({"code": "update_candidate", "severity": "info",
                              "message": f"新资料使该经验的统计发生变化（原：{approved[0].support_count}/{approved[0].group_total} 例 → 现：{cand['support']}/{cand['total']} 例）。通过后将替换已审核的旧条目；未通过前旧条目保持不变"})
                upd += 1
            else:
                new += 1
            n = s.scalar(select(StyleEntry.sort_order).where(StyleEntry.version_id == v.id).order_by(StyleEntry.sort_order.desc()).limit(1)) or 0
            e = StyleEntry(version_id=v.id, profile_id=p.id, category=cand["category"], group_key=cand["group_key"], title=cand["title"], body_json=body, summary=cand["summary"],
                           conditions=cand["conditions"], exceptions=cand["exceptions"], support_count=cand["support"], group_total=cand["total"], confidence=cand["confidence"],
                           flags_json=flags, origin="note" if cand["origin"] == "note" else "auto", origin_entry_id=origin_id, review_status=StyleReview.PENDING.value,
                           sort_order=n + 1)
            s.add(e)
            s.flush()
        seen: set[tuple[str, str]] = set()
        for ev in cand["evidence"]:
            k = (ev["case_id"], ev["kind"])
            if k in seen or not ev.get("quote"):
                continue
            seen.add(k)
            s.add(StyleEntryEvidence(entry_id=e.id, case_id=ev["case_id"], kind=ev["kind"], quote=ev["quote"][:300], detail_json=ev.get("detail") or {}))
    imps = list((v.stats_json or {}).get("imports", [])) + [imp.id]
    v.stats_json = {**stats, "imports": list(dict.fromkeys(imps))}
    imp.draft_version_id = v.id
    s.flush()
    return {"new": new, "updates": upd, "refreshed": refreshed, "unchanged": skipped, "draft_version_id": v.id, "groups": len(stats["groups"]),
            "below_threshold": stats["below_threshold"]}


# =========================================================================== terminal sync
def _on_terminal(job_id: str) -> None:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None or j.kind not in ("style_parse", "style_extract") or j.status not in ("failed", "cancelled"):
            return
        imp = s.get(StyleImport, (j.input_json or {}).get("import_id", ""))
        if imp is None or imp.status in (StyleImportStatus.EXTRACTED.value, StyleImportStatus.CANCELLED.value):
            return
        if j.status == "cancelled":
            imp.status = StyleImportStatus.CANCELLED.value
        elif j.kind == "style_extract":
            imp.status = StyleImportStatus.NEEDS_REVIEW.value        # 病例仍在，医师可修正后重试抽取
            imp.error_code, imp.error_detail = j.error_code or "extract_failed", (j.error_detail or "")[:1500]
        else:
            imp.status = StyleImportStatus.FAILED.value
            imp.error_code, imp.error_detail = j.error_code or "parse_failed", (j.error_detail or "")[:1500]


on_terminal(_on_terminal)
