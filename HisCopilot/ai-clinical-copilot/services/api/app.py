"""FastAPI main app — AI Clinical Copilot backend.

instruct #56: /health /ready /metrics
instruct #71: API surface
instruct #50: real model calls only; no fake AI
instruct #30: AI failure → graceful degradation, never block HIS
demo.md 第六节: AI analyze → background task + polling (no long-blocking POST)
demo.md 第七节: prescription → MockHIS idempotent submit
demo.md 第四节: persistence — golden-path entities survive restart
"""
from __future__ import annotations

import os
import sys
import time
import uuid
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, status, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))  # repo root
sys.path.insert(0, str(REPO / "packages"))  # clinical_schemas, packages.*
sys.path.insert(0, str(REPO / "packages" / "clinical-schemas"))  # clinical_schemas module
sys.path.insert(0, str(REPO / "services"))  # services.*

from packages.shared.config import get_settings  # noqa: E402
from packages.shared.db import init_engine, get_session  # noqa: E402
from packages.shared.his_adapter import get_his_adapter  # noqa: E402
from packages.shared.cache import get_cache  # noqa: E402
from packages.shared.providers import AIUnavailable  # noqa: E402
from packages.shared import audit  # noqa: E402
from packages.shared.repo import (  # noqa: E402
    AIRunRepo, EncounterRepo, FeedbackRepo, FollowupPlanRepo,
    FollowupResponseRepo, FollowupRiskEventRepo, LabResultRepo,
    MedicalRecordRepo, PatientRepo, PrescriptionDraftRepo, UserRepo,
)
from packages.shared.auth import (  # noqa: E402
    hash_password, verify_password, create_access_token, create_patient_token,
    decode_token, get_current_user, require_doctor, get_patient_context,
    set_doctor_cookie, set_patient_cookie, clear_doctor_cookie,
    DOCTOR_COOKIE,
)
from packages.shared.therapy_review_repo import TherapyItemReviewRepo  # noqa: E402

# Background AI run tasks: run_id -> asyncio.Task (held to prevent GC cancel).
_RUN_TASKS: dict[str, "asyncio.Task"] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_engine()
    his = get_his_adapter()
    # demo.md 第四节: idempotent demo data init on startup.
    try:
        if get_settings().app_env == "development":
            from scripts.init_demo import init_demo_data
            r = await init_demo_data(his)
            print(f"[init] demo data: {r}", flush=True)
    except Exception as e:
        print(f"[init] demo data skipped: {e}", flush=True)
    yield


# V1.2: allow CORS from HTTPS proxy (3443) + public penetration URL
# PUBLIC_BASE_URL can be set in .env for nat-penetrated deployments
from packages.shared.config import get_settings as _get_settings_for_cors
_public_base = _get_settings_for_cors().public_base_url.strip().rstrip("/")
_cors_origins = ["http://localhost:3000", "https://localhost:3443",
                  "https://127.0.0.1:3443", "https://0.0.0.0:3443"]
if _public_base:
    _cors_origins.append(_public_base)

app = FastAPI(
    title="AI Clinical Copilot",
    version="0.1.0-mvp",
    description="私有化 HIS 外挂 AI 中西医门诊临床副驾",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- idle tracking
# Lightweight activity heartbeat for the GPU-idle-watcher daemon.
# Every "business" request bumps data/logs/last_activity as an epoch float; the
# watcher reads it to decide whether GPU1's model servers have been idle long
# enough to shut down (freeing the card for AIComic dual-GPU batch rendering).
#
# Idle definition (instruct: POST + logged-in GET all count; browser heartbeat
# polling does NOT):
#   - any POST counts
#   - GET counts only for clinical-data paths (encounters/patients/records/
#     drugs/formulas/followups/ai-runs/ai/.../diagnoses/knowledge/prescriptions)
#     — NOT /health /ready /metrics /api/auth/me /api/doctors/* /api/doctors
#     /api/therapy-programs* /objects/* /mock-his/* which are heartbeat/listing
_ACTIVITY_PATHS = (
    "/api/encounters", "/api/patients", "/api/medical-records", "/api/drugs",
    "/api/formulas", "/api/followups", "/api/ai-runs", "/api/knowledge",
    "/api/prescriptions", "/api/asr/", "/api/ocr/", "/api/alerts", "/api/audit",
)
_ACTIVITY_FILE = REPO / "data" / "logs" / "last_activity"


def _touch_activity() -> None:
    """Record business activity for the idle watcher. Cheap, never raises."""
    try:
        _ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVITY_FILE.write_text(f"{time.time():.0f}\n")
    except Exception:  # noqa: BLE001
        pass


class _ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            method = request.method.upper()
            path = request.url.path
            is_get = method == "GET"
            is_business_get = is_get and (
                path == "/api/therapy-programs" or path.startswith("/api/therapy-programs/")
                or any(path.startswith(p) for p in _ACTIVITY_PATHS)
            )
            if method == "POST" or is_business_get:
                _touch_activity()
        except Exception:  # noqa: BLE001
            pass
        return await call_next(request)


app.add_middleware(_ActivityMiddleware)


@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


@app.get("/ready")
async def ready():
    return {"ready": True, "env": get_settings().app_env}


@app.get("/metrics")
async def metrics():
    return {"uptime_s": int(time.time()), "note": "structured logs in data/logs"}


# ---------------------------------------------------------------------------
# updatev2_2 §1: Auth endpoints — login, me, logout
# ---------------------------------------------------------------------------
class LoginReq(BaseModel):
    doctor_id: str
    password: str


@app.post("/api/auth/login")
async def login(req: LoginReq, response: Response):
    """Doctor login — verify password, issue JWT in httpOnly cookie."""
    from packages.shared.db import get_sessionmaker, User
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, req.doctor_id)
        if u is None or not u.is_active:
            raise HTTPException(401, "用户名或密码错误")
        if not u.password_hash:
            raise HTTPException(401, "用户未设置密码，请联系管理员")
        if not verify_password(req.password, u.password_hash):
            raise HTTPException(401, "用户名或密码错误")
    token = create_access_token(u.id, u.role, u.name)
    set_doctor_cookie(response, token)
    await audit.audit_log(actor=u.id, action="auth.login", target=u.id)
    return {"access_token": token, "user": {"id": u.id, "name": u.name, "role": u.role}}


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(require_doctor)):
    """Return the currently logged-in user."""
    return user


@app.post("/api/auth/logout")
async def logout(response: Response):
    clear_doctor_cookie(response)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Doctor profiles + therapy programs (instruct_1.md §3/§4)
# ---------------------------------------------------------------------------
class DoctorProfileReq(BaseModel):
    doctor_id: str
    dept: str = ""
    specialties: list = []
    syndrome_preference: str = ""
    formula_preference: str = ""
    herb_preference: str = ""
    non_drug_preference: str = ""
    analysis_focus: str = ""
    experience_notes: list = []


@app.get("/api/doctors")
async def list_doctors(user: dict = Depends(require_doctor)):
    from packages.shared.repo import UserRepo
    from sqlalchemy import select
    from packages.shared.db import get_sessionmaker, User
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(select(User).filter_by(role="DOCTOR").order_by(User.id))
        return [{"id": u.id, "name": u.name, "dept": u.role} for u in res.scalars()]


@app.get("/api/doctors/{did}/profile")
async def get_doctor_profile(did: str, user: dict = Depends(require_doctor)):
    from packages.shared.therapy_repo import DoctorProfileRepo
    p = await DoctorProfileRepo.get(did)
    if not p:
        # return a default empty profile so the frontend can still function
        return {"doctor_id": did, "dept": "", "specialties": [],
                "syndrome_preference": "", "formula_preference": "",
                "herb_preference": "", "non_drug_preference": "",
                "analysis_focus": "", "experience_notes": []}
    return p


@app.put("/api/doctors/{did}/profile")
@app.patch("/api/doctors/{did}/profile")
async def upsert_doctor_profile(did: str, req: DoctorProfileReq, user: dict = Depends(require_doctor)):
    from packages.shared.therapy_repo import DoctorProfileRepo
    rec = await DoctorProfileRepo.upsert(
        doctor_id=did, dept=req.dept, specialties=req.specialties,
        syndrome_preference=req.syndrome_preference,
        formula_preference=req.formula_preference,
        herb_preference=req.herb_preference,
        non_drug_preference=req.non_drug_preference,
        analysis_focus=req.analysis_focus,
        experience_notes=req.experience_notes)
    await audit.audit_log(actor=did, action="doctor.profile.update", target=did)
    return rec


@app.get("/api/therapy-programs")
async def list_therapy_programs(category: str = "", user: dict = Depends(require_doctor)):
    from packages.shared.therapy_repo import TherapyProgramRepo
    return await TherapyProgramRepo.list_all(category=category)


@app.get("/api/therapy-programs/categories")
async def therapy_categories(user: dict = Depends(require_doctor)):
    from packages.shared.therapy_repo import TherapyProgramRepo
    progs = await TherapyProgramRepo.list_all()
    cats = sorted({p["category"] for p in progs})
    return {"categories": cats}


# ---------------------------------------------------------------------------
# Tongue image upload (instruct #14, instruct_1.md §5 workstation completeness)
# ---------------------------------------------------------------------------
@app.post("/api/encounters/{eid}/tongue-image")
async def upload_tongue_image(eid: str, file: UploadFile = File(...),
                              user: dict = Depends(require_doctor)):
    """Upload a tongue image for the encounter. Stored to data/objects/.

    V1.2 §1: generate a compressed copy (max 1024px, JPEG q85) for model
    inference, keep original for doctor viewing. Persist TongueImage row.

    The frontend sends the returned key as tongue_image_key in the analyze
    request. The orchestrator's _tongue module loads the compressed image
    from data/objects/{compressed_key} and runs a real VLM call.
    """
    import os
    from packages.shared.config import REPO_ROOT
    from packages.shared.v12_repos import TongueImageRepo
    allowed = {"image/png", "image/jpeg", "image/webp", "image/jpg"}
    ct = (file.content_type or "").lower()
    if ct not in allowed:
        raise HTTPException(400, f"unsupported image type: {ct} (use png/jpeg/webp)")
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    orig_key = f"tongue_orig_{eid}_{uuid.uuid4().hex[:8]}{ext}"
    comp_key = f"tongue_comp_{uuid.uuid4().hex[:8]}.jpg"
    obj_dir = REPO_ROOT / "data" / "objects"
    obj_dir.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    (obj_dir / orig_key).write_bytes(data)
    # Generate compressed copy using PIL (max 1024px long side, JPEG q85)
    try:
        import io as _io
        from PIL import Image
        img = Image.open(_io.BytesIO(data))
        # Convert to RGB if needed (PNG with alpha → JPEG)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        comp_data = buf.getvalue()
        (obj_dir / comp_key).write_bytes(comp_data)
        comp_ok = True
    except Exception as e:
        # Fallback: copy original as compressed (model still works with original)
        comp_key = orig_key
        comp_ok = False
        print(f"[tongue] compression skipped: {e}", flush=True)
    # Determine patient_id for TongueImage row
    enc = await EncounterRepo.get(eid)
    patient_id = enc.get("patient_id", "") if enc else ""
    if patient_id:
        await TongueImageRepo.create(
            encounter_id=eid, patient_id=patient_id,
            object_key=orig_key, compressed_key=comp_key)
    await audit.audit_log(actor=user["id"], action="tongue_image.upload",
                          target=eid, meta={"key": orig_key,
                                            "compressed_key": comp_key,
                                            "bytes": len(data),
                                            "compression": comp_ok})
    return {"key": orig_key, "compressed_key": comp_key,
            "size": len(data), "content_type": ct,
            "url": f"/objects/{orig_key}",
            "preview_url": f"/objects/{comp_key}",
            "compression": comp_ok}


@app.post("/api/tongue-images")
async def upload_tongue_image_standalone(file: UploadFile = File(...)):
    """V1.2 §1: standalone tongue upload (no encounter_id needed yet).

    Used by the intake flow before the encounter is created. The returned
    key (compressed_key) is sent as tongue_image_key when creating the
    encounter. Original is preserved for later viewing.
    """
    from packages.shared.config import REPO_ROOT
    allowed = {"image/png", "image/jpeg", "image/webp", "image/jpg"}
    ct = (file.content_type or "").lower()
    if ct not in allowed:
        raise HTTPException(400, f"unsupported image type: {ct} (use png/jpeg/webp)")
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    orig_key = f"tongue_orig_{uuid.uuid4().hex[:8]}{ext}"
    comp_key = f"tongue_comp_{uuid.uuid4().hex[:8]}.jpg"
    obj_dir = REPO_ROOT / "data" / "objects"
    obj_dir.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    (obj_dir / orig_key).write_bytes(data)
    try:
        import io as _io
        from PIL import Image
        img = Image.open(_io.BytesIO(data))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        comp_data = buf.getvalue()
        (obj_dir / comp_key).write_bytes(comp_data)
        comp_ok = True
    except Exception as e:
        comp_key = orig_key
        comp_ok = False
        print(f"[tongue] compression skipped: {e}", flush=True)
    await audit.audit_log(actor="patient", action="tongue_image.upload",
                          target="standalone", meta={"key": orig_key,
                                      "compressed_key": comp_key,
                                      "bytes": len(data)})
    # The orchestrator expects compressed key in tongue_image_key field.
    # But it derives compressed from orig key convention. So we return
    # the compressed_key for the frontend to send as tongue_image_key,
    # AND the original key for doctor preview.
    # The orchestrator will try comp key first, fall back to orig.
    return {"key": comp_key, "original_key": orig_key,
            "compressed_key": comp_key,
            "size": len(data), "content_type": ct,
            "url": f"/objects/{comp_key}",
            "original_url": f"/objects/{orig_key}",
            "compression": comp_ok}


