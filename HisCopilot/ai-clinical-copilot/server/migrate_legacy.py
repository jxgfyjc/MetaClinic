"""One-way, idempotent import of the legacy `ai_clinical.db` into the v3 schema.

Principles
  * The legacy file is opened READ-ONLY (`mode=ro`); it is never modified. It stays as the rollback source.
  * Every migrated row is recorded in `legacy_map(legacy_table, legacy_id → new_table, new_id)`; re-running skips
    anything already mapped, so the import is safe to repeat and safe to run on a live system.
  * Nothing is fabricated: unknown values stay empty ("未填写"), legacy "DEMO-…" placeholder phones are dropped,
    patients are flagged `is_sample` when the legacy row had `is_demo`.
  * Legacy AI output is kept as READ-ONLY history (analyses/modules/diagnoses carry `legacy_source`); the legacy
    "unified draft" becomes a v3 `TreatmentPlan` (+TherapyItems) and its e-prescriptions become `Prescription`s
    linked to that plan — they arrive as ordinary draft/signed documents the doctor can inspect, never re-sent.
  * Finalised legacy records become `final` RecordVersions with a frozen snapshot, so they stay readable/printable.
  * Legacy users keep their existing bcrypt password hash (same algorithm) so people can still log in. If a hash
    still verifies against a KNOWN default password (the documented demo password) the account is flagged
    `must_change_password` — the API refuses every call except change-password until it is changed. A legacy user with
    no usable hash becomes a DISABLED stub so history keeps an author name.

CLI:  python -m server.migrate_legacy [--dry-run]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.base import init_db, session_scope
from server.db import migrate as mig
from server.db.models import (Analysis, AnalysisModule, Appointment, Artifact, AuditEvent, Diagnosis, Encounter, FollowupAlert, FollowupPlan,
                              FollowupResponse, FollowupTask, LabItem, LegacyMap, MedicalRecord, Patient, PatientAllergy, Prescription,
                              PrescriptionItem, RecordVersion, TherapyItem, TreatmentPlan, User)
from server.domain import records as rec_svc
from server.domain.enums import (AnalysisStatus, ArtifactKind, DxStatus, EncounterStatus, FuPlanStatus, FuTaskStatus, ModuleStatus, PlanStatus, ProcStatus, RxStatus)
from server.domain.ids import hash_obj, iso, new_id, utcnow
from server.domain.numbering import next_encounter_no, next_mrn, next_record_no, next_rx_no
from server.safety.data import reference as R
from server.security.auth import hash_password

LEGACY_NOTE = "legacy-import"


def _ts(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).replace("T", " ")[:26]
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _j(v, default):
    if v in (None, ""):
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default


class Importer:
    def __init__(self, db: Session, src: sqlite3.Connection):
        self.db, self.src = db, src
        self.map: dict[tuple[str, str], str] = {(m.legacy_table, m.legacy_id): m.new_id for m in db.scalars(select(LegacyMap))}
        self.stats: dict[str, int] = {}
        self.warnings: list[str] = []

    # -------------------------------------------------------------- helpers
    def q(self, sql: str, *a):
        cur = self.src.execute(sql, a)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def known(self, table: str, lid: str) -> str | None:
        return self.map.get((table, lid))

    def remember(self, table: str, lid: str, new_table: str, new_id_: str, note: str = "") -> None:
        self.db.add(LegacyMap(legacy_table=table, legacy_id=lid, new_table=new_table, new_id=new_id_, note=note))
        self.map[(table, lid)] = new_id_
        self.stats[new_table] = self.stats.get(new_table, 0) + 1

    # -------------------------------------------------------------- users
    KNOWN_DEFAULT_PASSWORDS = ("demo123", "Demo12345", "admin123", "123456", "password")

    @staticmethod
    def _usable_hash(h: str) -> bool:
        return bool(re.fullmatch(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}", h or ""))

    def _is_default_pw(self, h: str) -> bool:
        import bcrypt
        for pw in self.KNOWN_DEFAULT_PASSWORDS:
            try:
                if bcrypt.checkpw(pw.encode(), h.encode()):
                    return True
            except ValueError:
                return False
        return False

    def users(self) -> None:
        by_username = {u.username: u for u in self.db.scalars(select(User))}
        for r in self.q("select * from users"):
            if self.known("users", r["id"]):
                continue
            u = by_username.get(r["id"])
            if u is None:
                has_pw = self._usable_hash(r["password_hash"] or "")
                m = re.search(r"(副主任医师|主任医师|主治医师|主管护师|主管药师)$", r["name"] or "")
                name = re.sub(r"\s*(副主任医师|主任医师|主治医师|主管护师|主管药师)$", "", r["name"] or "").strip()
                u = User(username=r["id"], display_name=name or r["name"], role=r["role"] if r["role"] in ("DOCTOR", "NURSE", "ADMIN", "PHARMACIST") else "DOCTOR",
                         title=m.group(1) if m else "", password_hash=(r["password_hash"] if has_pw else ""),
                         active=bool(r["is_active"]) and has_pw, must_change_password=has_pw and self._is_default_pw(r["password_hash"]))
                self.db.add(u)
                self.db.flush()
                if not has_pw:
                    self.warnings.append(f"legacy user {r['id']} has no usable password hash → imported as DISABLED stub")
                elif u.must_change_password:
                    self.warnings.append(f"legacy user {r['id']} still uses a known default password → must change it at first login")
            self.remember("users", r["id"], "users", u.id)
        for r in self.q("select * from doctor_profiles"):
            uid = self.known("users", r["doctor_id"])
            u = self.db.get(User, uid) if uid else None
            if u and not u.profile_json:
                u.profile_json = {k: _j(r.get(k), r.get(k) or "") if k in ("specialties", "experience_notes") else (r.get(k) or "") for k in
                                  ("specialties", "syndrome_preference", "formula_preference", "herb_preference", "non_drug_preference", "analysis_focus", "experience_notes")}
                u.dept = u.dept or r.get("dept", "")

    # -------------------------------------------------------------- patients
    def patients(self) -> None:
        for r in self.q("select * from patients order by created_at"):
            if self.known("patients", r["id"]):
                continue
            extra = _j(r["extra_json"], {})
            created = _ts(r["created_at"])
            phone = (r["phone"] or "").strip()
            phone = "" if (phone.upper().startswith("DEMO") or not re.match(r"^1[3-9]\d{9}$", phone)) else phone
            gender = r["gender"] if r["gender"] in ("男", "女") else ""
            p = Patient(mrn=next_mrn(self.db, created), name=(r["name"] or "").strip() or "（未填写姓名）", gender=gender, age_years=r["age"] or None, phone=phone,
                        id_card=extra.get("id_card", "") or "", insurance=(extra.get("insurance", "") or "").replace("DEMO-", ""), address=extra.get("address", "") or "",
                        chronic_conditions_json=extra.get("chronic_conditions") if isinstance(extra.get("chronic_conditions"), list) else [],
                        is_sample=bool(r["is_demo"]), created_by="legacy", created_at=created or utcnow())
            self.db.add(p)
            self.db.flush()
            seen = set()
            for a in extra.get("allergies") or []:
                name = (a.get("allergen") if isinstance(a, dict) else str(a)).strip()
                if name and name not in seen:
                    seen.add(name)
                    self.db.add(PatientAllergy(patient_id=p.id, allergen=name, severity=(a.get("severity", "") if isinstance(a, dict) else ""), source="patient", confirmed=False))
            self.remember("patients", r["id"], "patients", p.id)

    # -------------------------------------------------------------- encounters
    def encounters(self) -> None:
        status_map = {"PENDING": EncounterStatus.WAITING.value, "IN_PROGRESS": EncounterStatus.IN_PROGRESS.value, "AI_REVIEW": EncounterStatus.IN_PROGRESS.value,
                      "SIGNED": EncounterStatus.PENDING_CONFIRM.value, "SUBMITTED": EncounterStatus.PENDING_CONFIRM.value}
        for r in self.q("select * from encounters order by created_at"):
            if self.known("encounters", r["id"]):
                continue
            pid = self.known("patients", r["patient_id"])
            if not pid:
                self.warnings.append(f"encounter {r['id']} skipped: patient {r['patient_id']} missing")
                continue
            cd = _j(r["clinical_data"], {})
            hist = cd.get("history") or {}
            created = _ts(r["created_at"])
            did = self.known("users", r["doctor_id"]) if r["doctor_id"] else None
            meds = [{"name": m.get("name", ""), "dose": m.get("dose", ""), "frequency": m.get("freq", m.get("frequency", ""))} for m in (cd.get("medications") or []) if isinstance(m, dict)]
            src = {"patient": "patient_intake", "doctor": "staff"}.get(cd.get("source", ""), "staff")
            e = Encounter(encounter_no=next_encounter_no(self.db, created), patient_id=pid, doctor_id=did, status=status_map.get(r["status"], "WAITING"), source=src,
                          chief_complaint=r["chief_complaint"] or "", hpi=cd.get("raw_text", "") or "", symptoms_json=hist.get("symptoms", []) if isinstance(hist.get("symptoms"), list) else [],
                          past_history="、".join(hist.get("comorbidities", [])) if isinstance(hist.get("comorbidities"), list) else "", current_meds_json=meds,
                          patient_reported_json={"legacy": True, "chief_complaint": r["chief_complaint"], "raw_text": cd.get("raw_text", "")} if src == "patient_intake" else {},
                          created_by="legacy", created_at=created or utcnow())
            self.db.add(e)
            self.db.flush()
            self.db.refresh(e)
            # legacy allergies on the encounter → patient allergy list (union)
            pat = self.db.get(Patient, pid)
            have = {a.allergen for a in pat.allergies}
            for a in cd.get("allergies") or []:
                nm = (a.get("allergen") if isinstance(a, dict) else str(a)).strip()
                if nm and nm not in have:
                    self.db.add(PatientAllergy(patient_id=pid, allergen=nm, severity=a.get("severity", "") if isinstance(a, dict) else "", source="patient"))
                    have.add(nm)
            self.db.flush()
            self.db.refresh(pat)
            self.remember("encounters", r["id"], "encounters", e.id)

    def labs(self) -> None:
        for r in self.q("select * from lab_results order by test_time"):
            if self.known("lab_results", r["id"]):
                continue
            eid, pid = self.known("encounters", r["encounter_id"]), self.known("patients", r["patient_id"])
            if not (eid and pid):
                continue
            ref = r["reference_high"] or ""
            flag = {"HIGH": "H", "LOW": "L", "NORMAL": "N"}.get((r["abnormal_flag"] or "").upper(), "")
            try:
                num = float(r["value"])
            except (TypeError, ValueError):
                num = None
            l = LabItem(encounter_id=eid, patient_id=pid, name=r["test_name"], value_text=str(r["value"] or ""), value_num=num, unit=r["unit"] or "", ref_text=ref,
                        flag=flag, source="manual", verified=False)         # legacy labs were never doctor-verified against an image
            self.db.add(l)
            self.db.flush()
            self.remember("lab_results", r["id"], "lab_items", l.id)

    def tongue_images(self) -> None:
        obj = Path(get_settings().xy_data_dir) / "objects"
        for r in self.q("select * from tongue_images"):
            if self.known("tongue_images", r["id"]):
                continue
            eid, pid = self.known("encounters", r["encounter_id"]), self.known("patients", r["patient_id"])
            src_file = obj / r["object_key"]
            if not (eid and pid and src_file.exists()):
                self.warnings.append(f"tongue image {r['id']} skipped (missing encounter or file {r['object_key']})")
                continue
            data = src_file.read_bytes()
            from server.domain.artifacts import store
            a, created = store(self.db, kind=ArtifactKind.TONGUE_IMAGE.value, data=data, original_name=r["object_key"], owner={"encounter_id": eid, "patient_id": pid, "by_kind": "staff", "by_id": "legacy"})
            a.proc_status = ProcStatus.NOT_REQUIRED.value
            self.remember("tongue_images", r["id"], "artifacts", a.id)

    # -------------------------------------------------------------- AI history (read-only)
    def analyses(self) -> None:
        st_map = {"SUCCEEDED": AnalysisStatus.SUCCEEDED.value, "PARTIAL": AnalysisStatus.PARTIAL.value, "FAILED": AnalysisStatus.FAILED.value,
                  "RUNNING": AnalysisStatus.FAILED.value, "PENDING": AnalysisStatus.FAILED.value, "TIMEOUT": AnalysisStatus.FAILED.value}
        runs = self.q("select * from ai_runs order by created_at")
        latest_by_enc: dict[str, str] = {}
        for r in runs:
            latest_by_enc[r["encounter_id"]] = r["id"]
        for r in runs:
            if self.known("ai_runs", r["id"]):
                continue
            eid = self.known("encounters", r["encounter_id"])
            if not eid:
                continue
            outs = {o["output_type"]: _j(o["structured_json"], {}) for o in self.q("select * from ai_outputs where ai_run_id=?", r["id"])}
            status = st_map.get(r["status"], "failed")
            stuck = r["status"] in ("RUNNING", "PENDING")
            a = Analysis(encounter_id=eid, status=status, trigger="legacy", inputs_version=0, inputs_hash="legacy:" + (r["input_hash"] or "")[:16],
                         is_current=False, requested_by="legacy", error_code="legacy_interrupted" if stuck else "", error_detail="旧系统中该任务从未完成（进程中断）" if stuck else "",
                         created_at=_ts(r["created_at"]) or utcnow(), finished_at=_ts(r["created_at"]),
                         summary_json={"headline": (outs.get("synthesis") or {}).get("summary", ""), "legacy": True},
                         models_json={"legacy": r["model_name"]})
            self.db.add(a)
            self.db.flush()
            for mod in ("western", "tcm", "tongue", "synthesis"):
                o = outs.get(mod)
                if o is None:
                    continue
                ms = ModuleStatus.FAILED.value if o.get("failed") else (ModuleStatus.SKIPPED.value if o.get("skipped") else ModuleStatus.SUCCEEDED.value)
                self.db.add(AnalysisModule(analysis_id=a.id, module=mod, status=ms, sort_order={"tongue": 0, "western": 1, "tcm": 2, "synthesis": 3}[mod],
                                           output_json={"legacy": True, **o}, error_detail=o.get("reason", "") if ms == ms and o.get("failed") else ""))
            self.remember("ai_runs", r["id"], "analyses", a.id)
            if latest_by_enc.get(r["encounter_id"]) == r["id"]:
                a.is_current = False                                    # legacy results are history; the doctor re-runs against v3 facts when needed

    def diagnoses(self) -> None:
        for r in self.q("select * from diagnosis_records order by created_at"):
            if self.known("diagnosis_records", r["id"]):
                continue
            eid = self.known("encounters", r["encounter_id"])
            if not eid:
                continue
            name = (r["western_diagnosis"] if r["module"] == "western" else r["tcm_syndrome"]) or r["western_diagnosis"] or r["tcm_syndrome"] or ""
            if not name.strip():
                continue
            status = {"accepted": DxStatus.ACCEPTED.value, "modified": DxStatus.MODIFIED.value, "rejected": DxStatus.REJECTED.value}.get(r["review_status"], DxStatus.PENDING.value)
            d = Diagnosis(encounter_id=eid, module=r["module"] if r["module"] in ("western", "tcm") else "western", name=name.strip(), original_name=name.strip(),
                          status=status, origin="doctor" if r["source"] == "doctor" else "ai", rationale=r["main_evidence"] or "",
                          evidence_json={"legacy": True, "treatment_goal": r["treatment_goal"] or ""}, doctor_note=r["doctor_notes"] or "",
                          sort_order=0, reviewed_by=self.known("users", r["doctor_id"]) if r["doctor_id"] else None, created_at=_ts(r["created_at"]) or utcnow())
            self.db.add(d)
            self.db.flush()
            self.remember("diagnosis_records", r["id"], "diagnoses", d.id)
        # de-duplicate identical accepted diagnoses created by the legacy double-write bug
        seen: dict[tuple, Diagnosis] = {}
        for d in self.db.scalars(select(Diagnosis).where(Diagnosis.status.in_(["accepted", "modified"])).order_by(Diagnosis.created_at)):
            if not (d.evidence_json or {}).get("legacy"):
                continue
            k = (d.encounter_id, d.module, d.name)
            if k in seen:
                d.status = DxStatus.WITHDRAWN.value
                d.doctor_note = (d.doctor_note + " [导入时发现与另一条重复，已合并]").strip()
            else:
                seen[k] = d

    # -------------------------------------------------------------- plans / prescriptions
    def plans_and_prescriptions(self) -> None:
        completed_legacy = {r["encounter_id"] for r in self.q("select encounter_id from medical_records where status='finalized'")}
        drafts = self.q("select * from prescription_drafts order by created_at")
        reviews: dict[str, list[dict]] = {}
        for rv in self.q("select * from therapy_item_reviews"):
            reviews.setdefault(rv["prescription_draft_id"], []).append(rv)
        eps = self.q("select * from e_prescriptions order by created_at")
        ep_by_draft: dict[str, list[dict]] = {}
        for ep in eps:
            ep_by_draft.setdefault(ep["prescription_draft_id"], []).append(ep)
        for d in drafts:
            if self.known("prescription_drafts", d["id"]):
                continue
            eid, pid = self.known("encounters", d["encounter_id"]), self.known("patients", d["patient_id"])
            if not (eid and pid):
                continue
            sj = _j(d["structured_json"], {})
            if d["target_diagnosis_id"] or sj.get("status") == "generating" or sj.get("error"):
                self.remember("prescription_drafts", d["id"], "skipped", "-", "per-diagnosis/failed legacy draft not migrated")
                self.stats["skipped_legacy_drafts"] = self.stats.get("skipped_legacy_drafts", 0) + 1
                continue                                                   # the old per-diagnosis model is exactly what v3 removes
            presc = sj.get("prescription") or {k: sj[k] for k in ("western_medications", "patent_medications", "patent_medicines", "herbal_drafts", "safety_notes") if k in sj}
            if not presc and not sj.get("therapy"):
                self.remember("prescription_drafts", d["id"], "skipped", "-", "empty draft")
                continue
            enc = self.db.get(Encounter, eid)
            dxs = [x for x in self.db.scalars(select(Diagnosis).where(Diagnosis.encounter_id == eid, Diagnosis.status.in_(["accepted", "modified"])))]
            ver = len(self.db.scalars(select(TreatmentPlan.id).where(TreatmentPlan.encounter_id == eid)).all()) + 1
            plan = TreatmentPlan(encounter_id=eid, version_no=ver, status=PlanStatus.DRAFT.value if d["status"] in ("AI_GENERATED", "DOCTOR_EDITING") else PlanStatus.CONFIRMED.value,
                                 dx_set_hash="legacy:" + (d["diagnosis_snapshot_hash"] or ""), dx_snapshot_json=[{"id": x.id, "module": x.module, "name": x.name, "status": x.status} for x in dxs],
                                 inputs_version=0, inputs_hash="legacy", sections_requested_json=["medication", "herbal", "therapy"],
                                 sections_state_json={"legacy": {"status": "imported"}}, ai_used=True, created_by="legacy", created_at=_ts(d["created_at"]) or utcnow(),
                                 content_json={"summary": sj.get("unified_plan_summary", ""), "coordination_notes": [], "medication": {"western": [], "patent": []}, "herbal": [],
                                               "lifestyle": (sj.get("therapy") or {}).get("lifestyle_advice", []), "monitoring": [], "followup_advice": "",
                                               "safety_notes": presc.get("safety_notes", []), "missing_info": [], "legacy": True},
                                 ai_original_json={"legacy": sj})
            self.db.add(plan)
            self.db.flush()
            # a legacy plan can never be silently "current": it is marked stale by construction (hash 'legacy…' ≠ any v3 hash)
            for i, c in enumerate((sj.get("therapy") or {}).get("candidates", [])):
                rv = next((x for x in reviews.get(d["id"], []) if x["item_index"] == i), None)
                st = {"ACCEPTED": "accepted", "EDITED": "modified", "REJECTED": "rejected"}.get((rv or {}).get("status", ""), "pending")
                edited = _j((rv or {}).get("edited_content"), {}) if rv else {}
                eff = {**c, **edited} if st == "modified" else c
                plan.therapy_items.append(TherapyItem(
                    encounter_id=eid, sort_order=i, category=eff.get("category", ""), name=eff.get("name", "(未命名)"), source_type="ai" if eff.get("source_type") == "hospital" else eff.get("source_type", "ai"),
                    acupoints_json=eff.get("acupoints", []), indications=eff.get("applicable_scenario", ""), contraindications=eff.get("contraindications", ""), rationale=eff.get("rationale", ""),
                    detail_json={k: eff.get(k, "") for k in ("location_basis", "manipulation", "direction_or_stimulation", "duration_or_count", "frequency", "course")} | {"stimulation": eff.get("direction_or_stimulation", ""), "duration": eff.get("duration_or_count", "")},
                    status=st, original_json={"name": c.get("name"), "category": c.get("category"), "acupoints": c.get("acupoints", [])} if st == "modified" else {}))
            self.remember("prescription_drafts", d["id"], "treatment_plans", plan.id)
            # ----- prescriptions: prefer the persisted e-prescriptions (signed documents); else the draft's own content
            made = False
            for ep in ep_by_draft.get(d["id"], []):
                if self.known("e_prescriptions", ep["id"]):
                    continue
                pj = _j(ep["prescription_json"], {})
                rx = self._make_rx(enc, pid, ep["doc_type"], pj, signed=True, doctor_legacy_id=ep["doctor_id"], created=_ts(ep["created_at"]), plan=plan, note="legacy e-prescription")
                if rx is not None:
                    self.remember("e_prescriptions", ep["id"], "prescriptions", rx.id)
                    made = True
            if not made and d["status"] in ("AI_GENERATED", "DOCTOR_EDITING") and presc and d["encounter_id"] not in completed_legacy:
                # unsigned AI drafts only matter on OPEN encounters; on a finalised visit they would be dead clutter
                for kind, pj in self._split_presc(presc):
                    rx = self._make_rx(enc, pid, kind[0], pj, signed=False, doctor_legacy_id=d["doctor_id"], created=_ts(d["created_at"]), plan=plan, note="legacy AI draft (unsigned)")
                    if rx is not None:
                        self.db.flush()
        self.db.flush()
        self._single_live_plan()

    def _single_live_plan(self) -> None:
        """v3 invariant: at most ONE live (draft/confirmed) plan per encounter. The legacy system kept several
        side-by-side; keep the one that backs signed prescriptions (else the newest) and supersede the rest."""
        by_enc: dict[str, list[TreatmentPlan]] = {}
        for pl in self.db.scalars(select(TreatmentPlan).where(TreatmentPlan.status.in_(["draft", "confirmed"]))):
            by_enc.setdefault(pl.encounter_id, []).append(pl)
        for eid, plans in by_enc.items():
            if len(plans) < 2:
                continue
            signed_plan_ids = {r.plan_id for r in self.db.scalars(select(Prescription).where(Prescription.encounter_id == eid, Prescription.status == "signed"))}
            keep = max(plans, key=lambda p: (p.id in signed_plan_ids, p.created_at))
            for pl in plans:
                if pl.id != keep.id:
                    pl.status, pl.superseded_by, pl.supersede_reason = "superseded", keep.id, "旧系统同一次就诊存在多份方案；导入时保留与已签署处方对应的/最新的一份"
        self.db.flush()

    @staticmethod
    def _split_presc(presc: dict):
        out = []
        if presc.get("western_medications"):
            out.append((("western_drugs",), {"western_medications": presc["western_medications"], "patent_medications": []}))
        if presc.get("patent_medicines") or presc.get("patent_medications"):
            out.append((("patent",), {"western_medications": [], "patent_medications": presc.get("patent_medicines") or presc.get("patent_medications")}))
        for hd in presc.get("herbal_drafts") or []:
            out.append((("herbal_decoction",), {"herbal_draft": hd}))
        return out

    def _make_rx(self, enc: Encounter, pid: str, doc_type: str, pj: dict, *, signed: bool, doctor_legacy_id: str, created, plan, note: str) -> Prescription | None:
        doctor_id = self.known("users", doctor_legacy_id) or enc.doctor_id
        if not doctor_id:
            doctor = self.db.scalar(select(User).where(User.role == "DOCTOR").order_by(User.username))
            doctor_id = doctor.id if doctor else None
        if not doctor_id:
            return None
        items: list[PrescriptionItem] = []
        rx_type, title, header = "western", "西药处方（导入）", {}
        if doc_type == "herbal_decoction":
            hd = pj.get("herbal_draft") or {}
            herbs = hd.get("herbs") or []
            doses = hd.get("doses") or {}
            if not herbs:
                return None
            rx_type, title = "herbal", hd.get("name") or "中药饮片处方（导入）"
            header = {"decoction": hd.get("decoction", "水煎"), "formula_type": hd.get("formula_type", ""), "formula_source": hd.get("rationale", "")[:120]}
            for n, h in enumerate(herbs, 1):
                g = self._grams(doses.get(h)) if isinstance(doses, dict) else None
                items.append(PrescriptionItem(line_no=n, name=str(h), dose_g=g, in_catalog=False,
                                              flags_json=([] if g else ["missing_dose"]) + ["legacy_unverified"]))
        else:
            wm = (pj.get("western_medications") or [])
            pm = (pj.get("patent_medications") or pj.get("patent_medicines") or [])
            rx_type = "western" if wm else "patent"
            title = "西药处方（导入）" if wm else "中成药处方（导入）"
            for n, m in enumerate(list(wm) + (list(pm) if not wm else []), 1):
                days = self._int(m.get("days") or m.get("duration"))
                items.append(PrescriptionItem(line_no=n, name=m.get("medication") or m.get("name") or "", dose_text=str(m.get("dose", "") or ""), frequency=str(m.get("frequency", "") or ""),
                                              route=m.get("usage") or "口服", days=days, reason=str(m.get("reason", ""))[:200], flags_json=["legacy_unverified"]))
            if not items:
                return None
        rx = Prescription(rx_no=next_rx_no(self.db, created), encounter_id=enc.id, patient_id=pid, doctor_id=doctor_id, plan_id=plan.id if plan else None, rx_type=rx_type,
                          status=RxStatus.SIGNED.value if signed else RxStatus.DRAFT.value, title=title, formula_type=header.get("formula_type", ""),
                          formula_source=header.get("formula_source", ""), doses_count=7 if rx_type == "herbal" else None, decoction=header.get("decoction", ""),
                          notes=note, ai_generated=True, created_by="legacy", created_at=created or utcnow(),
                          signed_by=doctor_id if signed else None, signed_at=created if signed else None,
                          signature_hash=hash_obj(["legacy", doc_type, pj]) if signed else "", his_mode="mock" if signed else "none",
                          his_status="mock_submitted" if signed else "not_submitted", his_ref="LEGACY-MOCK" if signed else "")
        self.db.add(rx)
        self.db.flush()
        for it in items:
            rx.items.append(it)
        self.db.flush()
        self.db.refresh(rx)
        from server.domain import prescriptions as rx_svc
        rx_svc.refresh_safety(self.db, rx, enc)
        # legacy dose_text for herbal rows lacks "doses_count"; the safety layer will keep reminding the doctor
        return rx

    @staticmethod
    def _grams(v) -> float | None:
        m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
        return float(m.group(0)) if m else None

    @staticmethod
    def _int(v) -> int | None:
        m = re.search(r"\d+", str(v or ""))
        return int(m.group(0)) if m else None

    # -------------------------------------------------------------- records
    def records(self) -> None:
        """One v3 MedicalRecord per encounter; every legacy row becomes a version (oldest→newest). A legacy
        `finalized` row becomes a frozen `final` version with a snapshot; later rows stay drafts."""
        rows = self.q("select * from medical_records order by encounter_id, created_at")
        by_enc: dict[str, list[dict]] = {}
        for r in rows:
            by_enc.setdefault(r["encounter_id"], []).append(r)
        for lenc, lst in by_enc.items():
            eid, pid = self.known("encounters", lenc), None
            if not eid:
                continue
            enc = self.db.get(Encounter, eid)
            rec = self.db.scalar(select(MedicalRecord).where(MedicalRecord.encounter_id == eid))
            if rec is None:
                rec = MedicalRecord(record_no=next_record_no(self.db, _ts(lst[0]["created_at"])), encounter_id=eid, patient_id=enc.patient_id, doctor_id=enc.doctor_id,
                                    created_at=_ts(lst[0]["created_at"]) or utcnow())
                self.db.add(rec)
                self.db.flush()
            for r in lst:
                if self.known("medical_records", r["id"]):
                    continue
                cj = _j(r["content_json"], {})
                content = self._legacy_sections(cj, r["raw_text"] or "", enc)
                finalized = (r["status"] or "") == "finalized"
                snap = self._legacy_snapshot(cj, enc) if finalized else {}
                already_final = rec.final_version_id is not None
                kind = ("amendment" if already_final else "final") if finalized else "draft"
                v = rec_svc._append_version(self.db, rec, kind=kind, content=content, author_id=r["doctor_id"] or "legacy",
                                            author_name=(self.db.get(User, self.known("users", r["doctor_id"])).display_name if r["doctor_id"] and self.known("users", r["doctor_id"]) else "（旧系统）"),
                                            note="从旧系统导入" + (("（旧系统再次定稿，按更正版本保留）" if already_final else "（原已定稿）") if finalized else ""), snapshot=snap,
                                            signature_hash=hash_obj(["legacy-final", r["id"]]) if finalized else "")
                v.legacy_source = f"medical_records:{r['id']}"
                v.created_at = _ts(r["finalized_at"] or r["updated_at"] or r["created_at"]) or v.created_at
                if finalized:
                    rec.status, rec.final_version_id, rec.finalized_at = "finalized", v.id, v.created_at
                    enc.status = EncounterStatus.COMPLETED.value
                    enc.completed_at = enc.completed_at or v.created_at
                else:
                    rec.working_version_id = v.id
                self.remember("medical_records", r["id"], "record_versions", v.id)
            if rec.status == "draft" and not rec.working_version_id:
                last = self.db.scalar(select(RecordVersion).where(RecordVersion.record_id == rec.id).order_by(RecordVersion.version_no.desc()).limit(1))
                rec.working_version_id = last.id if last else None
            if rec.status == "finalized":
                rec.working_version_id = rec.working_version_id or rec.final_version_id

    def _legacy_sections(self, cj: dict, raw_text: str, enc: Encounter) -> dict:
        from server.domain.records import SECTION_KEYS
        text = cj.get("text") or cj.get("editor_text") or raw_text or ""
        secs = {k: {"text": "", "origin": "doctor", "edited_by": "legacy", "edited_at": "", "auto_hash": ""} for k in SECTION_KEYS}
        if "patient_info" in cj:                                           # finalized shape: structured
            hist = cj.get("history") or {}
            secs["chief_complaint"]["text"] = cj.get("chief_complaint", "")
            secs["hpi"]["text"] = cj.get("raw_text", "")
            secs["past_history"]["text"] = "、".join(hist.get("comorbidities", [])) if isinstance(hist, dict) else ""
            secs["allergies"]["text"] = "、".join(a.get("allergen", "") for a in cj.get("allergies", []) if isinstance(a, dict)) or ""
            secs["current_meds"]["text"] = "；".join(f"{m.get('name', '')} {m.get('dose', '')} {m.get('freq', '')}".strip() for m in cj.get("medications", []) if isinstance(m, dict))
            secs["labs"]["text"] = "；".join(f"{l.get('item', '')} {l.get('value', '')}{l.get('unit', '')}" for l in cj.get("labs", []) if isinstance(l, dict))
            secs["diagnoses"]["text"] = "\n".join(d.get("western_diagnosis") or d.get("tcm_syndrome") or "" for d in cj.get("confirmed_diagnoses", []) if isinstance(d, dict))
            secs["treatment"]["text"] = cj.get("editor_text", "") or ""
        elif text:
            secs["hpi"]["text"] = text                                       # legacy free-text draft: keep verbatim in one place, never reflowed
            secs["chief_complaint"]["text"] = enc.chief_complaint
        return {"sections": secs, "legacy": True}

    def _legacy_snapshot(self, cj: dict, enc: Encounter) -> dict:
        p = self.db.get(Patient, enc.patient_id)
        pi = cj.get("patient_info") or {}
        return {"patient": {"id": p.id, "mrn": p.mrn, "name": p.name, "gender": p.gender, "age": p.age_years, "phone": p.phone},
                "encounter": {"id": enc.id, "no": enc.encounter_no, "created_at": iso(enc.created_at), "visit_type": enc.visit_type, "source": enc.source,
                              "doctor": enc.doctor.display_name if enc.doctor else "", "doctor_title": enc.doctor.title if enc.doctor else "", "license_no": ""},
                "diagnoses": [{"id": "", "module": d.get("module", ""), "name": d.get("western_diagnosis") or d.get("tcm_syndrome") or "", "status": d.get("review_status", ""), "primary": False,
                               "ai_original": None, "doctor_note": d.get("doctor_notes", "")} for d in cj.get("confirmed_diagnoses", []) if isinstance(d, dict)],
                "plan": {"legacy": cj.get("treatment_plan") or {}}, "prescriptions": [], "labs": [], "allergies": [a.get("allergen") for a in cj.get("allergies", []) if isinstance(a, dict)],
                "legacy": True, "legacy_patient_info": {k: pi.get(k) for k in ("name", "gender", "age")}}

    # -------------------------------------------------------------- follow-ups
    def followups(self) -> None:
        for r in self.q("select * from followup_plans order by created_at"):
            if self.known("followup_plans", r["id"]):
                continue
            eid, pid = self.known("encounters", r["encounter_id"]), self.known("patients", r["patient_id"])
            if not (eid and pid):
                self.warnings.append(f"followup plan {r['id']} orphaned (encounter/patient missing) – skipped")
                continue
            exists = self.db.scalar(select(FollowupPlan.id).where(FollowupPlan.encounter_id == eid))
            if exists:                                                       # legacy created up to 3 identical plans per encounter (double click) — keep the first
                self.remember("followup_plans", r["id"], "followup_plans", exists, "duplicate merged")
                continue
            enc = self.db.get(Encounter, eid)
            qs = [q.get("q", q) if isinstance(q, dict) else q for q in _j(r["questions_json"], [])]
            plan = FollowupPlan(encounter_id=eid, patient_id=pid, doctor_id=enc.doctor_id, status=FuPlanStatus.PROPOSED.value,
                                schedule_json=[{"day": int(r["days"] or 14), "kind": "symptom_check", "questions": qs}], created_by="legacy", created_at=_ts(r["created_at"]) or utcnow())
            self.db.add(plan)
            self.db.flush()
            self.remember("followup_plans", r["id"], "followup_plans", plan.id)
        for r in self.q("select * from followup_responses order by created_at"):
            if self.known("followup_responses", r["id"]):
                continue
            plan_id = self.known("followup_plans", r["followup_plan_id"])
            pid = self.known("patients", r["patient_id"])
            if not (plan_id and pid):
                self.warnings.append(f"followup response {r['id']} orphaned (legacy plan {r['followup_plan_id']!r} missing) – kept out of v3, see legacy_map")
                self.remember("followup_responses", r["id"], "skipped", "-", "orphan: plan missing")
                continue
            plan = self.db.get(FollowupPlan, plan_id)
            # The legacy model let a patient answer the same plan any number of times. In v3 one task = one answer,
            # so each legacy response becomes its own ANSWERED task (seq = order of answer) under the plan.
            seq = (self.db.scalar(select(func.max(FollowupTask.seq)).where(FollowupTask.plan_id == plan_id)) or 0) + 1
            task = FollowupTask(plan_id=plan_id, encounter_id=plan.encounter_id, patient_id=pid, doctor_id=plan.doctor_id, seq=seq,
                                due_at=_ts(r["created_at"]) or utcnow(), status=FuTaskStatus.ANSWERED.value,
                                answered_at=_ts(r["created_at"]), questions_json=(plan.schedule_json[0]["questions"] if plan.schedule_json else []))
            self.db.add(task)
            self.db.flush()
            ans = _j(r["answers_json"], {})
            lvl = {"normal": "normal", "attention": "attention", "high_risk": "high_risk"}.get(r["risk_level"], "normal")
            resp = FollowupResponse(task_id=task.id, patient_id=pid, encounter_id=plan.encounter_id, answers_json=ans if isinstance(ans, dict) else {"text": str(ans)},
                                    free_text=(ans.get("text") if isinstance(ans, dict) else "") or "", risk_level=lvl, ai_summary_json=_j(r["ai_summary_json"], {}),
                                    submit_key=f"legacy:{r['id']}", submitted_at=_ts(r["created_at"]) or utcnow(), legacy_source=f"followup_responses:{r['id']}")
            self.db.add(resp)
            self.db.flush()
            self.remember("followup_responses", r["id"], "followup_responses", resp.id)
            if lvl in ("attention", "high_risk"):
                self.db.add(FollowupAlert(response_id=resp.id, patient_id=pid, encounter_id=plan.encounter_id, doctor_id=plan.doctor_id, level=lvl,
                                          rules_json=[{"rule": ev["rule_triggered"], "kind": "high" if lvl == "high_risk" else "attention", "detail": ev["rule_triggered"]}
                                                      for ev in self.q("select * from followup_risk_events where followup_response_id=?", r["id"])],
                                          status="open", created_at=resp.submitted_at))

    # -------------------------------------------------------------- audit
    def audit(self, ndjson: Path) -> None:
        if not ndjson.exists() or self.known("audit", "ndjson"):
            return
        n = 0
        for line in ndjson.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            self.db.add(AuditEvent(ts=_ts(e.get("ts")) or utcnow(), actor_kind="staff" if str(e.get("actor", "")).startswith(("dr-", "nu-")) else "system",
                                   actor_id=str(e.get("actor", ""))[:40], actor_name=str(e.get("actor", ""))[:64], action=str(e.get("action", ""))[:48], entity_id=str(e.get("target", ""))[:40],
                                   summary="(旧系统审计) " + json.dumps(e.get("meta", {}), ensure_ascii=False)[:240], detail_json=e.get("meta", {}), legacy=True))
            n += 1
        self.remember("audit", "ndjson", "audit_events", f"{n} rows")

    def run(self, audit_file: Path | None = None) -> dict:
        for step in (self.users, self.patients, self.encounters, self.labs, self.tongue_images, self.analyses, self.diagnoses,
                     self.plans_and_prescriptions, self.records, self.followups):
            step()
            self.db.flush()
        if audit_file:
            self.audit(audit_file)
        return {"imported": dict(self.stats), "warnings": self.warnings}


def run_import(dry_run: bool = False) -> dict:
    s = get_settings()
    if not s.legacy_db_path.exists():
        return {"imported": {}, "warnings": [f"no legacy database at {s.legacy_db_path}"]}
    src = sqlite3.connect(f"file:{s.legacy_db_path}?mode=ro", uri=True)           # READ-ONLY: the legacy DB is never written
    eng = init_db(s.db_path)
    mig.migrate(eng, s.db_path, s.backups_dir)
    if not dry_run:
        mig.backup_database(s.db_path, s.backups_dir, "pre-legacy-import")
    from server.seed import seed_reference_data
    with session_scope() as db:
        seed_reference_data(db)
        res = Importer(db, src).run(audit_file=Path(s.xy_data_dir) / "audit.ndjson")
        if dry_run:
            db.rollback()
            res["dry_run"] = True
    src.close()
    return res


if __name__ == "__main__":
    out = run_import(dry_run="--dry-run" in sys.argv)
    print(json.dumps(out, ensure_ascii=False, indent=2))
