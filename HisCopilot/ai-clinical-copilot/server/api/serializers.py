"""JSON serialisers — the only place that decides which fields leave the server for each audience.

Patient-facing serialisers (`patient_*`) are deliberately minimal: no diagnoses, AI output, plan, prescriptions,
doctor notes or internal ids beyond what the patient's own token already scopes.
"""
from __future__ import annotations

from typing import Any

from server.db.models import (Analysis, Appointment, Artifact, Diagnosis, Encounter, FollowupAlert, FollowupMessage, FollowupPlan,
                              FollowupResponse, FollowupTask, LabItem, MedicalRecord, Patient, Prescription, TherapyItem,
                              TreatmentPlan, User)
from server.domain.encounters import active_allergy_names, calc_age
from server.domain.ids import iso


def user_out(u: User, *, with_perms: bool = False) -> dict:
    d = {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "dept": u.dept, "title": u.title,
         "license_no": u.license_no, "active": u.active, "must_change_password": u.must_change_password,
         "last_login_at": iso(u.last_login_at)}
    if with_perms:
        from server.security.permissions import perms_for
        d["permissions"] = sorted(perms_for(u.role))
    return d


def user_brief(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.display_name, "title": u.title, "dept": u.dept} if u else None


def patient_out(p: Patient, *, detail: bool = False) -> dict:
    d = {"id": p.id, "mrn": p.mrn, "name": p.name, "gender": p.gender or None, "age": calc_age(p), "phone": p.phone or None,
         "is_sample": p.is_sample, "created_at": iso(p.created_at),
         "allergies": [{"id": a.id, "allergen": a.allergen, "reaction": a.reaction, "severity": a.severity, "confirmed": a.confirmed,
                        "source": a.source} for a in p.allergies if a.active],
         "chronic_conditions": p.chronic_conditions_json or []}
    if detail:
        d.update({"birth_date": p.birth_date.isoformat() if p.birth_date else None, "id_card": p.id_card or None,
                  "insurance": p.insurance or None, "address": p.address or None, "notes": p.notes,
                  "duplicate_candidates": p.dup_of_candidates_json or [], "row_version": p.row_version})
    return d


def encounter_out(e: Encounter, *, brief: bool = False, extra: dict | None = None) -> dict:
    d = {"id": e.id, "encounter_no": e.encounter_no, "status": e.status, "source": e.source, "visit_type": e.visit_type,
         "patient": {"id": e.patient.id, "name": e.patient.name, "gender": e.patient.gender or None, "age": calc_age(e.patient),
                     "mrn": e.patient.mrn, "is_sample": e.patient.is_sample},
         "doctor": user_brief(e.doctor), "chief_complaint": e.chief_complaint or None, "created_at": iso(e.created_at),
         "started_at": iso(e.started_at), "completed_at": iso(e.completed_at), "prev_encounter_id": e.prev_encounter_id,
         "inputs_version": e.inputs_version, "row_version": e.row_version}
    if not brief:
        d.update({"hpi": e.hpi, "past_history": e.past_history, "personal_family_history": e.personal_family_history,
                  "physical_exam": e.physical_exam, "symptoms": e.symptoms_json or [], "current_meds": e.current_meds_json or [],
                  "vitals": e.vitals_json or {}, "special_status": e.special_status_json or {}, "tcm_four": e.tcm_four_json or {},
                  "patient_reported": e.patient_reported_json or {}, "cancel_reason": e.cancel_reason or None})
    if extra:
        d.update(extra)
    return d


def artifact_out(a: Artifact) -> dict:
    return {"id": a.id, "kind": a.kind, "purpose": a.purpose, "original_name": a.original_name, "mime": a.mime, "size_bytes": a.size_bytes,
            "proc_status": a.proc_status, "error_code": a.error_code or None, "error_detail": a.error_detail or None,
            "attempt_count": a.attempt_count, "job_id": a.proc_job_id, "result_text": a.result_text or None,
            "edited_text": a.edited_text or None, "confirmed": bool(a.confirmed_at), "duration_s": a.duration_s,
            "uploaded_by": a.uploaded_by_kind, "created_at": iso(a.created_at),
            "result": ({k: v for k, v in (a.result_json or {}).items() if k != "meta"} if a.kind == "tongue_image" else None),
            "url": f"/api/v1/artifacts/{a.id}/file"}