@app.get("/objects/{key:path}")
async def get_object(key: str, user: dict = Depends(require_doctor)):
    """Serve a stored object (tongue image etc.) to a logged-in staff member.

    HOTFIX 2026-10-29: the previous version joined `key` onto the objects dir
    without resolving it, so `/objects//etc/passwd` returned arbitrary files
    (including .env). Now: flat names only, resolved path must stay inside the
    objects directory, and a doctor/staff session is required."""
    from packages.shared.config import REPO_ROOT
    from fastapi.responses import FileResponse
    import mimetypes
    base = (REPO_ROOT / "data" / "objects").resolve()
    if not key or "/" in key or "\\" in key or key.startswith("."):
        raise HTTPException(404, "object not found")
    p = (base / key).resolve()
    if p.parent != base or not p.is_file():
        raise HTTPException(404, "object not found")
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return FileResponse(str(p), media_type=mime,
                        headers={"X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------------------
# Mock HIS routes (instruct #29)
# ---------------------------------------------------------------------------
@app.get("/mock-his/patients")
async def his_patients(user: dict = Depends(require_doctor)):
    his = get_his_adapter()
    mem = list(his._patients.values()) if hasattr(his, "_patients") else []
    if mem:
        return mem
    # DB fallback
    return await PatientRepo.list_all()


@app.get("/mock-his/patients/{pid}")
async def his_patient(pid: str, user: dict = Depends(require_doctor)):
    return await get_his_adapter().get_patient(pid)


@app.get("/mock-his/encounters/{eid}")
async def his_encounter(eid: str, user: dict = Depends(require_doctor)):
    return await get_his_adapter().get_encounter(eid)


@app.get("/mock-his/drugs")
async def his_drugs(q: str = "", user: dict = Depends(require_doctor)):
    return await get_his_adapter().get_drug_dictionary(q)


@app.get("/mock-his/formulas")
async def his_formulas(q: str = "", user: dict = Depends(require_doctor)):
    return await get_his_adapter().get_formula_dictionary(q)


@app.post("/mock-his/medical-records")
async def his_submit_record(req: dict, user: dict = Depends(require_doctor)):
    return await get_his_adapter().submit_medical_record(
        encounter_id=req["encounter_id"], doctor_id=req["doctor_id"],
        content=req.get("content", {}), signature_id=req.get("signature_id", ""),
    )


@app.get("/api/medical-records")
async def list_medical_records(user: dict = Depends(require_doctor)):
    """List all medical records from DB (for 病历管理 page)."""
    from packages.shared.db import get_sessionmaker, MedicalRecord
    from sqlalchemy import select
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(
            select(MedicalRecord).order_by(MedicalRecord.created_at.desc()).limit(100))
        rows = res.scalars().all()
    return [{"id": r.id, "encounter_id": r.encounter_id,
             "patient_id": r.patient_id,
             "content": r.content_json,
             "raw_text": r.raw_text,
             "created_at": str(r.created_at)} for r in rows]


@app.post("/mock-his/prescriptions")
async def his_submit_rx(req: dict, user: dict = Depends(require_doctor)):
    return await get_his_adapter().create_prescription(
        encounter_id=req["encounter_id"], doctor_id=req["doctor_id"],
        prescription=req.get("prescription", {}), signature_id=req.get("signature_id", ""),
    )


# ---------------------------------------------------------------------------
# AI endpoints (instruct #50: real calls; AIUnavailable → 503, not fake)
# demo.md 第六节: background task + polling, NOT blocking 2-5min POST
# ---------------------------------------------------------------------------
class AnalyzeReq(BaseModel):
    encounter_id: str
    patient_id: str
    patient: dict = {}
    medical_history: dict = {}
    labs: list[dict] = []
    medications: list[dict] = []
    allergies: list[dict] = []
    tongue_features: dict = {}
    tongue_image_key: str = ""
    raw_text: str = ""
    doctor_id: str = "dr-001"  # instruct_1.md §3 multi-doctor personalization


async def _progress_cb(run_id: str, module: str, status: str, payload: dict):
    """Persist per-module status to the DB so polling sees real progress.

    demo.md #6: 模块状态必须由真实模型调用及实际处理结果决定. We do NOT emit
    fake progress — this callback is only invoked from inside the real runner
    wrappers in orchestrator.run()."""
    try:
        if status == "RUNNING":
            await AIRunRepo.set_status(run_id, "RUNNING")
        elif status == "COMPLETE":
            # store the module output; 'run' module carries the terminal status
            if module == "run":
                await AIRunRepo.set_status(
                    run_id, payload.get("status", "SUCCEEDED"),
                    latency_ms=payload.get("latency_ms", 0))
                # V1.2 §7: persist module timings
                if payload.get("module_timings"):
                    await AIRunRepo.update_module_timings(
                        run_id, payload["module_timings"])
            else:
                await AIRunRepo.append_output(
                    run_id=run_id, output_type=module,
                    structured_json=payload,
                    display_text=str(payload.get("summary", ""))[:500])
        elif status == "SKIPPED":
            await AIRunRepo.append_output(
                run_id=run_id, output_type=module,
                structured_json={"skipped": True, **payload},
                display_text=payload.get("note", "module skipped"))
        elif status == "FAILED":
            await AIRunRepo.append_output(
                run_id=run_id, output_type=module,
                structured_json={"failed": True, **payload},
                display_text=payload.get("reason", "module unavailable"))
    except Exception as e:
        print(f"[progress] persist failed for {module}: {e}", flush=True)


@app.post("/api/encounters/{eid}/ai/analyze")
async def analyze(eid: str, req: AnalyzeReq, user: dict = Depends(require_doctor)):
    """Start an AI run in the background; return 202 + ai_run_id immediately.

    demo.md 第六节: 不要让 HTTP 请求长时间阻塞. The frontend polls
    GET /api/ai-runs/{rid} for per-module status driven by real model calls."""

    # RAG evidence retrieval first (instruct #43) — this is fast (<2s with
    # embedding on-demand) and happens before the background task starts.
    evidence: list[dict] = []
    try:
        from services.knowledge.rag import RAGPipeline
        rag = RAGPipeline()
        query = (req.raw_text or req.medical_history.get("chief_complaint", ""))[:512]
        if query:
            evidence = await rag.retrieve(query, top_k=6)
    except Exception:
        # instruct #30: AI sub-component failure must not block
        evidence = []

    ctx = await _build_ctx(eid, req, evidence)
    run_id = uuid.uuid4().hex[:24]
    input_hash = _hash_ctx(ctx)
    await AIRunRepo.create(run_id=run_id, patient_id=req.patient_id,
                           encounter_id=eid, model_name="orchestrator",
                           input_hash=input_hash, actor=user["id"])

    async def _bg():
        try:
            wf = _new_workflow()
            await wf.run(ctx, actor=user["id"], run_id=run_id,
                        on_progress=_progress_cb)
        except Exception as e:
            # instruct #30: never raise out of the background task — the 202
            # is already sent. Mark the run FAILED so polling sees it.
            await AIRunRepo.set_status(run_id, "FAILED")
            await audit.audit_log(actor="system", action="ai.run.failed",
                                   target=eid, meta={"run_id": run_id, "err": str(e)})
        finally:
            _RUN_TASKS.pop(run_id, None)

    _RUN_TASKS[run_id] = asyncio.create_task(_bg())
    return JSONResponse(
        {"ai_run_id": run_id, "status": "PENDING",
         "poll_url": f"/api/ai-runs/{run_id}"},
        status_code=status.HTTP_202_ACCEPTED)


@app.get("/api/ai-runs/{rid}")
async def get_ai_run(rid: str, user: dict = Depends(require_doctor)):
    """Poll an AI run's status + completed module outputs (demo.md #6)."""
    rec = await AIRunRepo.get(rid)
    if not rec:
        # fall back to in-process cache (older runs)
        cache = get_cache()
        cached = await cache.get(f"ai:run:{rid}")
        if not cached:
            raise HTTPException(404, "ai run not found")
        return cached
    # reshape outputs into the panel the frontend expects
    outputs = {o["output_type"]: o["structured_json"] for o in rec.get("outputs", [])}
    return {
        "run_id": rec["run_id"], "status": rec["status"],
        "latency_ms": rec.get("latency_ms", 0),
        "module_timings": rec.get("module_timings", {}),
        "panel_status": _derive_panel(outputs, rec["status"]),
        "western": outputs.get("western", {}),
        "tcm": outputs.get("tcm", {}),
        "tongue": outputs.get("tongue", {}),
        "knowledge": outputs.get("knowledge", {}),
        "synthesis": outputs.get("synthesis", {}),
        "prescription": outputs.get("prescription", {}),
        "therapy": outputs.get("therapy", {}),
        "evidence": (outputs.get("knowledge", {}) or {}).get("evidence", []),
    }


def _derive_panel(outputs: dict, run_status: str) -> dict:
    panel = {}
    for mod in ("western", "tcm", "tongue", "knowledge", "synthesis",
                "prescription", "therapy"):
        o = outputs.get(mod)
        if not o:
            panel[mod] = "pending"
        elif isinstance(o, dict) and o.get("failed"):
            panel[mod] = "failed"
        elif isinstance(o, dict) and o.get("skipped"):
            panel[mod] = "skipped"
        else:
            panel[mod] = "complete"
    if run_status == "PENDING":
        for k in panel:
            if panel[k] == "pending":
                panel[k] = "pending"
    return panel


# ---------------------------------------------------------------------------
# updatev2_2 §3: load latest persisted AI run (don't re-run on patient open)
# ---------------------------------------------------------------------------
@app.get("/api/encounters/{eid}/ai/latest")
async def get_latest_ai_run(eid: str, user: dict = Depends(require_doctor)):
    """Return the most recent AI run for an encounter, or {run_id: None}.

    Doctor opens patient → immediately sees existing analysis; no re-run.
    """
    runs = await AIRunRepo.list_for_encounter(eid)
    if not runs:
        return {"run_id": None, "status": "none"}
    latest = runs[0]  # list_for_encounter orders by created_at desc
    # Return the full run via the existing get_ai_run logic (reuse)
    return await get_ai_run(latest["run_id"], user)


class FeedbackReq(BaseModel):
    ai_output_id: str
    doctor_id: str
    action: str  # accept/modify/reject
    original_content: str = ""
    edited_content: str = ""
    reason: dict | str = ""  # V1.2 §2: can be dict with encounter_id/patient_id


# V1.2 §2: feedback route moved below (creates DiagnosisRecord on accept/modify)


# ---------------------------------------------------------------------------
# Knowledge endpoints (instruct #71)
# ---------------------------------------------------------------------------
@app.get("/api/knowledge/search")
async def knowledge_search(q: str = "", top_k: int = 6, user: dict = Depends(require_doctor)):
    from services.knowledge.rag import RAGPipeline
    try:
        return {"results": await RAGPipeline().retrieve(q, top_k=top_k)}
    except AIUnavailable as e:
        raise HTTPException(503, f"RAG unavailable: {e}")


class DocUpload(BaseModel):
    title: str = ""
    text: str = ""
    source_type: str = "textbook"
    domain: str = "western"
    trust_tier: str = "E"


@app.post("/api/knowledge/documents")
async def upload_doc(req: DocUpload, user: dict = Depends(require_doctor)):
    from services.knowledge.rag import KnowledgeStore, chunk_text, metadata_for
    from packages.shared.providers import EmbeddingProvider, AIUnavailable
    chunks = chunk_text(req.text)
    if not chunks:
        raise HTTPException(400, "empty text")
    try:
        emb = EmbeddingProvider("embedding")
        vecs = await emb.embed(chunks)
    except AIUnavailable as e:
        raise HTTPException(503, f"embedding unavailable: {e}")
    store = KnowledgeStore()
    metas = [metadata_for(source_type=req.source_type, domain=req.domain,
                         title=req.title, trust_tier=req.trust_tier) for _ in chunks]
    chunk_dicts = [{"document_id": "", "source_id": "", "text": c,
                     "metadata": m, "chunk_index": i}
                    for i, (c, m) in enumerate(zip(chunks, metas))]
    ids = await store.add_chunks(chunk_dicts, vecs)
    await audit.audit_log(actor=user["id"], action="knowledge.upload", target=req.title,
                          meta={"chunks": len(ids)})
    return {"chunks": len(ids), "ids": ids}


# ---------------------------------------------------------------------------
# HIS Adapter passthrough for business APIs (instruct #65: only via adapter)
# ---------------------------------------------------------------------------
@app.get("/api/formulas")
async def formulas(q: str = "", user: dict = Depends(require_doctor)):
    return await get_his_adapter().get_formula_dictionary(q)


@app.post("/api/formulas")
async def create_formula(req: dict, user: dict = Depends(require_doctor)):
    his = get_his_adapter()
    if hasattr(his, "_formulas"):
        his._formulas.append(req)
    await audit.audit_log(actor=user["id"], action="formula.create", target=req.get("name",""))
    return req


@app.get("/api/drugs")
async def drugs(q: str = "", category: str = "", limit: int = 500, user: dict = Depends(require_doctor)):
    """V1.2 §3: DB-first drug search. MockHIS fallback for compat."""
    from packages.shared.drug_repo import DrugRepo
    results = await DrugRepo.search(q=q, category=category, limit=limit)
    if not results:
        return await get_his_adapter().get_drug_dictionary(q)
    return results


# ---------------------------------------------------------------------------
# Encounters / Patients (DB-backed, demo.md 第四节)
# V1.0: DB-first reads, runtime creation endpoints
# ---------------------------------------------------------------------------
@app.get("/api/encounters")
async def list_encounters(status: str = "", user: dict = Depends(require_doctor)):
    encs = await EncounterRepo.list_all()
    # V1.0: also include seed encounters from MockHIS memory that aren't in DB
    his = get_his_adapter()
    if hasattr(his, "_encounters"):
        db_ids = {e["id"] for e in encs}
        for eid, e in his._encounters.items():
            if eid not in db_ids:
                encs.append(e)
    if status:
        encs = [e for e in encs if e.get("status") == status]
    return encs


@app.get("/api/encounters/{eid}")
async def get_encounter(eid: str, user: dict = Depends(require_doctor)):
    # V1.0: DB-first, enrich with MockHIS (seed compat) + labs + medical record
    e = await EncounterRepo.get(eid)
    if not e:
        e = await get_his_adapter().get_encounter(eid)
    if e:
        labs = await LabResultRepo.list_for_encounter(eid)
        if labs:
            e.setdefault("labs", labs)
        mr = await MedicalRecordRepo.get_by_encounter(eid)
        if mr:
            e["medical_record"] = mr
        return e
    return {}


@app.get("/api/patients")
async def list_patients(user: dict = Depends(require_doctor)):
    # V1.0: DB-first (includes runtime-created + seed patients)
    db_patients = await PatientRepo.list_all()
    # Also include MockHIS in-memory patients not yet in DB (seed compat)
    his = get_his_adapter()
    if hasattr(his, "_patients"):
        db_ids = {p["id"] for p in db_patients}
        for pid, p in his._patients.items():
            if pid not in db_ids:
                db_patients.append(p)
    return db_patients


@app.get("/api/patients/{pid}")
async def get_patient(pid: str, user: dict = Depends(require_doctor)):
    rec = await PatientRepo.get(pid)
    if rec:
        return rec
    return await get_his_adapter().get_patient(pid)


# V1.0: Create patient at runtime (no init_demo required)
class CreatePatientReq(BaseModel):
    name: str
    gender: str = ""
    age: int = 0
    phone: str = ""
    id_card: str = ""
    address: str = ""
    allergies: list[dict] = []
    insurance: str = ""


@app.post("/api/patients")
async def create_patient(req: CreatePatientReq):
    pid = f"p-{uuid.uuid4().hex[:8]}"
    extra = {"allergies": req.allergies, "id_card": req.id_card,
             "address": req.address, "insurance": req.insurance}
    rec = await PatientRepo.upsert(pid, name=req.name, gender=req.gender,
                                    age=req.age, phone=req.phone,
                                    is_demo=False, extra=extra)
    await audit.audit_log(actor="patient", action="patient.create", target=pid)
    return rec


@app.get("/api/patients/search/phone/{phone}")
async def search_patient_by_phone(phone: str, user: dict = Depends(require_doctor)):
    """V1.0: lookup patient by phone for intake pre-fill."""
    rec = await PatientRepo.search_by_phone(phone)
    return rec or {}


# V1.0: Create encounter at runtime (patient self-service or doctor direct)
class CreateEncounterReq(BaseModel):
    patient_id: str
    doctor_id: str = ""
    chief_complaint: str = ""
    history: dict = {}
    raw_text: str = ""
    medications: list[dict] = []
    allergies: list[dict] = []
    labs: list[dict] = []
    source: str = "doctor"  # "patient" or "doctor"
    case_type: str = ""
    tongue_image_key: str = ""


@app.post("/api/encounters")
async def create_encounter(req: CreateEncounterReq, response: Response,
                           user: dict | None = Depends(get_current_user)):
    eid = f"enc-{uuid.uuid4().hex[:8]}"
    clinical_data = {"history": req.history, "raw_text": req.raw_text,
                     "medications": req.medications, "allergies": req.allergies,
                     "labs": req.labs, "source": req.source,
                     "case_type": req.case_type, "tongue_image_key": req.tongue_image_key}
    # §1: if logged-in doctor created encounter, use their id
    effective_doctor = req.doctor_id or (user["id"] if user else "")
    rec = await EncounterRepo.upsert(eid, patient_id=req.patient_id,
                                      doctor_id=effective_doctor,
                                      chief_complaint=req.chief_complaint,
                                      encounter_no=eid, status="PENDING",
                                      is_demo=False, extra=clinical_data)
    # persist labs to LabResult table if provided
    if req.labs:
        await LabResultRepo.upsert_for_encounter(
            encounter_id=eid, patient_id=req.patient_id, labs=req.labs)
    # auto-generate medical record draft (template, NOT AI)
    await _generate_medical_record_draft(eid, req.patient_id, rec)
    # §1: audit with real actor (doctor id from token, or "patient")
    actor_id = user["id"] if user else "patient"
    await audit.audit_log(actor=actor_id,
                          action="encounter.create", target=eid)
    # §2: auto-trigger AI pre-analysis for patient-submitted encounters
    patient_token = None
    if req.source == "patient":
        patient_token = create_patient_token(eid, req.patient_id)
        set_patient_cookie(response, patient_token)
        # fire background AI analysis (idempotent — _auto_analyze checks existing runs)
        asyncio.create_task(_auto_analyze(eid, req, actor_id))
    rec["patient_token"] = patient_token
    return rec


class PatchEncounterReq(BaseModel):
    chief_complaint: str = ""
    history: dict = {}
    raw_text: str = ""
    medications: list[dict] = []
    allergies: list[dict] = []
    labs: list[dict] = []
    status: str = ""
    doctor_id: str = ""
    tongue_image_key: str = ""


@app.patch("/api/encounters/{eid}")
async def update_encounter(eid: str, req: PatchEncounterReq, user: dict = Depends(require_doctor)):
    data = {k: v for k, v in req.model_dump().items() if v}
    rec = await EncounterRepo.update_clinical_data(eid, data)
    if not rec:
        raise HTTPException(404, f"encounter {eid} not found")
    # persist new labs if provided
    if req.labs:
        existing = await EncounterRepo.get(eid)
        await LabResultRepo.upsert_for_encounter(
            encounter_id=eid, patient_id=existing["patient_id"], labs=req.labs)
    await audit.audit_log(actor=user["id"], action="encounter.update", target=eid)
    return rec


async def _generate_medical_record_draft(eid: str, pid: str, enc: dict):
    """V1.0: template-based medical record draft from intake data.
    NOT an AI call — instant, doctor edits this draft."""
    chief = enc.get("chief_complaint", "")
    raw_text = enc.get("raw_text", "")
    history = enc.get("history", {})
    symptoms = history.get("symptoms", [])
    comorbidities = history.get("comorbidities", [])
    allergies = enc.get("allergies", [])
    meds = enc.get("medications", [])
    labs = enc.get("labs", [])

    draft = f"""主诉：{chief}

现病史：
{raw_text or '（待补充）'}

症状：{"、".join(symptoms) if symptoms else "—"}
既往史/慢病：{"、".join(comorbidities) if comorbidities else "—"}
过敏史：{"、".join(a.get("allergen", "") for a in allergies) if allergies else "无"}
当前用药：{"；".join(f"{m.get('name','')} {m.get('dose','')} {m.get('freq','')}" for m in meds) if meds else "无"}
检验结果：{"；".join(f"{l.get('item','')}={l.get('value','')}{l.get('unit','')}" for l in labs) if labs else "无"}

查体：（待医生补充）

初步诊断：（待医生补充/AI辅助）

处理方案：（待医生补充/AI辅助）
"""
    content = {"text": draft, "source": "auto_draft", "encounter_id": eid}
    await MedicalRecordRepo.upsert_for_encounter(
        encounter_id=eid, patient_id=pid, content=content,
        raw_text=draft, doctor_id="")


# ---------------------------------------------------------------------------
# updatev2_2 §2: Auto-trigger AI pre-analysis on patient encounter creation
# ---------------------------------------------------------------------------
async def _auto_analyze(eid: str, req: CreateEncounterReq, actor_id: str):
    """Background task: fire AI analysis for patient-submitted encounters.

    Idempotent: if a non-FAILED run already exists for this encounter, skip.
    Patient data (encounter) is already persisted before this runs — model
    failure never loses patient data.
    """
    try:
        existing = await AIRunRepo.list_for_encounter(eid)
        if existing and any(r["status"] not in ("FAILED",) for r in existing):
            # already analyzed or analyzing — skip (no duplicate)
            return
        # Build an AnalyzeReq-compatible object from the encounter data
        analyze_req = AnalyzeReq(
            encounter_id=eid, patient_id=req.patient_id,
            patient={}, medical_history=req.history,
            labs=req.labs, medications=req.medications,
            allergies=req.allergies, tongue_features={},
            tongue_image_key=req.tongue_image_key,
            raw_text=req.raw_text, doctor_id=req.doctor_id or "system")
        evidence: list[dict] = []
        try:
            from services.knowledge.rag import RAGPipeline
            rag = RAGPipeline()
            query = (req.raw_text or req.chief_complaint or "")[:512]
            if query:
                evidence = await rag.retrieve(query, top_k=6)
        except Exception:
            evidence = []
        ctx = await _build_ctx(eid, analyze_req, evidence)
        run_id = uuid.uuid4().hex[:24]
        input_hash = _hash_ctx(ctx)
        await AIRunRepo.create(run_id=run_id, patient_id=req.patient_id,
                               encounter_id=eid, model_name="orchestrator",
                               input_hash=input_hash, actor="system")
        await audit.audit_log(actor="system", action="ai.run.auto_triggered",
                              target=eid, meta={"run_id": run_id})

        async def _bg():
            try:
                wf = _new_workflow()
                await wf.run(ctx, actor="system", run_id=run_id,
                            on_progress=_progress_cb)
            except Exception as e:
                await AIRunRepo.set_status(run_id, "FAILED")
                await audit.audit_log(actor="system", action="ai.run.failed",
                                       target=eid, meta={"run_id": run_id, "err": str(e)})
            finally:
                _RUN_TASKS.pop(run_id, None)
        _RUN_TASKS[run_id] = asyncio.create_task(_bg())
    except Exception as e:
        print(f"[auto_analyze] failed for {eid}: {e}", flush=True)


# V1.0: ASR proxy endpoint (graceful 503 if model unavailable)
@app.post("/api/asr/transcribe")
async def asr_transcribe(file: UploadFile = File(...)):
    """updatev2_1 §2: ASR proxy with full diagnostics — request_id passthrough,
    structured error classification, audio metadata."""
    import httpx, logging, traceback
    logger = logging.getLogger("asr_proxy")
    try:
        audio = await file.read()
    except Exception:
        raise HTTPException(400, {"detail": "无法读取上传的音频文件",
                                  "category": "upload_failed"})
    if not audio:
        raise HTTPException(400, {"detail": "音频内容为空",
                                  "category": "upload_failed"})
    filename = file.filename or "audio.wav"
    audio_size = len(audio)
    logger.info(f"asr_proxy received filename={filename} size={audio_size}")
    try:
        async with httpx.AsyncClient(timeout=240) as client:
            r = await client.post(
                "http://127.0.0.1:7872/v1/audio/transcriptions",
                files={"file": (filename, audio)},
                data={"model": "Qwen3-ASR-1.7B", "filename": filename},
            )
        if r.status_code != 200:
            # Parse upstream error for request_id + category
            try:
                err_data = r.json()
            except Exception:
                err_data = {"detail": r.text[:200]}
            detail = err_data.get("detail", str(err_data)[:200])
            rid = err_data.get("request_id", "")
            cat = err_data.get("category", "upstream_error")
            logger.error(f"asr_proxy upstream={r.status_code} rid={rid} cat={cat} detail={detail}")
            raise HTTPException(503, {"detail": detail, "request_id": rid,
                                      "category": cat})
        result = r.json()
        # Enrich with proxy-side metadata
        result.setdefault("audio_meta", {})["proxy_size"] = audio_size
        result.setdefault("audio_meta", {})["filename"] = filename
        return result
    except httpx.ConnectError:
        logger.error("asr server not running on 7872")
        raise HTTPException(503, {"detail": "ASR 服务未启动 (port 7872)，请手动输入",
                                  "category": "model_unavailable"})
    except httpx.TimeoutException:
        logger.error("asr proxy timeout (120s)")
        raise HTTPException(503, {"detail": "ASR 推理超时，请缩短音频或手动输入",
                                  "category": "timeout"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"asr_proxy unexpected: {traceback.format_exc()}")
        raise HTTPException(503, {"detail": str(e), "category": "unknown"})


# V1.0: OCR proxy endpoint (PaddleOCR-VL or clinical_main VLM fallback)
# updatev2_1 §2: request_id + diagnostics + error classification
@app.post("/api/ocr/extract")
async def ocr_extract(file: UploadFile = File(...)):
    import httpx, base64, logging, uuid, time, traceback
    logger = logging.getLogger("ocr_proxy")
    request_id = f"ocr-{uuid.uuid4().hex[:12]}"
    t_start = time.time()
    stages: dict = {}

    try:
        image = await file.read()
    except Exception:
        logger.error(f"[{request_id}] upload read failed")
        raise HTTPException(400, {"detail": "无法读取上传的图片文件",
                                  "request_id": request_id, "category": "upload_failed"})
    if not image:
        logger.error(f"[{request_id}] empty image")
        raise HTTPException(400, {"detail": "图片内容为空",
                                  "request_id": request_id, "category": "upload_failed"})
    img_size = len(image)
    filename = file.filename or "image.jpg"
    mime = file.content_type or "image/jpeg"
    # Probe image dimensions (best-effort, never raises)
    img_meta = {"size": img_size, "filename": filename, "mime": mime}
    try:
        from PIL import Image as _PILImage
        import io as _io
        with _PILImage.open(_io.BytesIO(image)) as im:
            img_meta["width"] = im.size[0]
            img_meta["height"] = im.size[1]
    except Exception:
        pass
    logger.info(f"[{request_id}] received filename={filename} size={img_size} "
                f"dims={img_meta.get('width')}x{img_meta.get('height')}")

    # Validate image is actually decodable. If PIL can't open it, neither can
    # the VLM downstream — fail early with a clear category instead of sending
    # garbage to the model and getting an opaque 503.
    if "width" not in img_meta:
        logger.error(f"[{request_id}] image validation failed: not a valid image")
        raise HTTPException(400, {"detail": "无法识别图片格式，请上传 JPG/PNG/WebP 图片",
                                  "request_id": request_id,
                                  "category": "upload_failed",
                                  "image_meta": img_meta})

    img_b64 = base64.b64encode(image).decode("utf-8")
    data_url = f"data:{mime};base64,{img_b64}"
    ocr_prompt = "请识别并提取图片中的所有文字内容，包括检验项目名称、数值、单位、参考范围和异常标志。按原始格式输出。"
    errors: list[str] = []

    # Try PaddleOCR-VL server first (port 7875)
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "http://127.0.0.1:7875/v1/chat/completions",
                json={
                    "model": "PaddleOCR-VL-1.6",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": ocr_prompt},
                        ],
                    }],
                    "max_tokens": 2000,
                    "temperature": 0.1,
                },
            )
            stages["paddleocr_ms"] = (time.time() - t0) * 1000
            if r.status_code == 200:
                try:
                    data = r.json()
                    text = data["choices"][0]["message"]["content"]
                    stages["total_ms"] = (time.time() - t_start) * 1000
                    logger.info(f"[{request_id}] success source=paddleocr-vl "
                                f"text_len={len(text)} latency_ms={stages['total_ms']:.0f}")
                    return {"text": text, "source": "paddleocr-vl",
                            "request_id": request_id,
                            "latency_ms": round(stages["total_ms"]),
                            "image_meta": img_meta}
                except (KeyError, IndexError, ValueError) as ke:
                    errors.append(f"paddleocr-vl 响应异常: {ke}")
            else:
                errors.append(f"paddleocr-vl HTTP {r.status_code}: {r.text[:120]}")
    except httpx.TimeoutException:
        stages["paddleocr_ms"] = (time.time() - t0) * 1000
        errors.append("paddleocr-vl 超时（图片可能过大，建议压缩到 512px 以内）")
    except httpx.ConnectError:
        errors.append("paddleocr-vl 服务未启动 (port 7875)")
    except Exception as e:
        errors.append(f"paddleocr-vl 异常: {e}")
    logger.info(f"[{request_id}] paddleocr-vl failed: {errors[-1]}; falling back to clinical_main VLM")

    # Fallback: clinical_main VLM (port 7870, always running)
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "http://127.0.0.1:7870/v1/chat/completions",
                json={
                    "model": "clinical_main",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": ocr_prompt},
                        ],
                    }],
                    "max_tokens": 2000,
                    "temperature": 0.1,
                },
            )
            stages["clinical_main_ms"] = (time.time() - t0) * 1000
            if r.status_code == 200:
                try:
                    data = r.json()
                    text = data["choices"][0]["message"]["content"]
                    stages["total_ms"] = (time.time() - t_start) * 1000
                    logger.info(f"[{request_id}] success source=clinical_main_vlm "
                                f"text_len={len(text)} latency_ms={stages['total_ms']:.0f}")
                    return {"text": text, "source": "clinical_main_vlm",
                            "request_id": request_id,
                            "latency_ms": round(stages["total_ms"]),
                            "image_meta": img_meta}
                except (KeyError, IndexError, ValueError) as ke:
                    errors.append(f"clinical_main 响应异常: {ke}")
            else:
                errors.append(f"clinical_main HTTP {r.status_code}: {r.text[:120]}")
    except httpx.TimeoutException:
        stages["clinical_main_ms"] = (time.time() - t0) * 1000
        errors.append("clinical_main 超时")
    except httpx.ConnectError:
        errors.append("clinical_main 服务未启动 (port 7870)")
    except Exception as e:
        errors.append(f"clinical_main 异常: {e}")

    stages["total_ms"] = (time.time() - t_start) * 1000
    detail = f"OCR 不可用：{'；'.join(errors)}"
    logger.error(f"[{request_id}] FAILED {detail} stages={stages}\n{traceback.format_exc()}")
    raise HTTPException(503, {"detail": detail, "request_id": request_id,
                              "category": "ocr_unavailable", "image_meta": img_meta})


