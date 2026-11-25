"""Application factory: middleware, uniform errors, lifespan (migrate → seed → job workers), routers."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.config import get_settings
from server.db import migrate
from server.db.base import get_engine, init_db
from server.errors import ApiError

log = logging.getLogger("xy.app")
INSTANCE_ID = uuid.uuid4().hex[:10]            # changes on every API start
STARTED_AT = time.time()

# --------------------------------------------------------------------------- idle tracking
# Shared with the v1 API (services/api/app.py): every "business" request bumps
# data/logs/last_activity so the GPU idle-watcher daemon can tell when the
# copilot has been quiet long enough to free GPU1 for AIComic. See the matching
# block in services/api/app.py for the idle definition (POST + logged-in GET).
_REPO = Path(__file__).resolve().parents[1]
_ACTIVITY_FILE = _REPO / "data" / "logs" / "last_activity"
_ACTIVITY_PATHS = (
    "/api/encounters", "/api/patients", "/api/medical-records", "/api/drugs",
    "/api/formulas", "/api/followups", "/api/ai-runs", "/api/knowledge",
    "/api/prescriptions", "/api/asr/", "/api/ocr/", "/api/alerts", "/api/audit",
)


def _touch_activity() -> None:
    try:
        _ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVITY_FILE.write_text(f"{time.time():.0f}\n")
    except Exception:  # noqa: BLE001
        pass


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = init_db(s.db_path)
        applied = migrate.migrate(eng, s.db_path, s.backups_dir)
        if applied:
            log.info("applied migrations: %s", applied)
        s.objects_dir.mkdir(parents=True, exist_ok=True)
        from server.seed import seed_reference_data, seed_demo_users
        from server.db.base import session_scope
        with session_scope() as db:
            seed_reference_data(db)
            if s.seed_demo_users and not s.is_production:
                seed_demo_users(db)
            elif s.seed_demo_users and s.is_production:
                log.warning("XY_SEED_DEMO_USERS ignored in production — create accounts with `python -m server.cli create-user`")
        pool = None
        # Handlers must be registered whether or not workers run in THIS process: enqueue() uses the registry to
        # route a job to its resource pool (asr/ocr/llm), and tests/CLIs drain the queue synchronously.
        import server.jobs.handlers  # noqa: F401
        import server.style.jobs  # noqa: F401   (style_parse / style_extract)
        if s.jobs_enabled:
            from server.jobs.engine import WorkerPool
            pool = WorkerPool()
            pool.start()
            app.state.pool = pool
            log.info("job workers started")
        yield
        if pool:
            pool.stop()

    app = FastAPI(title="MetaClinic API", version="3.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)

    @app.middleware("http")
    async def ctx_mw(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:32] or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        t0 = time.time()
        try:
            resp = await call_next(request)
        except Exception:                                      # noqa: BLE001 - last-resort guard
            log.exception("unhandled error rid=%s %s %s", rid, request.method, request.url.path)
            resp = JSONResponse({"error": {"code": "internal_error", "message": "服务器内部错误，请稍后重试或联系管理员",
                                           "request_id": rid}}, status_code=500)
        resp.headers["X-Request-ID"] = rid
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        # idle heartbeat: POST always counts; GET counts only for clinical-data paths
        try:
            method = request.method.upper()
            path = request.url.path
            if method == "POST" or (
                method == "GET" and (
                    path == "/api/therapy-programs" or path.startswith("/api/therapy-programs/")
                    or any(path.startswith(p) for p in _ACTIVITY_PATHS)
                )
            ):
                _touch_activity()
        except Exception:  # noqa: BLE001
            pass
        ms = int((time.time() - t0) * 1000)
        if ms > 2000 or resp.status_code >= 500:
            log.warning("slow/err rid=%s %s %s -> %s in %dms", rid, request.method, request.url.path, resp.status_code, ms)
        return resp

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details,
                                       "request_id": getattr(request.state, "request_id", "")}},
                            status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]} for e in exc.errors()[:10]]
        return JSONResponse({"error": {"code": "validation_error", "message": "提交的内容不符合要求，请检查标红字段",
                                       "details": {"fields": fields}, "request_id": getattr(request.state, "request_id", "")}}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        """A UNIQUE violation that reaches the top is almost always two requests racing (double click / two windows):
        answer 409 so the client can refresh and retry, instead of a 500. Other integrity errors are real bugs → still 500."""
        msg = str(getattr(exc, "orig", exc))
        rid = getattr(request.state, "request_id", "")
        if "UNIQUE constraint failed" in msg:
            log.warning("unique conflict rid=%s %s %s: %s", rid, request.method, request.url.path, msg)
            return JSONResponse({"error": {"code": "conflict", "message": "操作冲突：可能是重复提交或其他窗口同时修改，请刷新后确认结果再重试",
                                           "details": None, "request_id": rid}}, status_code=409)
        log.exception("integrity error rid=%s %s %s", rid, request.method, request.url.path)
        return JSONResponse({"error": {"code": "internal_error", "message": "服务器内部错误，请稍后重试", "details": None, "request_id": rid}}, status_code=500)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse({"error": {"code": code, "message": "接口不存在" if exc.status_code == 404 else str(exc.detail),
                                       "request_id": getattr(request.state, "request_id", "")}}, status_code=exc.status_code)

    from server.api import router as api_router
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "uptime_s": int(time.time() - STARTED_AT), "instance": INSTANCE_ID}

    @app.get("/api/v1/ping", include_in_schema=False)
    def ping():
        """Public, data-free. Lets ops verify that the web proxy talks to THIS API instance (compare with /healthz)."""
        return {"ok": True, "instance": INSTANCE_ID}

    return app