def artifact_patient_out(a: Artifact) -> dict:
    """What a patient (intake session) may see of their own uploads: status + their own transcript to correct."""
    return {"id": a.id, "kind": a.kind, "purpose": a.purpose, "original_name": a.original_name, "proc_status": a.proc_status,
            "error_code": a.error_code or None, "error_detail": a.error_detail or None,
            "text": (a.edited_text or a.result_text or None) if a.kind == "audio" else None,
            "lab_items_found": (a.result_json or {}).get("items_found") if a.kind == "lab_image" else None,
            "created_at": iso(a.created_at)}


def lab_out(l: LabItem) -> dict:
    return {"id": l.id, "artifact_id": l.artifact_id, "abbr": l.abbr, "name": l.name, "value": l.value_text, "unit": l.unit,
            "ref": l.ref_text, "ref_low": l.ref_low, "ref_high": l.ref_high, "flag": l.flag, "source": l.source, "verified": l.verified}


def diagnosis_out(d: Diagnosis) -> dict:
    return {"id": d.id, "module": d.module, "name": d.name, "original_name": d.original_name or None, "icd_code": d.icd_code or None,
            "status": d.status, "origin": d.origin, "analysis_id": d.analysis_id, "confidence": d.confidence or None,
            "rationale": d.rationale, "evidence": d.evidence_json or {}, "doctor_note": d.doctor_note, "is_primary": d.is_primary,
            "sort_order": d.sort_order, "reviewed_at": iso(d.reviewed_at), "row_version": d.row_version}


def analysis_out(a: Analysis | None, *, facts: bool = True) -> dict | None:
    if a is None:
        return None
    return {"id": a.id, "status": a.status, "trigger": a.trigger, "inputs_version": a.inputs_version, "job_id": a.job_id,
            "is_current": a.is_current, "error_code": a.error_code or None, "error_detail": a.error_detail or None,
            "created_at": iso(a.created_at), "started_at": iso(a.started_at), "finished_at": iso(a.finished_at),
            "summary": a.summary_json or {}, "models": a.models_json or {}, "prompt_versions": a.prompt_versions_json or {},
            "facts": a.facts_json if facts else None,
            "modules": [{"module": m.module, "status": m.status, "error_code": m.error_code or None, "error_detail": m.error_detail or None,
                         "duration_ms": m.duration_ms, "model": m.model, "output": m.output_json or {}} for m in a.modules]}


def therapy_out(t: TherapyItem) -> dict:
    return {"id": t.id, "category": t.category, "name": t.name, "source_type": t.source_type, "acupoints": t.acupoints_json or [],
            "detail": t.detail_json or {}, "indications": t.indications, "contraindications": t.contraindications,
            "needs_confirmation": t.needs_confirmation_json or [], "rationale": t.rationale, "status": t.status,
            "original": t.original_json or {}, "doctor_note": t.doctor_note, "reviewed_at": iso(t.reviewed_at)}


def plan_out(p: TreatmentPlan | None, staleness: dict | None = None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "version_no": p.version_no, "status": p.status, "ai_used": p.ai_used, "content": p.content_json or {},
            "therapy_items": [therapy_out(t) for t in p.therapy_items], "dx_snapshot": p.dx_snapshot_json or [],
            "sections_requested": p.sections_requested_json or [], "sections_state": p.sections_state_json or {},
            "job_id": p.job_id, "error_code": p.error_code or None, "error_detail": p.error_detail or None,
            "confirmed_at": iso(p.confirmed_at), "created_at": iso(p.created_at), "row_version": p.row_version,
            "models": p.models_json or {}, "staleness": staleness, "style": p.style_json or None}