class MedicalRecordReq(BaseModel):
    patient_id: str
    doctor_id: str = ""
    content: dict
    raw_text: str = ""


@app.post("/api/encounters/{eid}/medical-record")
async def save_medical_record(eid: str, req: MedicalRecordReq,
                              user: dict = Depends(require_doctor)):
    """Idempotent medical-record save (demo.md 第五节: 病历编辑器, 幂等不覆盖已编辑)."""
    rec = await MedicalRecordRepo.upsert_for_encounter(
        encounter_id=eid, patient_id=req.patient_id, content=req.content,
        raw_text=req.raw_text, doctor_id=user["id"])
    await audit.audit_log(actor=user["id"],
                          action="medical_record.save", target=eid,
                          meta={"record_id": rec.get("id"), "updated": rec.get("updated")})
    return rec


# ---------------------------------------------------------------------------
# Followup + risk (instruct #31-#34, demo.md 第八节)
# ---------------------------------------------------------------------------
class FollowupPlanReq(BaseModel):
    patient_id: str
    encounter_id: str
    days: int = 14
    template: str = "custom"
    questions: list = []


@app.post("/api/followups")
async def create_followup(req: FollowupPlanReq, user: dict = Depends(require_doctor)):
    plan_id = f"fup-{uuid.uuid4().hex[:8]}"
    rec = await FollowupPlanRepo.create(
        plan_id=plan_id, patient_id=req.patient_id, encounter_id=req.encounter_id,
        template=req.template, days=req.days, questions=req.questions)
    await audit.audit_log(actor=user["id"], action="followup.create",
                          target=req.patient_id, meta={"plan_id": plan_id})
    return rec


@app.get("/api/followups")
async def list_followups(user: dict = Depends(require_doctor)):
    return {"followups": await FollowupPlanRepo.list_all()}


class FollowupResponseReq(BaseModel):
    followup_plan_id: str
    patient_id: str
    answers: dict


