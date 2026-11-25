"""API 序列化（纯函数）。病例全文只给可管理者；授权画像的使用者只看到条目与逐字证据片段。"""
from __future__ import annotations

from server.db.models import (StyleAuthorization, StyleCase, StyleEntry, StyleEntryEvidence, StyleImport, StyleProfile, StyleSource, StyleVersion)
from server.domain.ids import iso


def profile_out(p: StyleProfile, *, auth: StyleAuthorization | None = None, active: StyleVersion | None = None, draft: StyleVersion | None = None,
                counts: dict | None = None, can_manage: bool = False, can_review: bool = False, usable: bool = False) -> dict:
    return {"id": p.id, "kind": p.kind, "subject_name": p.subject_name, "subject_title": p.subject_title, "subject_dept": p.subject_dept, "intro": p.intro,
            "status": p.status, "disabled_reason": p.disabled_reason, "proxy_created": p.proxy_created, "owner_user_id": p.owner_user_id, "reviewer_id": p.reviewer_id,
            "active_version": version_out(active) if active else None, "draft_version": version_out(draft) if draft else None,
            "authorization": auth_out(auth) if auth else None, "case_counts": counts or {},
            "can_manage": can_manage, "can_review": can_review, "usable": usable, "row_version": p.row_version, "created_at": iso(p.created_at)}


def version_out(v: StyleVersion) -> dict:
    return {"id": v.id, "version_no": v.version_no, "status": v.status, "note": v.note, "stats": v.stats_json or {}, "snapshot_hash": v.snapshot_hash,
            "created_at": iso(v.created_at), "activated_at": iso(v.activated_at), "retired_at": iso(v.retired_at)}


def auth_out(a: StyleAuthorization) -> dict:
    return {"id": a.id, "profile_id": a.profile_id, "status": a.status, "basis_type": a.basis_type, "basis_text": a.basis_text, "authorizer_name": a.authorizer_name,
            "scope": a.scope, "scope_dept": a.scope_dept, "valid_until": iso(a.valid_until), "declared_by_role": a.declared_by_role, "approved_at": iso(a.approved_at),
            "revoked_at": iso(a.revoked_at), "revoke_reason": a.revoke_reason, "created_at": iso(a.created_at)}


def import_out(i: StyleImport, sources: list[StyleSource] | None = None, job: dict | None = None) -> dict:
    return {"id": i.id, "profile_id": i.profile_id, "label": i.label, "status": i.status, "stats": i.stats_json or {}, "extract_mode": i.extract_mode,
            "ext_acked": bool(i.ext_ack_at), "draft_version_id": i.draft_version_id, "error_code": i.error_code, "error_detail": i.error_detail,
            "sources_purged": bool(i.sources_purged_at), "created_at": iso(i.created_at), "job": job,
            "sources": [source_out(s) for s in (sources or [])]}


def source_out(s: StyleSource) -> dict:
    return {"id": s.id, "kind": s.kind, "name": s.original_name, "size": s.size_bytes, "parse_status": s.parse_status, "parse_error": s.parse_error,
            "has_file": bool(s.storage_key), "mapping": (s.parsed_json or {}).get("mapping"), "unmapped": (s.parsed_json or {}).get("unmapped")}


def case_out(c: StyleCase, *, full: bool = True) -> dict:
    d = {"id": c.id, "seq": c.seq, "kind": c.kind, "origin": c.origin, "status": c.status, "section": c.section or "", "source_label": c.source_label, "visit_date": c.visit_date,
         "redaction": c.redaction_json or {}, "anomalies": c.anomaly_json or [], "herbs": c.herbs_json or [], "dup_of_id": c.dup_of_id, "dup_score": c.dup_score,
         "struct_status": c.struct_status, "struct_model": c.struct_model, "struct_error": c.struct_error, "fields": (c.structured_json or {}).get("fields") or {},
         "row_version": c.row_version}
    if full:
        d["text"] = c.text
    return d


def entry_out(e: StyleEntry, evidence: list[StyleEntryEvidence] | None = None, *, total_evidence: int | None = None) -> dict:
    d = {"id": e.id, "version_id": e.version_id, "category": e.category, "group_key": e.group_key, "title": e.title, "summary": e.summary, "body": _public_body(e.body_json),
         "conditions": e.conditions, "exceptions": e.exceptions, "support_count": e.support_count, "group_total": e.group_total, "confidence": e.confidence,
         "flags": e.flags_json or [], "ai_eligible": e.ai_eligible, "risk_ack_reason": e.risk_ack_reason, "origin": e.origin, "review_status": e.review_status,
         "review_note": e.review_note, "reviewed_at": iso(e.reviewed_at), "row_version": e.row_version, "origin_entry_id": e.origin_entry_id}
    if evidence is not None:
        d["evidence"] = [evidence_out(x) for x in evidence]
    if total_evidence is not None:
        d["evidence_total"] = total_evidence
    return d


def evidence_out(x: StyleEntryEvidence) -> dict:
    return {"case_id": x.case_id, "kind": x.kind, "quote": x.quote, "detail": x.detail_json or {}}


def _public_body(b: dict | None) -> dict:
    return {k: v for k, v in (b or {}).items() if not k.startswith("_")}