def rx_out(r: Prescription, *, brief: bool = False) -> dict:
    d = {"id": r.id, "rx_no": r.rx_no, "rx_type": r.rx_type, "status": r.status, "title": r.title, "encounter_id": r.encounter_id,
         "patient": {"id": r.patient.id, "name": r.patient.name, "gender": r.patient.gender or None, "age": calc_age(r.patient), "mrn": r.patient.mrn,
                     "allergies": active_allergy_names(r.patient)},
         "doctor": user_brief(r.doctor), "plan_id": r.plan_id, "signed_at": iso(r.signed_at), "his": {"mode": r.his_mode, "status": r.his_status,
                                                                                                           "ref": r.his_ref or None,
                                                                                                           "simulated": r.his_mode == "mock"},
         "created_at": iso(r.created_at), "row_version": r.row_version, "item_count": len(r.items),
         "safety_summary": (r.safety_json or {}).get("summary", {})}
    if not brief:
        d.update({"formula_type": r.formula_type, "formula_source": r.formula_source, "dx_links": r.dx_links_json or [],
                  "doses_count": r.doses_count, "decoction": r.decoction, "herbal_usage": r.herbal_usage, "notes": r.notes,
                  "ai_generated": r.ai_generated,
                  "items": [{"line_no": i.line_no, "drug_id": i.drug_id, "name": i.name, "spec": i.spec, "form": i.form,
                             "dose_text": i.dose_text, "frequency": i.frequency, "route": i.route, "days": i.days, "quantity": i.quantity,
                             "quantity_unit": i.quantity_unit, "dose_g": i.dose_g, "special": i.special, "role": i.role,
                             "instructions": i.instructions, "reason": i.reason, "in_catalog": i.in_catalog, "flags": i.flags_json or []}
                            for i in r.items],
                  "safety": r.safety_json or {}, "signature_hash": r.signature_hash or None, "override_reasons": r.override_reasons_json or [],
                  "review": {"by": r.review_by, "at": iso(r.review_at), "note": r.review_note},
                  "dispensed_at": iso(r.dispensed_at), "void_reason": r.void_reason or None, "print_count": r.print_count})
    return d


def record_out(rec: MedicalRecord, version, *, versions: list | None = None) -> dict:
    d = {"id": rec.id, "record_no": rec.record_no, "encounter_id": rec.encounter_id, "status": rec.status, "finalized_at": iso(rec.finalized_at),
         "patient": {"id": rec.patient.id, "name": rec.patient.name, "mrn": rec.patient.mrn},
         "version": {"id": version.id, "version_no": version.version_no, "kind": version.kind, "author": version.author_name,
                     "note": version.note, "created_at": iso(version.created_at), "content_hash": version.content_hash,
                     "signature_hash": version.signature_hash or None,
                     "sections": (version.content_json or {}).get("sections", {})}}
    if versions is not None:
        d["versions"] = [{"id": v.id, "version_no": v.version_no, "kind": v.kind, "author": v.author_name, "note": v.note,
                          "created_at": iso(v.created_at)} for v in versions]
    return d


def followup_task_out(t: FollowupTask, patient_name: str = "") -> dict:
    return {"id": t.id, "plan_id": t.plan_id, "encounter_id": t.encounter_id, "patient_id": t.patient_id, "patient_name": patient_name,
            "seq": t.seq, "kind": t.kind, "due_at": iso(t.due_at), "status": t.status, "questions": t.questions_json or [],
            "answered_at": iso(t.answered_at)}


def followup_response_out(r: FollowupResponse) -> dict:
    return {"id": r.id, "task_id": r.task_id, "answers": r.answers_json or {}, "free_text": r.free_text, "risk_level": r.risk_level,
            "rule_hits": r.rule_hits_json or [], "ai_summary": r.ai_summary_json or {}, "submitted_at": iso(r.submitted_at)}


def message_out(m: FollowupMessage) -> dict:
    return {"id": m.id, "sender_kind": m.sender_kind, "sender_name": m.sender_name, "body": m.body, "created_at": iso(m.created_at),
            "read": bool(m.read_at), "encounter_id": m.encounter_id, "task_id": m.task_id}


def appointment_out(a: Appointment, doctor_name: str = "", patient_name: str = "") -> dict:
    return {"id": a.id, "patient_id": a.patient_id, "patient_name": patient_name or a.guest_name, "guest_phone": a.guest_phone or None,
            "doctor_id": a.doctor_id, "doctor_name": doctor_name, "slot_start": iso(a.slot_start), "slot_end": iso(a.slot_end),
            "status": a.status, "reason": a.reason, "kind": a.kind, "encounter_id": a.encounter_id, "source_encounter_id": a.source_encounter_id}