@app.post("/api/followups/{fid}/response")
async def followup_response(fid: str, req: FollowupResponseReq):
    # LLM extract + rule engine (instruct #32/#33)
    from services.followup.rules import assess
    from packages.shared.providers import LLMProvider, AIUnavailable
    # HOTFIX 2026-10-29: previously accepted writes for ANY plan id / patient id.
    plan = await FollowupPlanRepo.get(fid)
    if not plan or plan.get("patient_id") != req.patient_id:
        raise HTTPException(404, "随访计划不存在或与患者不匹配")
    text = " ".join(str(v) for v in req.answers.values())
    llm_signals: list[str] = []
    try:
        llm = LLMProvider("clinical_main")
        resp = await llm.chat([
            {"role": "system", "content": "提取随访回答中的风险信号（如胸痛、呼吸困难、恶化等），返回 JSON {\"signals\":[...]}"},
            {"role": "user", "content": text[:1000]},
        ], json_mode=True)
        import json
        d = json.loads(resp.text) if resp.text.strip().startswith("{") else {"signals": []}
        llm_signals = d.get("signals", [])
    except AIUnavailable:
        pass  # instruct #30: rule engine still runs
    risk = assess(followup_text=text, llm_signals=llm_signals)
    # persist the response + risk event (demo.md #8)
    resp_rec = await FollowupResponseRepo.create(
        plan_id=fid, patient_id=req.patient_id, answers=req.answers,
        ai_summary={"signals": llm_signals, "reason": risk.get("reason", "")},
        risk_level=risk["risk_level"])
    if risk["risk_level"] in ("high_risk", "attention"):
        for rule in risk.get("triggered_rules", []) or ["assessed"]:
            await FollowupRiskEventRepo.create(
                response_id=resp_rec["id"], patient_id=req.patient_id,
                rule_triggered=rule, risk_level=risk["risk_level"],
                escalated=risk.get("escalation", False))
    await audit.audit_log(actor="patient", action="followup.response",
                          target=fid, meta={"risk": risk["risk_level"],
                                            "response_id": resp_rec["id"]})
    return {"risk": risk, "answers": req.answers, "response_id": resp_rec["id"]}


@app.get("/api/alerts")
async def alerts(user: dict = Depends(require_doctor)):
    return {"alerts": await FollowupRiskEventRepo.list_alerts()}


@app.get("/api/audit")
async def audit_list(limit: int = 50, user: dict = Depends(require_doctor)):
    """Read recent audit log entries from data/audit.ndjson (append-only)."""
    import json as _json
    from packages.shared.config import REPO_ROOT
    path = REPO_ROOT / "data" / "audit.ndjson"
    if not path.exists():
        return {"logs": []}
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    # last `limit` entries, reversed to newest-first
    recent = lines[-limit:][::-1]
    logs = []
    for ln in recent:
        try:
            rec = _json.loads(ln)
            logs.append({
                "id": rec.get("id", ""),
                "time": rec.get("ts", "").replace("T", " "),
                "actor": rec.get("actor", ""),
                "action": rec.get("action", ""),
                "target": rec.get("target", ""),
                "detail": _json.dumps(rec.get("meta", {}), ensure_ascii=False)[:120],
                "level": rec.get("meta", {}).get("level", "INFO"),
            })
        except Exception:
            continue
    return {"logs": logs}


# ---------------------------------------------------------------------------
# Prescription workflow (instruct #66: server-controlled status, demo.md #7)
# ---------------------------------------------------------------------------
class DraftReq(BaseModel):
    encounter_id: str
    patient_id: str
    doctor_id: str = "dr-001"
    ai_run_id: str = ""
    prescription: dict
    target_diagnosis_id: str = ""


@app.post("/api/prescriptions/drafts")
async def create_draft(req: DraftReq, user: dict = Depends(require_doctor)):
    pid = f"rx-{uuid.uuid4().hex[:8]}"
    rec = await PrescriptionDraftRepo.create(
        prescription_id=pid, encounter_id=req.encounter_id,
        patient_id=req.patient_id, doctor_id=req.doctor_id,
        ai_run_id=req.ai_run_id, structured_json=req.prescription,
        target_diagnosis_id=req.target_diagnosis_id)
    await audit.audit_log(actor="ai", action="prescription.draft.create", target=pid)
    return rec


@app.get("/api/prescriptions/drafts/{pid}")
async def get_draft(pid: str, user: dict = Depends(require_doctor)):
    """Get a prescription draft by ID — used by frontend polling for
    per-diagnosis treatment generation status."""
    rec = await PrescriptionDraftRepo.get(pid)
    if not rec:
        raise HTTPException(404, f"draft {pid} not found")
    return rec


@app.get("/api/prescriptions/pending")
async def list_pending_prescriptions(user: dict = Depends(require_doctor)):
    """List drafts awaiting doctor action (AI_GENERATED or DOCTOR_EDITING)."""
    from packages.shared.db import get_sessionmaker, PrescriptionDraft as PDM
    from sqlalchemy import select
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(select(PDM).where(
            PDM.status.in_(["AI_GENERATED", "DOCTOR_EDITING"])
        ).order_by(PDM.created_at.desc()))
        rows = res.scalars().all()
        return [{"id": d.id, "encounter_id": d.encounter_id,
                 "patient_id": d.patient_id, "status": d.status,
                 "target_diagnosis_id": getattr(d, "target_diagnosis_id", "") or ""}
                for d in rows]


class PatchDraftReq(BaseModel):
    prescription: dict
    doctor_id: str = "dr-001"
    new_status: str = "DOCTOR_EDITING"


@app.patch("/api/prescriptions/drafts/{pid}")
async def update_draft(pid: str, req: PatchDraftReq,
                        user: dict = Depends(require_doctor)):
    # Deep-merge: read existing structured_json, merge the new prescription
    # sub-key into it, so editing the prescription doesn't wipe the therapy
    # section (they coexist in the same structured_json for combined treatments).
    existing = await PrescriptionDraftRepo.get(pid)
    if not existing:
        raise HTTPException(404, f"draft {pid} not found")
    merged = dict(existing.get("structured_json") or {})
    # If the request's prescription contains sub-keys like western_medications,
    # herbal_drafts, etc., merge them INTO the existing "prescription" sub-dict
    # rather than flattening them to the top level.
    if isinstance(req.prescription, dict):
        existing_presc = merged.get("prescription")
        if isinstance(existing_presc, dict):
            new_presc = dict(existing_presc)
            new_presc.update(req.prescription)
            merged["prescription"] = new_presc
        else:
            merged["prescription"] = req.prescription
    rec = await PrescriptionDraftRepo.update_structured(
        pid, merged, new_status=req.new_status,
        doctor_id=user["id"])
    await audit.audit_log(actor=user["id"], action="prescription.edit", target=pid)
    return rec


@app.post("/api/prescriptions/drafts/{pid}/confirm")
async def confirm_draft(pid: str, req: dict,
                        user: dict = Depends(require_doctor)):
    return await PrescriptionDraftRepo.update_structured(
        pid, {}, new_status="DOCTOR_CONFIRMED",
        doctor_id=user.get("id", req.get("doctor_id", "dr-001")))


@app.post("/api/prescriptions/drafts/{pid}/sign")
async def sign_draft(pid: str, req: dict,
                     user: dict = Depends(require_doctor)):
    # instruct #67: doctor sign + audit (demo level: no password)
    effective_doctor = user.get("id", req.get("doctor_id", ""))
    rec = await PrescriptionDraftRepo.sign(pid, doctor_id=effective_doctor)
    sig_id = f"sig-{uuid.uuid4().hex[:8]}"
    await audit.audit_log(actor=effective_doctor, action="prescription.sign",
                          target=pid, meta={"signature_id": sig_id})
    return {"id": pid, "status": "SIGNED", "signature_id": sig_id, "repo": rec}


class SubmitHISReq(BaseModel):
    encounter_id: str
    doctor_id: str
    prescription: dict
    signature_id: str


@app.post("/api/prescriptions/{pid}/submit-his")
async def submit_his(pid: str, req: SubmitHISReq,
                     user: dict = Depends(require_doctor)):
    # server-controlled: must be SIGNED to submit (demo.md #7)
    effective_doctor = user.get("id", req.doctor_id)
    repo_rec = await PrescriptionDraftRepo.submit_to_his(
        pid, doctor_id=effective_doctor, signature_id=req.signature_id)
    if repo_rec.get("error"):
        raise HTTPException(409, repo_rec["error"])
    # only via HISAdapter (instruct #65); idempotent on encounter+signature
    rec = await get_his_adapter().create_prescription(
        encounter_id=req.encounter_id, doctor_id=effective_doctor,
        prescription=req.prescription, signature_id=req.signature_id)
    await audit.audit_log(actor=effective_doctor, action="prescription.submit_his",
                          target=pid, meta={"his_status": rec.get("his_status"),
                                            "request_id": rec.get("request_id")})
    # updatev2_3 §2b: persist formal e-prescriptions to DB (survive API restart).
    # One encounter can have multiple e-prescriptions by doc_type. The MockHIS
    # in-memory list remains for compatibility but is no longer the sole source.
    try:
        from packages.shared.eprescription_repo import EPrescriptionRepo
        enc = await EncounterRepo.get(req.encounter_id)
        patient_id = enc["patient_id"] if enc else ""
        diag_summary = ""
        try:
            from packages.shared.v12_repos import DiagnosisRecordRepo
            diags = await DiagnosisRecordRepo.list_for_encounter(req.encounter_id)
            confirmed = [d for d in diags if d.get("review_status") in ("accepted", "modified")]
            diag_summary = "；".join(
                (d.get("western_diagnosis") or d.get("tcm_syndrome") or "")
                for d in confirmed) or ""
        except Exception:
            pass
        # §2b-fix: if the caller sent an empty/absent prescription, pull the
        # real structured prescription from the SIGNED draft so e-prescription
        # documents are always populated from the doctor-confirmed content.
        presc = req.prescription
        therapy_from_draft: dict = {}
        if not presc:
            draft = await PrescriptionDraftRepo.get(pid)
            sj = (draft or {}).get("structured_json", {}) or {}
            presc = sj.get("prescription", {}) or {}
            therapy_from_draft = sj.get("therapy", {}) or {}
        if not isinstance(presc, dict):
            presc = {}
        # therapy may live under prescription.therapy (legacy) or as a top-level
        # structured_json key; normalize so the therapy_orders doc is populated.
        if therapy_from_draft and "therapy" not in presc:
            presc = {**presc, "therapy": therapy_from_draft}
        # western + patent medicines → one e-prescription doc
        wm = presc.get("western_medications", [])
        pm = presc.get("patent_medications") or presc.get("patent_medicines", [])
        if wm or pm:
            await EPrescriptionRepo.create(
                encounter_id=req.encounter_id, patient_id=patient_id,
                doctor_id=effective_doctor, prescription_draft_id=pid,
                doc_type="western_drugs", diagnosis_summary=diag_summary,
                prescription_json={"western_medications": wm,
                                   "patent_medications": pm},
                signature_id=req.signature_id,
                doctor_name=user.get("name", ""),
                his_status=rec.get("his_status", "ACCEPTED"),
                request_id=rec.get("request_id", ""))
        # herbal decoctions → separate e-prescription doc per applicable norm
        for hd in presc.get("herbal_drafts", []):
            if hd.get("herbs"):
                await EPrescriptionRepo.create(
                    encounter_id=req.encounter_id, patient_id=patient_id,
                    doctor_id=effective_doctor, prescription_draft_id=pid,
                    doc_type="herbal_decoction", diagnosis_summary=diag_summary,
                    prescription_json={"herbal_draft": hd},
                    signature_id=req.signature_id,
                    doctor_name=user.get("name", ""),
                    his_status=rec.get("his_status", "ACCEPTED"),
                    request_id=rec.get("request_id", ""))
        # therapy/rehab orders → separate e-prescription (doc_type=therapy_orders)
        therapy = presc.get("therapy") or {}
        therapy_candidates = therapy.get("candidates", []) if isinstance(therapy, dict) else []
        if therapy_candidates:
            await EPrescriptionRepo.create(
                encounter_id=req.encounter_id, patient_id=patient_id,
                doctor_id=effective_doctor, prescription_draft_id=pid,
                doc_type="therapy_orders", diagnosis_summary=diag_summary,
                prescription_json={"therapy": therapy,
                                   "lifestyle_advice": therapy.get("lifestyle_advice", [])},
                signature_id=req.signature_id,
                doctor_name=user.get("name", ""),
                his_status=rec.get("his_status", "ACCEPTED"),
                request_id=rec.get("request_id", ""))
        # link encounter to its final prescription (first e-prescription id)
        eps = await EPrescriptionRepo.list_for_encounter(req.encounter_id)
        if eps:
            await PrescriptionDraftRepo.set_final_prescription(req.encounter_id, eps[0]["id"])
    except Exception as e:
        print(f"[submit_his] e-prescription persist failed: {e}", flush=True)
    # §6e: auto-create followup plan after successful HIS submit
    try:
        enc = await EncounterRepo.get(req.encounter_id)
        if enc:
            fup_id = f"fup-{uuid.uuid4().hex[:8]}"
            await FollowupPlanRepo.create(
                plan_id=fup_id, patient_id=enc["patient_id"],
                encounter_id=req.encounter_id,
                template="post_visit", days=14,
                questions=_default_followup_questions())
            await audit.audit_log(actor=effective_doctor,
                                  action="followup.auto_create",
                                  target=fup_id,
                                  meta={"encounter_id": req.encounter_id})
    except Exception as e:
        print(f"[submit_his] auto followup failed: {e}", flush=True)
    return {"id": pid, "status": "SUBMITTED_TO_HIS", "his": rec}


def _default_followup_questions() -> list:
    """Default post-visit followup questions."""
    return [
        "症状是否缓解？",
        "是否出现新不适？",
        "是否按时服药？",
        "有无药物不良反应？",
    ]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _build_ctx(eid: str, req: AnalyzeReq, evidence: list[dict]):
    from services.ai_orchestrator.orchestrator import ClinicalContext
    # instruct_1.md §3: load doctor profile for personalization (no per-doctor model).
    # instruct_1.md §4: load hospital therapy catalog for the therapy module.
    doctor_profile: dict = {}
    hospital_programs: list[dict] = []
    try:
        from packages.shared.therapy_repo import DoctorProfileRepo, TherapyProgramRepo
        if req.doctor_id:
            doctor_profile = await DoctorProfileRepo.get(req.doctor_id) or {}
        hospital_programs = await TherapyProgramRepo.list_all()
    except Exception as e:
        print(f"[ctx] doctor profile/therapy load skipped: {e}", flush=True)
    return ClinicalContext(
        encounter_id=eid, patient_id=req.patient_id, patient=req.patient,
        medical_history=req.medical_history, labs=req.labs, medications=req.medications,
        allergies=req.allergies, tongue_features=req.tongue_features,
        tongue_image_key=req.tongue_image_key, raw_text=req.raw_text, evidence=evidence,
        doctor_id=req.doctor_id, doctor_profile=doctor_profile,
        hospital_programs=hospital_programs,
    )


def _hash_ctx(ctx) -> str:
    import hashlib, json
    blob = json.dumps({
        "p": ctx.patient, "h": ctx.medical_history, "l": ctx.labs,
        "m": ctx.medications, "a": ctx.allergies, "t": ctx.tongue_features,
        "r": ctx.raw_text, "d": getattr(ctx, "doctor_id", ""),
        "hp": len(getattr(ctx, "hospital_programs", [])),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _new_workflow():
    from services.ai_orchestrator.orchestrator import ClinicalAnalysisWorkflow
    return ClinicalAnalysisWorkflow()


# ===========================================================================
# V1.2 ENDPOINTS
# ===========================================================================

# ---------------------------------------------------------------------------
# V1.2 §3: Drug library CRUD + CSV import
# ---------------------------------------------------------------------------
class DrugReq(BaseModel):
    name: str
    generic_name: str = ""
    spec: str = ""
    unit: str = ""
    form: str = ""
    usage: str = ""
    source: str = "hospital"
    category: str = "western"
    his_code: str = ""
    insurance_code: str = ""
    contraindication: str = ""
    note: str = ""


@app.get("/api/drugs/search")
async def search_drugs(q: str = "", category: str = "", form: str = "", limit: int = 50, user: dict = Depends(require_doctor)):
    """V1.2 §3: DB-first drug search. MockHIS fallback for compat.
    Returns {drugs: [...]} for frontend compatibility."""
    from packages.shared.drug_repo import DrugRepo
    results = await DrugRepo.search(q=q, category=category, form=form)
    if not results:
        # Fallback to MockHIS in-memory
        his_drugs = await get_his_adapter().get_drug_dictionary(q)
        return {"drugs": his_drugs[:limit] if his_drugs else []}
    return {"drugs": results[:limit]}


@app.post("/api/drugs")
async def create_drug(req: DrugReq, user: dict = Depends(require_doctor)):
    from packages.shared.drug_repo import DrugRepo
    rec = await DrugRepo.create(**req.model_dump())
    await audit.audit_log(actor=user["id"], action="drug.create", target=rec["id"])
    return rec


@app.put("/api/drugs/{drug_id}")
async def update_drug(drug_id: str, req: DrugReq,
                      user: dict = Depends(require_doctor)):
    from packages.shared.drug_repo import DrugRepo
    rec = await DrugRepo.update(drug_id, **req.model_dump())
    if not rec:
        raise HTTPException(404, f"drug {drug_id} not found")
    return rec


@app.delete("/api/drugs/{drug_id}")
async def delete_drug(drug_id: str, user: dict = Depends(require_doctor)):
    from packages.shared.drug_repo import DrugRepo
    ok = await DrugRepo.delete(drug_id)
    if not ok:
        raise HTTPException(404, f"drug {drug_id} not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# updatev2_3 §2: list data cleanup — single + batch delete with manual
# cascade (SQLite has no FK ON DELETE CASCADE; db.py confirms zero ondelete).
# Encounter/Patient deletes clean all child rows in dependency order.
# ---------------------------------------------------------------------------
class DeleteBatchReq(BaseModel):
    ids: list[str]


@app.delete("/api/encounters/{eid}")
async def delete_encounter(eid: str, user: dict = Depends(require_doctor)):
    ok = await EncounterRepo.delete(eid)
    if not ok:
        raise HTTPException(404, f"encounter {eid} not found")
    await audit.audit_log(actor=user["id"], action="encounter.delete", target=eid)
    return {"ok": True}


@app.delete("/api/encounters")
async def delete_encounters_batch(req: DeleteBatchReq,
                                  user: dict = Depends(require_doctor)):
    n = await EncounterRepo.delete_many(req.ids)
    await audit.audit_log(actor=user["id"], action="encounter.delete_batch",
                          target=f"{n}/{len(req.ids)}")
    return {"ok": True, "deleted": n}


@app.delete("/api/patients/{pid}")
async def delete_patient(pid: str, user: dict = Depends(require_doctor)):
    ok = await PatientRepo.delete(pid)
    if not ok:
        raise HTTPException(404, f"patient {pid} not found")
    await audit.audit_log(actor=user["id"], action="patient.delete", target=pid)
    return {"ok": True}


@app.delete("/api/patients")
async def delete_patients_batch(req: DeleteBatchReq,
                                user: dict = Depends(require_doctor)):
    n = await PatientRepo.delete_many(req.ids)
    await audit.audit_log(actor=user["id"], action="patient.delete_batch",
                          target=f"{n}/{len(req.ids)}")
    return {"ok": True, "deleted": n}


@app.delete("/api/followups/{fid}")
async def delete_followup(fid: str, user: dict = Depends(require_doctor)):
    ok = await FollowupPlanRepo.delete(fid)
    if not ok:
        raise HTTPException(404, f"followup {fid} not found")
    await audit.audit_log(actor=user["id"], action="followup.delete", target=fid)
    return {"ok": True}


@app.delete("/api/followups")
async def delete_followups_batch(req: DeleteBatchReq,
                                user: dict = Depends(require_doctor)):
    n = await FollowupPlanRepo.delete_many(req.ids)
    await audit.audit_log(actor=user["id"], action="followup.delete_batch",
                          target=f"{n}/{len(req.ids)}")
    return {"ok": True, "deleted": n}


@app.delete("/api/medical-records/{mid}")
async def delete_medical_record(mid: str,
                                user: dict = Depends(require_doctor)):
    ok = await MedicalRecordRepo.delete(mid)
    if not ok:
        raise HTTPException(404, f"medical record {mid} not found")
    await audit.audit_log(actor=user["id"], action="medical_record.delete",
                          target=mid)
    return {"ok": True}


@app.delete("/api/medical-records")
async def delete_medical_records_batch(req: DeleteBatchReq,
                                       user: dict = Depends(require_doctor)):
    n = await MedicalRecordRepo.delete_many(req.ids)
    await audit.audit_log(actor=user["id"],
                          action="medical_record.delete_batch",
                          target=f"{n}/{len(req.ids)}")
    return {"ok": True, "deleted": n}


@app.post("/api/drugs/import")
async def import_drugs(file: UploadFile = File(...),
                       user: dict = Depends(require_doctor)):
    """V1.2 §3: CSV import. Columns: name,generic_name,spec,form,unit,usage,
    source,category,his_code"""
    from packages.shared.drug_repo import DrugRepo
    csv_bytes = await file.read()
    if not csv_bytes:
        raise HTTPException(400, "empty file")
    try:
        csv_text = csv_bytes.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        csv_text = csv_bytes.decode("gbk", errors="replace")
    rows = DrugRepo.parse_csv(csv_text)
    if not rows:
        raise HTTPException(400, "no rows found in CSV (need header row)")
    result = await DrugRepo.import_batch(rows)
    await audit.audit_log(actor=user["id"], action="drug.import",
                          target=f"{result['imported']} drugs")
    return result


# ---------------------------------------------------------------------------
# V1.2 §2: Diagnosis records — AI candidate → doctor review → prescription
# ---------------------------------------------------------------------------
class DiagnosisReq(BaseModel):
    patient_id: str
    doctor_id: str = ""
    ai_output_id: str = ""
    module: str = "western"  # western/tcm
    western_diagnosis: str = ""
    tcm_syndrome: str = ""
    main_evidence: str = ""
    treatment_goal: str = ""
    review_status: str = "pending"
    source: str = "ai"


@app.post("/api/encounters/{eid}/diagnoses")
async def create_diagnosis(eid: str, req: DiagnosisReq,
                           user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import DiagnosisRecordRepo
    effective_doctor = user.get("id", req.doctor_id)
    rec = await DiagnosisRecordRepo.create(
        encounter_id=eid, patient_id=req.patient_id, doctor_id=effective_doctor,
        ai_output_id=req.ai_output_id, module=req.module,
        western_diagnosis=req.western_diagnosis, tcm_syndrome=req.tcm_syndrome,
        main_evidence=req.main_evidence, treatment_goal=req.treatment_goal,
        review_status=req.review_status, source=req.source)
    await audit.audit_log(actor=effective_doctor,
                          action="diagnosis.create", target=rec["id"])
    return rec


@app.get("/api/encounters/{eid}/diagnoses")
async def list_diagnoses(eid: str, user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import DiagnosisRecordRepo
    return await DiagnosisRecordRepo.list_for_encounter(eid)


class PatchDiagnosisReq(BaseModel):
    review_status: str = ""
    western_diagnosis: str = ""
    tcm_syndrome: str = ""
    main_evidence: str = ""
    treatment_goal: str = ""
    doctor_notes: str = ""


@app.patch("/api/encounters/{eid}/diagnoses/{did}")
async def update_diagnosis(eid: str, did: str, req: PatchDiagnosisReq,
                           user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import DiagnosisRecordRepo
    fields = {k: v for k, v in req.model_dump().items() if v}
    rec = await DiagnosisRecordRepo.update(did, **fields)
    if not rec:
        raise HTTPException(404, f"diagnosis {did} not found")
    # updatev2_3 §1c: when a diagnosis's review_status changes, mark existing
    # encounter-scoped (target_diagnosis_id='') drafts stale so they are not
    # silently reused with a mismatched prescription. Signed/submitted records
    # are preserved (not invalidated).
    if "review_status" in fields:
        try:
            marked = await PrescriptionDraftRepo.mark_stale_for_encounter(eid)
            if marked:
                await audit.audit_log(
                    actor=user["id"], action="treatment_plan.mark_stale",
                    target=eid,
                    meta={"diagnosis_id": did, "marked_stale": marked,
                          "reason": "diagnosis review_status changed"})
        except Exception as e:
            print(f"[update_diagnosis] mark_stale failed: {e}", flush=True)
    await audit.audit_log(actor=user["id"], action="diagnosis.update", target=did)
    return rec


@app.post("/api/encounters/{eid}/diagnoses/{did}/prescription")
async def generate_diagnosis_prescription(eid: str, did: str, req: dict,
                                          user: dict = Depends(require_doctor)):
    """V1.2 §2: async prescription generation for a SINGLE diagnosis.

    Only called when doctor accepts a specific diagnosis. Returns 202 +
    a draft_id (immediately created as PENDING), then background task
    fills in the prescription via LLM.
    """
    import asyncio
    from packages.shared.v12_repos import DiagnosisRecordRepo
    from packages.shared.drug_repo import DrugRepo

    diag = await DiagnosisRecordRepo.list_for_encounter(eid)
    diag_rec = next((d for d in diag if d["id"] == did), None)
    if not diag_rec:
        raise HTTPException(404, f"diagnosis {did} not found")

    doctor_id = user.get("id", req.get("doctor_id", "dr-001"))
    patient_id = diag_rec.get("patient_id", "")

    # Create a PENDING prescription draft linked to this diagnosis
    pid = f"rx-{uuid.uuid4().hex[:8]}"
    await PrescriptionDraftRepo.create(
        prescription_id=pid, encounter_id=eid, patient_id=patient_id,
        doctor_id=doctor_id, ai_run_id="",
        structured_json={"status": "generating", "target_diagnosis_id": did})
    # Set target_diagnosis_id if the column exists
    try:
        from packages.shared.db import get_sessionmaker, PrescriptionDraft as PDM
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(PDM, pid)
            if rec:
                rec.target_diagnosis_id = did
                await s.commit()
    except Exception:
        pass

    async def _bg_rx():
        try:
            ctx = await _build_diag_ctx(eid, patient_id, doctor_id)
            wf = _new_workflow()
            status, data = await wf.generate_prescription_for_diagnosis(ctx, diag_rec)
            if status.value == "ok":
                await PrescriptionDraftRepo.update_structured(
                    pid, data, new_status="AI_GENERATED", doctor_id=doctor_id)
            else:
                await PrescriptionDraftRepo.update_structured(
                    pid, {"error": "generation failed", "reason": data.get("note", "")},
                    new_status="AI_GENERATED", doctor_id=doctor_id)
        except Exception as e:
            await PrescriptionDraftRepo.update_structured(
                pid, {"error": str(e)}, new_status="AI_GENERATED",
                doctor_id=doctor_id)
        finally:
            _RUN_TASKS.pop(pid, None)

    _RUN_TASKS[pid] = asyncio.create_task(_bg_rx())
    return JSONResponse(
        {"prescription_id": pid, "diagnosis_id": did,
         "status": "generating", "poll_url": f"/api/prescriptions/drafts/{pid}"},
        status_code=status.HTTP_202_ACCEPTED)


@app.post("/api/encounters/{eid}/diagnoses/{did}/treatment")
async def generate_diagnosis_treatment(eid: str, did: str, req: dict,
                                       user: dict = Depends(require_doctor)):
    """Accelerated combined treatment generation for a SINGLE diagnosis.

    One LLM call produces BOTH prescription + therapy in a single JSON.
    Halves GPU calls vs separate prescription + therapy → ~20-30s.
    Returns 202 + draft_id, background task fills in the treatment.
    """
    import asyncio
    from packages.shared.v12_repos import DiagnosisRecordRepo

    diag = await DiagnosisRecordRepo.list_for_encounter(eid)
    diag_rec = next((d for d in diag if d["id"] == did), None)
    if not diag_rec:
        raise HTTPException(404, f"diagnosis {did} not found")

    doctor_id = user.get("id", req.get("doctor_id", "dr-001"))
    patient_id = diag_rec.get("patient_id", "")

    pid = f"rx-{uuid.uuid4().hex[:8]}"
    await PrescriptionDraftRepo.create(
        prescription_id=pid, encounter_id=eid, patient_id=patient_id,
        doctor_id=doctor_id, ai_run_id="",
        structured_json={"status": "generating", "target_diagnosis_id": did})
    try:
        from packages.shared.db import get_sessionmaker, PrescriptionDraft as PDM
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(PDM, pid)
            if rec:
                rec.target_diagnosis_id = did
                await s.commit()
    except Exception:
        pass

    async def _bg_treatment():
        try:
            ctx = await _build_diag_ctx(eid, patient_id, doctor_id)
            wf = _new_workflow()
            status, data = await wf.generate_treatment_for_diagnosis(ctx, diag_rec)
            if status.value == "ok":
                await PrescriptionDraftRepo.update_structured(
                    pid, data, new_status="AI_GENERATED", doctor_id=doctor_id)
            else:
                await PrescriptionDraftRepo.update_structured(
                    pid, {"error": "generation failed", "reason": data.get("note", "")},
                    new_status="AI_GENERATED", doctor_id=doctor_id)
        except Exception as e:
            await PrescriptionDraftRepo.update_structured(
                pid, {"error": str(e)}, new_status="AI_GENERATED",
                doctor_id=doctor_id)
        finally:
            _RUN_TASKS.pop(pid, None)

    _RUN_TASKS[pid] = asyncio.create_task(_bg_treatment())
    return JSONResponse(
        {"prescription_id": pid, "diagnosis_id": did,
         "status": "generating", "poll_url": f"/api/prescriptions/drafts/{pid}"},
        status_code=status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------------------
# updatev2_3 §1: Encounter-level unified treatment plan
# ---------------------------------------------------------------------------
def _diagnosis_snapshot_hash(diagnoses: list[dict]) -> str:
    """Stable hash of the confirmed-diagnosis inputs so we can detect when a
    plan no longer matches the current diagnosis set (§1c: no silent reuse)."""
    import hashlib, json as _json
    payload = [
        {"id": d.get("id", ""), "module": d.get("module", ""),
         "western_diagnosis": d.get("western_diagnosis", ""),
         "tcm_syndrome": d.get("tcm_syndrome", ""),
         "review_status": d.get("review_status", "")}
        for d in diagnoses
    ]
    payload.sort(key=lambda x: x["id"])
    return hashlib.sha256(_json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


@app.post("/api/encounters/{eid}/treatment")
async def generate_encounter_treatment(eid: str, req: dict,
                                        user: dict = Depends(require_doctor)):
    """updatev2_3 §1: generate ONE unified comprehensive treatment plan for all
    confirmed (accepted/modified) diagnoses of this encounter.

    Replaces the per-diagnosis path: a single LLM call integrates western drugs,
    patent medicines, herbal decoctions, classic formulas, and applicable
    therapy/rehab, avoiding duplicate/contradictory treatment. The resulting
    PrescriptionDraft is encounter-scoped (target_diagnosis_id='' sentinel).
    Idempotent: if a non-stale unified draft already exists with the same
    diagnosis snapshot, return it instead of regenerating.
    """
    import asyncio
    import hashlib, json as _json
    from packages.shared.v12_repos import DiagnosisRecordRepo

    enc = await EncounterRepo.get(eid)
    if not enc:
        raise HTTPException(404, f"encounter {eid} not found")

    all_diags = await DiagnosisRecordRepo.list_for_encounter(eid)
    confirmed = [d for d in all_diags if d.get("review_status") in ("accepted", "modified")]
    if not confirmed:
        raise HTTPException(400, "没有已确认的诊断，无法生成综合治疗方案。"
                            "请先接受或修改至少一个诊断。")

    doctor_id = user.get("id", req.get("doctor_id", enc.get("doctor_id") or "dr-001"))
    patient_id = enc["patient_id"]
    snap_hash = _diagnosis_snapshot_hash(confirmed)

    # Idempotency / no-duplicate: reuse existing non-stale unified draft if its
    # diagnosis snapshot matches. If stale (diagnoses changed since generation),
    # generate a fresh one and mark the old stale.
    existing = await PrescriptionDraftRepo.get_unified_draft(eid)
    if existing and existing.get("diagnosis_snapshot_hash") == snap_hash:
        return JSONResponse(
            {"prescription_id": existing["id"], "status": existing["status"],
             "reused": True,
             "poll_url": f"/api/prescriptions/drafts/{existing['id']}"},
            status_code=status.HTTP_200_OK)

    # Create a single encounter-scoped draft (target_diagnosis_id='')
    pid = f"rx-{uuid.uuid4().hex[:8]}"
    await PrescriptionDraftRepo.create(
        prescription_id=pid, encounter_id=eid, patient_id=patient_id,
        doctor_id=doctor_id, ai_run_id="",
        structured_json={"status": "generating", "target_diagnosis_id": ""},
        target_diagnosis_id="")
    # stamp the diagnosis snapshot so we can detect future mismatches
    try:
        await PrescriptionDraftRepo.update_structured(
            pid, {}, new_status="AI_GENERATED", doctor_id=doctor_id,
            is_stale=False, diagnosis_snapshot_hash=snap_hash)
        # but structured_json is still the placeholder — re-set it
        from packages.shared.db import get_sessionmaker, PrescriptionDraft as PDM
        sm = get_sessionmaker()
        async with sm() as s:
            rec = await s.get(PDM, pid)
            if rec:
                rec.structured_json = {"status": "generating",
                                       "target_diagnosis_id": "",
                                       "diagnosis_snapshot_hash": snap_hash}
                await s.commit()
    except Exception as e:
        print(f"[treatment] snapshot stamp failed: {e}", flush=True)

    # mark older non-terminal encounter drafts stale (diagnoses changed)
    await PrescriptionDraftRepo.mark_stale_for_encounter(eid, except_id=pid)

    async def _bg_encounter_treatment():
        try:
            ctx = await _build_diag_ctx(eid, patient_id, doctor_id)
            wf = _new_workflow()
            status, data = await wf.generate_treatment_for_encounter(ctx, confirmed)
            # attach snapshot hash into structured_json for traceability
            data["diagnosis_snapshot_hash"] = snap_hash
            data["confirmed_diagnosis_count"] = len(confirmed)
            if status.value == "ok":
                await PrescriptionDraftRepo.update_structured(
                    pid, data, new_status="AI_GENERATED", doctor_id=doctor_id,
                    is_stale=False, diagnosis_snapshot_hash=snap_hash)
            else:
                await PrescriptionDraftRepo.update_structured(
                    pid, {"error": "generation failed",
                          "reason": data.get("note", "")},
                    new_status="AI_GENERATED", doctor_id=doctor_id)
        except Exception as e:
            await PrescriptionDraftRepo.update_structured(
                pid, {"error": str(e)}, new_status="AI_GENERATED",
                doctor_id=doctor_id)
        finally:
            _RUN_TASKS.pop(pid, None)

    _RUN_TASKS[pid] = asyncio.create_task(_bg_encounter_treatment())
    await audit.audit_log(actor=doctor_id, action="treatment_plan.generate",
                          target=eid,
                          meta={"draft_id": pid, "diagnoses": len(confirmed),
                                "snapshot_hash": snap_hash})
    return JSONResponse(
        {"prescription_id": pid, "status": "generating", "reused": False,
         "diagnoses": len(confirmed),
         "poll_url": f"/api/prescriptions/drafts/{pid}"},
        status_code=status.HTTP_202_ACCEPTED)


@app.get("/api/encounters/{eid}/treatment/latest")
async def get_latest_treatment(eid: str, user: dict = Depends(require_doctor)):
    """updatev2_3 §1: get the latest encounter-scoped unified treatment draft
    (target_diagnosis_id=''), including its stale flag so the frontend can
    warn the doctor if diagnoses changed since it was generated."""
    draft = await PrescriptionDraftRepo.get_unified_draft(eid)
    if not draft:
        # fall back to any encounter draft (legacy per-diagnosis)
        all_drafts = await PrescriptionDraftRepo.list_for_encounter(eid)
        if all_drafts:
            return {"draft": None, "has_legacy": True, "count": len(all_drafts)}
        return {"draft": None, "has_legacy": False, "count": 0}
    return {"draft": draft, "has_legacy": False, "count": 1}


# ---------------------------------------------------------------------------
# V1.2 §2: Feedback → create diagnosis record (accept/modify triggers it)
# ---------------------------------------------------------------------------
@app.post("/api/ai-outputs/{oid}/feedback")
async def feedback(oid: str, req: FeedbackReq,
                   user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import DiagnosisRecordRepo
    effective_doctor = user.get("id", req.doctor_id)
    # demo.md 第五节: Accept/Modify/Reject — persist original + edited (not audit-only stub)
    rec = await FeedbackRepo.create(
        ai_output_id=oid, doctor_id=effective_doctor, action=req.action,
        original_content=req.original_content, edited_content=req.edited_content,
        reason=str(req.reason))
    await audit.audit_log(actor=effective_doctor, action=f"ai.feedback.{req.action}",
                          target=oid, meta={"reason": str(req.reason), "fb_id": rec["id"]})
    # V1.2 §2: on accept/modify, auto-create a DiagnosisRecord if encounter_id provided
    diag_rec = None
    encounter_id = req.reason.get("encounter_id", "") if isinstance(req.reason, dict) else ""
    patient_id = req.reason.get("patient_id", "") if isinstance(req.reason, dict) else ""
    if req.action in ("accept", "modify") and encounter_id and patient_id:
        diag_rec = await DiagnosisRecordRepo.create(
            encounter_id=encounter_id, patient_id=patient_id,
            doctor_id=effective_doctor, ai_output_id=oid,
            module="western",
            western_diagnosis=req.edited_content or req.original_content,
            main_evidence="", treatment_goal="",
            review_status="accepted" if req.action == "accept" else "modified",
            source="ai")
    return {"ok": True, "ai_output_id": oid, "feedback_id": rec["id"],
            "diagnosis_record": diag_rec}


# ---------------------------------------------------------------------------
# updatev2_2 §5: Therapy item review — accept/edit/reject per candidate
# ---------------------------------------------------------------------------
class TherapyReviewReq(BaseModel):
    prescription_draft_id: str
    encounter_id: str = ""
    patient_id: str = ""
    item_index: int
    action: str  # accept / edit / reject
    edited_content: dict = {}
    original_content: dict = {}


@app.post("/api/therapy/review")
async def review_therapy_item(req: TherapyReviewReq,
                              user: dict = Depends(require_doctor)):
    """Per-item therapy accept/edit/reject.

    Each therapy candidate in a prescription draft gets its own
    TherapyItemReview row (AI_GENERATED → ACCEPTED/EDITED/REJECTED),
    preserving both original_content and edited_content + operator + time.
    """
    # Fetch original content from draft if not provided
    original = req.original_content
    if not original:
        draft = await PrescriptionDraftRepo.get(req.prescription_draft_id)
        if draft:
            sj = draft.get("structured_json", {}) or {}
            candidates = (sj.get("therapy", {}) or {}).get("candidates", [])
            if 0 <= req.item_index < len(candidates):
                original = candidates[req.item_index]
    # If encounter_id/patient_id not provided, derive from draft
    if not req.encounter_id or not req.patient_id:
        draft = await PrescriptionDraftRepo.get(req.prescription_draft_id)
        if draft:
            req.encounter_id = req.encounter_id or draft.get("encounter_id", "")
            req.patient_id = req.patient_id or draft.get("patient_id", "")

    action_map = {"accept": "ACCEPTED", "edit": "EDITED", "reject": "REJECTED"}
    status_val = action_map.get(req.action, "AI_GENERATED")
    edited = req.edited_content if req.action == "edit" else {}

    rec = await TherapyItemReviewRepo.upsert_for_draft(
        prescription_draft_id=req.prescription_draft_id,
        encounter_id=req.encounter_id,
        patient_id=req.patient_id,
        item_index=req.item_index,
        original_content=original,
        doctor_id=user["id"])
    rec = await TherapyItemReviewRepo.update_status(
        rec["id"], status=status_val,
        edited_content=edited if edited else None,
        doctor_id=user["id"])
    await audit.audit_log(
        actor=user["id"], action=f"therapy.review.{req.action}",
        target=req.prescription_draft_id,
        meta={"item_index": req.item_index, "review_id": rec["id"],
              "status": status_val})
    return rec


@app.get("/api/therapy/review")
async def list_therapy_reviews(draft_id: str = "",
                               encounter_id: str = "",
                               user: dict = Depends(require_doctor)):
    """List therapy item reviews for a draft or encounter."""
    if draft_id:
        return {"reviews": await TherapyItemReviewRepo.list_for_draft(draft_id)}
    if encounter_id:
        return {"reviews": await TherapyItemReviewRepo.list_for_encounter(encounter_id)}
    return {"reviews": []}


# ---------------------------------------------------------------------------
# updatev2_2 §6: Final medical record — assemble + finalize + detail
# ---------------------------------------------------------------------------
class FinalizeRecordReq(BaseModel):
    signature_id: str = ""


@app.post("/api/encounters/{eid}/finalize-record")
async def finalize_medical_record(eid: str, req: FinalizeRecordReq,
                                  user: dict = Depends(require_doctor)):
    """§6: assemble a complete, finalized medical record from all confirmed data.

    Gathers patient info, encounter data, confirmed diagnoses, the SIGNED
    prescription draft, accepted therapy items, followup plan, and audit
    trail into one MedicalRecord version with status='finalized'.
    Reopenable later via GET /api/medical-records/{id} without re-running AI.
    """
    from packages.shared.v12_repos import DiagnosisRecordRepo
    enc = await EncounterRepo.get(eid)
    if not enc:
        raise HTTPException(404, f"encounter {eid} not found")
    patient = await PatientRepo.get(enc["patient_id"])
    diagnoses = await DiagnosisRecordRepo.list_for_encounter(eid)
    # updatev2_3 §2e: prefer the encounter-scoped unified draft (target_diagnosis_id=''),
    # not the first SIGNED per-diagnosis draft. Fall back to legacy behavior.
    all_drafts = await PrescriptionDraftRepo.list_for_encounter(eid)
    final_draft = await PrescriptionDraftRepo.get_unified_draft(eid)
    if not final_draft:
        for d in all_drafts:
            if d.get("status") in ("SIGNED", "SUBMITTED_TO_HIS", "DOCTOR_CONFIRMED"):
                final_draft = d
                break
    # Accepted therapy items only
    therapy_reviews = await TherapyItemReviewRepo.list_for_encounter(eid)
    accepted_therapy = [r for r in therapy_reviews if r["status"] != "REJECTED"]
    # Followup plan
    followups = await FollowupPlanRepo.list_all()
    enc_followup = next((f for f in followups if f.get("encounter_id") == eid), None)
    # Audit trail for this encounter
    audit_trail = await audit.audit_for(target=eid)

    # updatev2_3 §2: also include persisted e-prescriptions (survive restart)
    e_prescriptions: list[dict] = []
    try:
        from packages.shared.eprescription_repo import EPrescriptionRepo
        e_prescriptions = await EPrescriptionRepo.list_for_encounter(eid)
    except Exception:
        pass
    # updatev2_4 §4: include the doctor's manually-typed medical record narrative
    # (the editor text saved via save_medical_record), so the finalized record
    # preserves the full clinical narrative — not just structured fields. The
    # editor text contains the 【已确认诊断】 and 【综合治疗方案】 blocks with
    # prescription grams and therapy acupoints in human-readable form.
    editor_text = ""
    try:
        existing_mr = await MedicalRecordRepo.list_for_encounter(eid)
        if existing_mr and existing_mr.get("content"):
            editor_text = (existing_mr["content"] or {}).get("text", "") or ""
    except Exception:
        pass
    # updatev2_4 §4: flatten prescription + therapy from the unified draft so the
    # PDF export can render full grams/acupoints even when the draft wasn't SIGNED.
    plan_json = final_draft.get("structured_json", {}) if final_draft else {}
    if isinstance(plan_json, dict):
        presc_json = plan_json.get("prescription", {})
        therapy_json = plan_json.get("therapy", {})
        # Legacy/malformed drafts may inline prescription fields at the top
        # level (western_medications, herbal_drafts, etc.) alongside "therapy"
        # instead of nesting them under "prescription". Collect them so the
        # PDF and editor blocks still render full details.
        if not presc_json:
            _PRESC_KEYS = ("western_medications", "patent_medications",
                           "patent_medicines", "herbal_drafts", "safety_notes")
            presc_json = {k: plan_json[k] for k in _PRESC_KEYS if k in plan_json}
        if not therapy_json:
            therapy_json = plan_json.get("therapy", {})
    else:
        presc_json, therapy_json = {}, {}
    content_json = {
        "patient_info": patient or {},
        "encounter_info": {
            "id": enc["id"], "chief_complaint": enc.get("chief_complaint", ""),
            "case_type": enc.get("case_type", ""), "status": enc.get("status", ""),
            "created_at": enc.get("created_at", ""),
        },
        "chief_complaint": enc.get("chief_complaint", ""),
        "history": enc.get("history", {}),
        "allergies": enc.get("allergies", []),
        "labs": enc.get("labs", []),
        "medications": enc.get("medications", []),
        "raw_text": enc.get("raw_text", ""),
        "editor_text": editor_text,
        "confirmed_diagnoses": diagnoses,
        "treatment_plan": final_draft.get("structured_json", {}) if final_draft else {},
        "prescription_flat": presc_json,
        "therapy_flat": therapy_json,
        "prescription_details": final_draft or {},
        "e_prescriptions": e_prescriptions,
        "therapy_items": accepted_therapy,
        "doctor_sign": {
            "doctor_id": user["id"], "doctor_name": user.get("name", ""),
            "signature_id": req.signature_id,
        },
        "followup_plan": enc_followup or {},
        "audit_trail": audit_trail,
    }
    # Save as a new MedicalRecord version, marked finalized
    record_id = f"mr-{uuid.uuid4().hex[:8]}"
    rec = await MedicalRecordRepo.upsert_for_encounter(
        encounter_id=eid, patient_id=enc["patient_id"],
        content=content_json, raw_text=enc.get("raw_text", ""),
        doctor_id=user["id"])
    # Set finalized status + signature on the record
    # updatev2_3 §2/§1c: if a finalized record exists, keep it as a historical
    # version and create a new finalized version with the latest content —
    # finalized records are never silently overwritten by later AI re-runs.
    try:
        from packages.shared.db import get_sessionmaker, MedicalRecord as MR
        sm = get_sessionmaker()
        async with sm() as s:
            mr = await s.get(MR, rec["id"])
            if mr:
                mr.status = "finalized"
                mr.finalized_at = datetime.utcnow()
                mr.doctor_id = user["id"]
                mr.signature_id = req.signature_id
                # link the followup plan if one exists for this encounter
                if enc_followup:
                    mr.followup_plan_id = enc_followup.get("id", "")
                await s.commit()
    except Exception as e:
        print(f"[finalize_record] status update failed: {e}", flush=True)
    await audit.audit_log(actor=user["id"], action="medical_record.finalize",
                          target=rec["id"], meta={"encounter_id": eid,
                                                  "signature_id": req.signature_id})
    rec["status"] = "finalized"
    rec["content_json"] = content_json
    return rec


# ---------------------------------------------------------------------------
# updatev2_3 §2f: E-prescription query/detail endpoints
# ---------------------------------------------------------------------------
@app.get("/api/encounters/{eid}/prescriptions")
async def list_encounter_prescriptions(eid: str,
                                         user: dict = Depends(require_doctor)):
    """updatev2_3 §2: list all persisted e-prescriptions for an encounter."""
    from packages.shared.eprescription_repo import EPrescriptionRepo
    return {"prescriptions": await EPrescriptionRepo.list_for_encounter(eid)}


@app.get("/api/prescriptions/{epid}")
async def get_eprescription_detail(epid: str, user: dict = Depends(require_doctor)):
    """updatev2_3 §2: single e-prescription detail."""
    from packages.shared.eprescription_repo import EPrescriptionRepo
    ep = await EPrescriptionRepo.get(epid)
    if not ep:
        raise HTTPException(404, f"e-prescription {epid} not found")
    return ep


# ---------------------------------------------------------------------------
# updatev2_3 §2: PDF export for medical record / e-prescription (fpdf2, cn font)
# ---------------------------------------------------------------------------
def _build_pdf_bytes(title: str, sections: list[tuple[str, str]]) -> bytes:
    """Render a simple multi-section PDF with fpdf2 using the bundled
    Source Han Serif SC font for CJK. Returns raw bytes. No external deps
    beyond fpdf2 (already installed in hiscopilot env)."""
    import io
    from fpdf import FPDF
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font_dir = REPO / "assets" / "fonts"
    regular = str(font_dir / "cn-regular.otf")
    medium = str(font_dir / "cn-medium.otf")
    pdf.add_font("CN", "", regular, uni=True)
    pdf.add_font("CN", "B", medium, uni=True)
    usable_w = pdf.w - pdf.l_margin - pdf.r_margin  # full usable width
    pdf.set_font("CN", "B", 16)
    pdf.multi_cell(usable_w, 12, title)
    pdf.ln(4)
    for label, body in sections:
        pdf.set_font("CN", "B", 11)
        pdf.multi_cell(usable_w, 7, label)
        pdf.set_font("CN", "", 10)
        for line in (body or "").split("\n"):
            # render blank lines as a space to keep vertical spacing; fpdf2
            # multi_cell raises on truly empty strings.
            pdf.multi_cell(usable_w, 6, line if line else " ")
        pdf.ln(2)
    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()


@app.get("/api/medical-records/{mid}/pdf")
async def export_medical_record_pdf(mid: str,
                                      user: dict = Depends(require_doctor)):
    """updatev2_3 §2: real server-side PDF export of a finalized medical record."""
    from packages.shared.db import get_sessionmaker, MedicalRecord
    sm = get_sessionmaker()
    async with sm() as s:
        mr = await s.get(MedicalRecord, mid)
        if not mr:
            raise HTTPException(404, f"medical record {mid} not found")
    c = mr.content_json or {}
    patient = c.get("patient_info", {}) or {}
    enc = c.get("encounter_info", {}) or {}
    diags = c.get("confirmed_diagnoses", []) or []
    plan = c.get("treatment_plan", {}) or {}
    # updatev2_4 §4: prefer the flattened prescription/therapy if available
    # (added in finalize), else fall back to nested plan.prescription/therapy.
    presc = c.get("prescription_flat") or (plan.get("prescription") if isinstance(plan, dict) else {}) or {}
    therapy = c.get("therapy_flat") or (plan.get("therapy") if isinstance(plan, dict) else {}) or {}
    sign = c.get("doctor_sign", {}) or {}
    editor_text = c.get("editor_text", "") or ""
    e_prescs = c.get("e_prescriptions", []) or []
    therapy_items = c.get("therapy_items", []) or []

    def _fmt_meds(meds):
        lines = []
        for m in (meds or []):
            lines.append(
                f"{m.get('medication','') or m.get('name','')} "
                f"{m.get('dose','')} {m.get('frequency','')} "
                f"{m.get('usage','') or '口服'}"
                f"{('  ×'+str(m.get('days',''))+'天') if m.get('days') else ''}"
                f"{('  用途:'+m['reason']) if m.get('reason') else ''}")
        return "\n".join(lines) or "无"

    def _fmt_herbs(hds):
        lines = []
        for hd in (hds or []):
            herbs = hd.get("herbs", [])
            doses = hd.get("doses", {}) or {}
            parts = [f"{h} {doses.get(h,'10g')}" for h in herbs]
            lines.append(f"{hd.get('name','自拟方')}（{hd.get('formula_type','')}）"
                         f" 煎法：{hd.get('decoction','水煎')}")
            lines.append("  " + "、".join(parts))
            if hd.get("rationale"):
                lines.append(f"  组方思路：{hd['rationale']}")
        return "\n".join(lines) or "无"

    def _fmt_therapy(cands):
        lines = []
        for c in (cands or []):
            ap = c.get("acupoints", []) or []
            lines.append(
                f"{c.get('category','')} - {c.get('name','')}")
            if ap:
                lines.append(f"  穴位：{'、'.join(ap)}")
            if c.get("location_basis"):
                lines.append(f"  定位依据：{c['location_basis']}")
            if c.get("manipulation"):
                lines.append(f"  手法：{c['manipulation']}")
            if c.get("direction_or_stimulation"):
                lines.append(f"  方向/刺激：{c['direction_or_stimulation']}")
            if c.get("duration_or_count"):
                lines.append(f"  时长/次数：{c['duration_or_count']}")
            if c.get("frequency"):
                lines.append(f"  频次：{c['frequency']}")
            if c.get("course"):
                lines.append(f"  疗程：{c['course']}")
            if c.get("therapeutic_goal"):
                lines.append(f"  目标：{c['therapeutic_goal']}")
            if c.get("contraindications"):
                lines.append(f"  禁忌：{c['contraindications']}")
            if c.get("rationale"):
                lines.append(f"  依据：{c['rationale']}")
        return "\n".join(lines) or "无"

    def _fmt_eprescs(eps):
        """Render persisted e-prescriptions with full drug/herb detail."""
        lines = []
        for ep in (eps or []):
            pj = ep.get("prescription_json", {}) or {}
            dt = ep.get("doc_type", "")
            label_map = {"western_drugs": "西药/成药", "herbal_decoction": "中药饮片",
                         "patent_medicine": "中成药", "therapy_orders": "理疗医嘱"}
            lines.append(f"[{label_map.get(dt, dt)}] 诊断：{ep.get('diagnosis_summary','')}")
            if dt == "herbal_decoction":
                hd = pj.get("herbal_draft", {}) or {}
                herbs = hd.get("herbs", [])
                doses = hd.get("doses", {}) or {}
                lines.append(f"  {hd.get('name','自拟方')}（{hd.get('formula_type','')}）煎法：{hd.get('decoction','水煎')}")
                lines.append("  " + "、".join(f"{h} {doses.get(h,'10g')}" for h in herbs))
            else:
                wm = pj.get("western_medications", [])
                pm = pj.get("patent_medications", [])
                for m in (wm + pm):
                    lines.append(f"  {m.get('medication','') or m.get('name','')}  "
                                 f"{m.get('dose','')}  {m.get('frequency','')}  "
                                 f"{m.get('usage','') or '口服'}"
                                 f"{('  ×'+str(m.get('days',''))+'天') if m.get('days') else ''}")
            lines.append(f"  签字：{ep.get('doctor_name','')}（{ep.get('doctor_id','')}）"
                         f"  HIS状态：{ep.get('his_status','')}")
        return "\n".join(lines) or "无"

    sections = [
        ("患者信息", f"姓名：{patient.get('name','未填写')}\n"
                  f"性别：{patient.get('gender','未填写')}\n"
                  f"年龄：{patient.get('age','未填写')}\n"
                  f"电话：{patient.get('phone','未填写')}"),
        ("就诊信息", f"就诊号：{enc.get('id','')}\n"
                  f"主诉：{c.get('chief_complaint','未填写')}\n"
                  f"状态：{enc.get('status','')}\n"
                  f"创建：{enc.get('created_at','')}"),
        ("现病史", c.get("raw_text", "未填写") or "未填写"),
        ("过敏史", "、".join(a.get("allergen","") for a in (c.get("allergies",[]) or [])) or "无"),
        ("确诊诊断", "\n".join(
            f"{d.get('module','')}：{d.get('western_diagnosis','') or d.get('tcm_syndrome','')}"
            + (f"（{d.get('tcm_syndrome','')}）" if d.get('western_diagnosis') and d.get('tcm_syndrome') else "")
            + (f"\n  主要依据：{d['main_evidence']}" if d.get('main_evidence') else "")
            + (f"\n  治疗目标：{d['treatment_goal']}" if d.get('treatment_goal') else "")
            for d in diags) or "无"),
        ("处方 - 西药/成药", _fmt_meds(presc.get("western_medications"))),
        ("处方 - 中成药", _fmt_meds(presc.get("patent_medications") or presc.get("patent_medicines"))),
        ("处方 - 中药饮片", _fmt_herbs(presc.get("herbal_drafts"))),
        ("处方 - 用药提示", "、".join(s for s in (presc.get("safety_notes") or []) if s) or "无"),
        ("理疗方案", _fmt_therapy(therapy.get("candidates"))),
        ("理疗 - 生活建议", "、".join(s for s in (therapy.get("lifestyle_advice") or []) if s) or "无"),
        ("正式电子处方", _fmt_eprescs(e_prescs)),
    ]
    # updatev2_4 §4: include the doctor's full medical record narrative
    # (editor text with 【已确认诊断】+【综合治疗方案】 blocks) so the PDF
    # preserves prescription grams, therapy acupoints, and all specifics the
    # doctor wrote/confirmed — not just the structured summary above.
    if editor_text:
        sections.append(("病历正文（医生编辑）", editor_text))
    sections.append(("签字信息", f"医生：{sign.get('doctor_name','')}（{sign.get('doctor_id','')}）\n"
                  f"签字ID：{sign.get('signature_id','')}\n"
                  f"定稿时间：{getattr(mr,'finalized_at','') or ''}"))
    pdf_bytes = _build_pdf_bytes("象元 AI 诊疗 — 诊疗记录", sections)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="record_{mid}.pdf"'})


@app.get("/api/prescriptions/{epid}/pdf")
async def export_eprescription_pdf(epid: str,
                                     user: dict = Depends(require_doctor)):
    """updatev2_3 §2: real server-side PDF export of a formal e-prescription."""
    from packages.shared.eprescription_repo import EPrescriptionRepo
    ep = await EPrescriptionRepo.get(epid)
    if not ep:
        raise HTTPException(404, f"e-prescription {epid} not found")
    pj = ep.get("prescription_json", {}) or {}
    if ep["doc_type"] == "herbal_decoction":
        hd = pj.get("herbal_draft", {}) or {}
        herbs = hd.get("herbs", [])
        doses = hd.get("doses", {}) or {}
        body = f"{hd.get('name','自拟方')}（{hd.get('formula_type','')}）\n煎法：{hd.get('decoction','水煎')}\n" + \
               "\n".join(f"{h}  {doses.get(h,'10g')}" for h in herbs)
        doc_label = "中药饮片处方"
    else:
        wm = pj.get("western_medications", [])
        pm = pj.get("patent_medications", [])
        lines = []
        for m in (wm + pm):
            lines.append(f"{m.get('medication','') or m.get('name','')}  "
                         f"{m.get('dose','')}  {m.get('frequency','')}  "
                         f"{m.get('usage','') or '口服'}"
                         f"{('  ×'+str(m.get('days',''))+'天') if m.get('days') else ''}")
        body = "\n".join(lines) or "无"
        doc_label = "西药/成药处方"
    sections = [
        ("处方类型", doc_label),
        ("就诊号", ep["encounter_id"]),
        ("患者ID", ep["patient_id"]),
        ("诊断摘要", ep.get("diagnosis_summary", "") or "无"),
        ("处方内容", body),
        ("医生签字", f"{ep.get('doctor_name','')}（{ep.get('doctor_id','')}）\n"
                  f"签字ID：{ep.get('signature_id','')}\n"
                  f"HIS状态：{ep.get('his_status','')}\n"
                  f"创建：{ep.get('created_at','')}"),
    ]
    pdf_bytes = _build_pdf_bytes(f"象元 AI 诊疗 — {doc_label}", sections)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="rx_{epid}.pdf"'})


@app.get("/api/medical-records/{mid}")
async def get_medical_record_detail(mid: str, user: dict = Depends(require_doctor)):
    """§6: full medical record detail — reopenable without re-running AI."""
    from packages.shared.db import get_sessionmaker, MedicalRecord
    sm = get_sessionmaker()
    async with sm() as s:
        mr = await s.get(MedicalRecord, mid)
        if not mr:
            raise HTTPException(404, f"medical record {mid} not found")
        return {
            "id": mr.id, "encounter_id": mr.encounter_id,
            "patient_id": mr.patient_id, "doctor_id": mr.doctor_id or "",
            "status": getattr(mr, "status", None) or "draft",
            "finalized_at": str(mr.finalized_at) if getattr(mr, "finalized_at", None) else "",
            "signature_id": getattr(mr, "signature_id", "") or "",
            "content": mr.content_json, "raw_text": mr.raw_text or "",
            "created_at": str(mr.created_at) if mr.created_at else "",
            "updated_at": str(mr.updated_at) if mr.updated_at else "",
        }


# ---------------------------------------------------------------------------
# V1.2 §6: Doctor QR token
# ---------------------------------------------------------------------------
@app.post("/api/doctors/{did}/qr-token")
async def create_qr_token(did: str, user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import DoctorTokenRepo
    from packages.shared.config import get_settings
    settings = get_settings()
    # Determine public base URL for QR code
    public_base = settings.public_base_url.strip().rstrip("/")
    if public_base:
        base = public_base
    else:
        # Fallback: detect local server IP (for LAN access)
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            server_ip = s.getsockname()[0]
            s.close()
        except Exception:
            server_ip = "127.0.0.1"
        base = f"https://{server_ip}:3443"
    rec = await DoctorTokenRepo.create_token(did, ttl_hours=24)
    token = rec["token"]
    return {
        "token": token, "doctor_id": did,
        "url": f"/intake?d={token}",
        "full_url": f"{base}/intake?d={token}",
        "expires_at": rec["expires_at"],
        "note": "二维码包含路由+令牌，不含患者信息。手机扫码后进入问诊页，"
                f"提交时自动绑定医生 {did}",
    }


@app.get("/api/qr/{token}")
async def validate_qr_token(token: str):
    from packages.shared.v12_repos import DoctorTokenRepo
    rec = await DoctorTokenRepo.validate(token)
    if not rec:
        raise HTTPException(403, "令牌无效或已过期")
    # Get doctor info
    from packages.shared.repo import UserRepo
    user = await UserRepo.get(rec["doctor_id"])
    return {
        "doctor_id": rec["doctor_id"],
        "doctor_name": user["name"] if user else rec["doctor_id"],
        "dept": "",
    }


# ---------------------------------------------------------------------------
# V1.2 §4: Bidirectional followup — doctor reply + patient timeline
# ---------------------------------------------------------------------------
@app.get("/api/followups/responses/pending")
async def pending_followup_responses(user: dict = Depends(require_doctor)):
    """Doctor's queue of patient followup responses awaiting reply."""
    from packages.shared.db import get_sessionmaker, FollowupResponse, FollowupPlan
    from sqlalchemy import select
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(
            select(FollowupResponse).order_by(FollowupResponse.created_at.desc()))
        responses = []
        for r in res.scalars().all():
            summary = r.ai_summary_json or {}
            # Check if this response has a doctor reply
            has_reply = bool(summary.get("doctor_reply"))
            if not has_reply:
                responses.append({
                    "id": r.id, "followup_plan_id": r.followup_plan_id,
                    "patient_id": r.patient_id,
                    "answers": r.answers_json,
                    "risk_level": r.risk_level,
                    "risk_reason": summary.get("risk_reason", ""),
                    "created_at": str(r.created_at),
                })
        return {"responses": responses}


class DoctorReplyReq(BaseModel):
    doctor_reply: str = ""
    reply: str = ""  # alias for frontend compat
    create_next_followup: bool = False
    next_days: int = 14
    next_questions: list = []


@app.post("/api/followups/responses/{rid}/reply")
async def doctor_reply_followup(rid: str, req: DoctorReplyReq,
                                user: dict = Depends(require_doctor)):
    from packages.shared.db import get_sessionmaker, FollowupResponse, FollowupPlan
    from sqlalchemy import select
    sm = get_sessionmaker()
    async with sm() as s:
        rec = await s.get(FollowupResponse, rid)
        if not rec:
            raise HTTPException(404, f"response {rid} not found")
        summary = dict(rec.ai_summary_json or {})
        reply_text = req.doctor_reply or req.reply
        summary["doctor_reply"] = reply_text
        summary["doctor_reply_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        summary["reply_time"] = summary["doctor_reply_at"]
        rec.ai_summary_json = summary
        # SQLAlchemy doesn't track mutations inside JSON columns by default
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(rec, "ai_summary_json")
        await s.commit()
    await audit.audit_log(actor=user["id"], action="followup.reply",
                          target=rid, meta={"reply_length": len(reply_text)})
    # Optionally create next followup
    next_plan = None
    if req.create_next_followup:
        # Get the original plan's patient_id and encounter_id
        async with sm() as s:
            plan = await s.get(FollowupPlan, rec.followup_plan_id)
            if plan:
                plan_id = f"fup-{uuid.uuid4().hex[:8]}"
                await FollowupPlanRepo.create(
                    plan_id=plan_id, patient_id=plan.patient_id,
                    encounter_id=plan.encounter_id,
                    template="followup", days=req.next_days,
                    questions=req.next_questions)
                next_plan = plan_id
    return {"ok": True, "doctor_reply": reply_text, "reply": reply_text, "next_followup_id": next_plan}


@app.get("/api/patients/{pid}/timeline")
async def patient_timeline(pid: str, user: dict = Depends(require_doctor)):
    """V1.2 §4: patient timeline — encounters + followups + responses + alerts."""
    from packages.shared.db import (
        get_sessionmaker, Encounter, FollowupPlan, FollowupResponse,
        FollowupRiskEvent,
    )
    from sqlalchemy import select
    sm = get_sessionmaker()
    timeline = []
    async with sm() as s:
        # encounters
        res = await s.execute(
            select(Encounter).filter_by(patient_id=pid)
            .order_by(Encounter.created_at))
        for e in res.scalars().all():
            timeline.append({
                "type": "encounter", "id": e.id,
                "label": e.chief_complaint or e.case_type or "就诊",
                "status": e.status, "time": str(e.created_at),
            })
        # followup plans
        res = await s.execute(
            select(FollowupPlan).filter_by(patient_id=pid)
            .order_by(FollowupPlan.created_at))
        for f in res.scalars().all():
            timeline.append({
                "type": "followup", "id": f.id,
                "label": f"随访计划({f.days}天)", "status": f.status,
                "time": str(f.created_at),
            })
        # followup responses
        res = await s.execute(
            select(FollowupResponse).filter_by(patient_id=pid)
            .order_by(FollowupResponse.created_at))
        for r in res.scalars().all():
            reply = (r.ai_summary_json or {}).get("doctor_reply", "")
            timeline.append({
                "type": "followup_response", "id": r.id,
                "label": "随访回复", "risk": r.risk_level,
                "doctor_reply": reply,
                "time": str(r.created_at),
            })
        # risk events
        res = await s.execute(
            select(FollowupRiskEvent).filter_by(patient_id=pid)
            .order_by(FollowupRiskEvent.created_at))
        for ev in res.scalars().all():
            timeline.append({
                "type": "risk_event", "id": ev.id,
                "label": ev.rule_triggered, "risk": ev.risk_level,
                "escalated": ev.escalated,
                "time": str(ev.created_at),
            })
    timeline.sort(key=lambda x: x["time"], reverse=True)
    return {"patient_id": pid, "timeline": timeline,
            "sms": "未配置", "wechat": "未配置"}


# ---------------------------------------------------------------------------
# V1.2 §1: Lab report upload + OCR persistence
# ---------------------------------------------------------------------------
@app.post("/api/encounters/{eid}/lab-reports")
async def upload_lab_report(eid: str, file: UploadFile = File(...),
                            user: dict = Depends(require_doctor)):
    """Upload a lab report image → OCR → persist LabReport + LabResults."""
    import httpx, base64
    from packages.shared.v12_repos import LabReportRepo
    # Get encounter for patient_id
    enc = await EncounterRepo.get(eid)
    if not enc:
        raise HTTPException(404, f"encounter {eid} not found")
    patient_id = enc.get("patient_id", "")
    image = await file.read()
    if not image:
        raise HTTPException(400, "empty image")
    mime = file.content_type or "image/png"
    img_b64 = base64.b64encode(image).decode("utf-8")
    data_url = f"data:{mime};base64,{img_b64}"
    ocr_prompt = ("请识别并提取图片中的所有文字内容，包括检验项目名称、"
                  "数值、单位、参考范围和异常标志。按原始格式输出。")
    errors: list[str] = []
    ocr_text = ""
    # Try PaddleOCR-VL first
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "http://127.0.0.1:7875/v1/chat/completions",
                json={
                    "model": "PaddleOCR-VL-1.6",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": ocr_prompt},
                        ],
                    }],
                    "max_tokens": 2000, "temperature": 0.1,
                })
            if r.status_code == 200:
                ocr_text = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        errors.append(str(e))
    # Fallback to clinical_main VLM
    if not ocr_text:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    "http://127.0.0.1:7870/v1/chat/completions",
                    json={
                        "model": "clinical_main",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": data_url}},
                                {"type": "text", "text": ocr_prompt},
                            ],
                        }],
                        "max_tokens": 2000, "temperature": 0.1,
                    })
                if r.status_code == 200:
                    ocr_text = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            errors.append(str(e))
    if not ocr_text:
        raise HTTPException(503, f"OCR 不可用：{'；'.join(errors)}")
    # Parse structured lab items from OCR text
    import re
    items = []
    for line in ocr_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.+?)\s{1,}(\d+\.?\d*)\s{1,}([a-zA-Z%/μµ·]+)?\s*(?:\(([^)]+)\))?\s*(↑|↓|HIGH|LOW)?", line)
        if m:
            items.append({
                "test_name": m.group(1).strip(),
                "value": m.group(2),
                "unit": m.group(3) or "",
                "reference_low": m.group(4) or "",
                "reference_high": m.group(4) or "",
                "abnormal_flag": m.group(5) or "",
            })
    # Persist LabReport + LabResults
    rec = await LabReportRepo.create_from_ocr(
        encounter_id=eid, patient_id=patient_id,
        ocr_text=ocr_text, items=items)
    await audit.audit_log(actor=user["id"], action="lab_report.upload",
                          target=eid, meta={"report_id": rec["id"],
                                            "items": len(items)})
    return {"report_id": rec["id"], "ocr_text": ocr_text,
            "items": items, "item_count": len(items)}


# ---------------------------------------------------------------------------
# V1.2 §5: Doctor experience mining — batch case import
# Supports both JSON text and multipart image upload.
# ---------------------------------------------------------------------------
class CaseImportReq(BaseModel):
    cases: list[dict]  # each: {text, image_path(optional), diagnosis, prescription...}


@app.post("/api/doctors/{did}/cases/import")
async def import_cases(did: str, req: CaseImportReq,
                       user: dict = Depends(require_doctor)):
    """V1.2 §5: batch upload historical cases (text) → async structure + mine."""
    import asyncio
    from packages.shared.v12_repos import CaseImportRepo
    cid = uuid.uuid4().hex[:24]
    rec = await CaseImportRepo.create(
        doctor_id=did, total_count=len(req.cases),
        cases_json={"cases": req.cases})
    cid = rec["id"]

    async def _bg_mine():
        try:
            from services.case_mining.pipeline import CaseMiningPipeline
            pipeline = CaseMiningPipeline(doctor_id=did, case_import_id=cid)
            await pipeline.run(req.cases)
        except Exception as e:
            await CaseImportRepo.update_progress(cid, status="failed", error=str(e))
            print(f"[case_mining] failed: {e}", flush=True)
        finally:
            _RUN_TASKS.pop(cid, None)

    _RUN_TASKS[cid] = asyncio.create_task(_bg_mine())
    return JSONResponse(
        {"import_id": cid, "total": len(req.cases), "status": "processing",
         "poll_url": f"/api/doctors/{did}/case-imports/{cid}"},
        status_code=status.HTTP_202_ACCEPTED)


@app.post("/api/doctors/{did}/cases/import-images")
async def import_case_images(did: str, files: list[UploadFile] = File(...),
                             user: dict = Depends(require_doctor)):
    """V1.2 §5: batch upload case images → save → OCR → structure + mine.

    Each image becomes one case. Images are saved to data/objects/,
    then the async pipeline OCRs each image and structures it via LLM.
    """
    import asyncio
    import os
    from packages.shared.v12_repos import CaseImportRepo

    OBJECTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "objects")
    OBJECTS_DIR = os.path.abspath(OBJECTS_DIR)
    os.makedirs(OBJECTS_DIR, exist_ok=True)

    cases = []
    for i, f in enumerate(files):
        ext = ".jpg"
        ct = f.content_type or ""
        if "png" in ct:
            ext = ".png"
        elif "webp" in ct:
            ext = ".webp"
        key = f"case_img_{uuid.uuid4().hex[:12]}{ext}"
        path = os.path.join(OBJECTS_DIR, key)
        content = await f.read()
        with open(path, "wb") as fp:
            fp.write(content)
        cases.append({
            "image_path": path,
            "image_key": key,
            "original_filename": f.filename or f"case-{i}",
            "text": "",
        })

    cid = uuid.uuid4().hex[:24]
    rec = await CaseImportRepo.create(
        doctor_id=did, total_count=len(cases),
        cases_json={"cases": cases})
    cid = rec["id"]

    async def _bg_mine():
        try:
            from services.case_mining.pipeline import CaseMiningPipeline
            pipeline = CaseMiningPipeline(doctor_id=did, case_import_id=cid)
            await pipeline.run(cases)
        except Exception as e:
            await CaseImportRepo.update_progress(cid, status="failed", error=str(e))
            print(f"[case_mining] image import failed: {e}", flush=True)
        finally:
            _RUN_TASKS.pop(cid, None)

    _RUN_TASKS[cid] = asyncio.create_task(_bg_mine())
    return JSONResponse(
        {"import_id": cid, "total": len(cases), "status": "processing",
         "poll_url": f"/api/doctors/{did}/case-imports/{cid}"},
        status_code=status.HTTP_202_ACCEPTED)


@app.get("/api/doctors/{did}/case-imports/{cid}")
async def get_case_import(did: str, cid: str,
                          user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import CaseImportRepo
    rec = await CaseImportRepo.get(cid)
    if not rec:
        raise HTTPException(404, f"case import {cid} not found")
    return rec


@app.get("/api/doctors/{did}/patterns")
async def list_patterns(did: str, user: dict = Depends(require_doctor)):
    from packages.shared.v12_repos import ExperiencePatternRepo
    return {"patterns": await ExperiencePatternRepo.list_for_doctor(did)}


@app.post("/api/doctors/{did}/patterns/{pid}/confirm")
async def confirm_pattern(did: str, pid: str,
                          user: dict = Depends(require_doctor)):
    """V1.2 §5: doctor confirms a mined pattern → writes to DoctorProfile."""
    from packages.shared.v12_repos import ExperiencePatternRepo
    from packages.shared.therapy_repo import DoctorProfileRepo
    rec = await ExperiencePatternRepo.confirm(pid)
    if not rec:
        raise HTTPException(404, f"pattern {pid} not found")
    # Add to doctor's experience_notes
    profile = await DoctorProfileRepo.get(did) or {}
    notes = profile.get("experience_notes", [])
    note_text = (f"[{rec['category']}] {rec['pattern_text']} "
                 f"(样本{rec['sample_size']}, 置信{rec['confidence']:.0%})")
    if note_text not in notes:
        notes.append(note_text)
    await DoctorProfileRepo.upsert(
        doctor_id=did, dept=profile.get("dept", ""),
        specialties=profile.get("specialties", []),
        syndrome_preference=profile.get("syndrome_preference", ""),
        formula_preference=profile.get("formula_preference", ""),
        herb_preference=profile.get("herb_preference", ""),
        non_drug_preference=profile.get("non_drug_preference", ""),
        analysis_focus=profile.get("analysis_focus", ""),
        experience_notes=notes)
    await audit.audit_log(actor=did, action="pattern.confirm", target=pid)
    return rec


# ---------------------------------------------------------------------------
# V1.2 §4: followup create from encounter (convenience wrapper)
# ---------------------------------------------------------------------------
@app.get("/api/followups/responses/{rid}")
async def get_followup_response(rid: str, user: dict = Depends(require_doctor)):
    from packages.shared.db import get_sessionmaker, FollowupResponse
    sm = get_sessionmaker()
    async with sm() as s:
        r = await s.get(FollowupResponse, rid)
        if not r:
            raise HTTPException(404, f"response {rid} not found")
        summary = r.ai_summary_json or {}
        return {
            "id": r.id, "plan_id": r.followup_plan_id,
            "followup_plan_id": r.followup_plan_id,
            "patient_id": r.patient_id, "answers": r.answers_json,
            "risk_level": r.risk_level, "ai_summary": summary,
            "doctor_reply": summary.get("doctor_reply", ""),
            "doctor_reply_at": summary.get("doctor_reply_at", ""),
            "risk_reason": summary.get("risk_reason", ""),
            "created_at": str(r.created_at),
        }


async def _build_diag_ctx(eid: str, patient_id: str, doctor_id: str):
    """Build a minimal ClinicalContext for per-diagnosis prescription."""
    from services.ai_orchestrator.orchestrator import ClinicalContext
    from packages.shared.therapy_repo import DoctorProfileRepo
    # Load encounter
    enc = await EncounterRepo.get(eid)
    patient = await PatientRepo.get(patient_id) or {}
    doctor_profile = {}
    try:
        doctor_profile = await DoctorProfileRepo.get(doctor_id) or {}
    except Exception:
        pass
    allergies = (enc or {}).get("allergies", patient.get("allergies", []))
    medications = (enc or {}).get("medications", [])
    labs = (enc or {}).get("labs", [])
    clinical_data = (enc or {}).get("clinical_data", {})
    return ClinicalContext(
        encounter_id=eid, patient_id=patient_id, patient=patient,
        medical_history=clinical_data.get("history", {}),
        labs=labs, medications=medications, allergies=allergies,
        raw_text=clinical_data.get("raw_text", ""),
        doctor_id=doctor_id, doctor_profile=doctor_profile)


def main():
    import uvicorn
    settings = get_settings()
    uvicorn.run("app:app", host="0.0.0.0", port=settings.app_port,
                reload=False, log_level=settings.log_level)


if __name__ == "__main__":
    main()
