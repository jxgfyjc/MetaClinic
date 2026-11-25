"""Durable job engine (SQLite-backed queue + worker threads).

Lifecycle  queued → running → succeeded | failed | cancelled   (+ `attempts`/`max_attempts` retry)
Durability every state change is a committed row; nothing lives only in memory. On startup `recover()` re-queues
           jobs that were `running` when the process died (stale heartbeat) so a restart never silently loses work
           — and never double-runs a finished job (terminal states are final).
Concurrency one worker pool per *resource class*; the pool size is the concurrency limit for that class:
               asr=1, ocr=1 (CPU llama.cpp servers are single-flight), llm=1 (GPU: parallel requests don't add
               throughput), light=2 (DB-only jobs).
           A claim is an atomic conditional UPDATE, so even several API processes could share one queue.
Idempotency `enqueue(idempotency_key=...)` returns the existing job instead of creating a duplicate.
Handlers   `@register("kind")` functions `(ctx: JobContext) -> dict`; they raise `JobError(code, msg, retryable)`
           (or ModelError) for expected failures. Unexpected exceptions are recorded with a traceback excerpt.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from server.db.base import new_session, session_scope
from server.db.models import Job, JobEvent
from server.domain.enums import JobStatus
from server.domain.ids import hash_obj, utcnow

log = logging.getLogger("xy.jobs")

# `style` = long batch jobs of the 医师经验画像 module (case extraction). Its own pool so a 100-case extraction can never block a patient's
# analysis/plan job; local-model calls still serialise per call through analysis.LLM_LOCK.
POOLS = {"asr": 1, "ocr": 1, "llm": 1, "light": 2, "style": 1}
HEARTBEAT_EVERY_S = 5
STALE_AFTER_S = 45
RETRY_BACKOFF_S = (5, 20, 60)


class JobError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class Cancelled(Exception):
    pass


_HANDLERS: dict[str, tuple[Callable[["JobContext"], dict], str]] = {}


def register(kind: str, resource: str = "light") -> Callable:
    def deco(fn: Callable[["JobContext"], dict]):
        _HANDLERS[kind] = (fn, resource)
        return fn
    return deco


def resource_for(kind: str) -> str:
    return _HANDLERS[kind][1] if kind in _HANDLERS else "light"


@dataclass
class JobContext:
    job_id: str
    kind: str
    input: dict
    attempt: int
    worker: str

    def progress(self, pct: float, msg: str = "") -> None:
        with session_scope() as s:
            j = s.get(Job, self.job_id)
            if j is None:
                return
            if j.cancel_requested:
                raise Cancelled()
            j.progress, j.progress_msg = max(0.0, min(1.0, pct)), msg[:200]
            j.heartbeat_at = utcnow()
            s.add(JobEvent(job_id=self.job_id, level="info", message=msg[:400] or f"{int(pct * 100)}%"))

    def check_cancel(self) -> None:
        with session_scope() as s:
            j = s.get(Job, self.job_id)
            if j is not None and j.cancel_requested:
                raise Cancelled()


# --------------------------------------------------------------------------- enqueue / control
def enqueue(kind: str, input_json: dict, *, subject_type: str = "", subject_id: str = "", encounter_id: str | None = None,
            idempotency_key: str | None = None, priority: int = 50, max_attempts: int = 2, timeout_s: int = 600,
            created_by: str = "system", delay_s: int = 0, session=None) -> Job:
    """Create a queued job (or return the existing one for the same idempotency key). Pass `session` to enlist the
    insert in the caller's transaction (recommended so the job and its subject row commit together)."""
    own = session is None
    s = session or new_session()
    try:
        if idempotency_key:
            ex = s.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
            if ex is not None:
                return ex
        j = Job(kind=kind, resource=resource_for(kind), status=JobStatus.QUEUED.value, priority=priority,
                subject_type=subject_type, subject_id=subject_id, encounter_id=encounter_id,
                idempotency_key=idempotency_key, input_json=input_json, input_hash=hash_obj(input_json),
                max_attempts=max_attempts, timeout_s=timeout_s, created_by=created_by,
                run_after=utcnow() + timedelta(seconds=delay_s))
        s.add(j)
        s.flush()
        s.add(JobEvent(job_id=j.id, message="已加入队列"))
        if own:
            s.commit()
        return j
    except IntegrityError:
        s.rollback()
        ex = s.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if ex is None:
            raise
        return ex
    finally:
        if own:
            s.close()


def cancel(job_id: str) -> bool:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None or j.status in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            return False
        if j.status == JobStatus.QUEUED.value:
            j.status, j.finished_at = JobStatus.CANCELLED.value, utcnow()
            s.add(JobEvent(job_id=j.id, message="已取消（尚未开始）"))
        else:
            j.cancel_requested = True
            s.add(JobEvent(job_id=j.id, message="已请求取消（将在下一个检查点生效）"))
        return True


def retry(job_id: str, *, reset_attempts: bool = True) -> Job | None:
    """Manual retry of a failed/cancelled job (creates no duplicate: same row goes back to queued)."""
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None or j.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            return None
        j.status, j.cancel_requested = JobStatus.QUEUED.value, False
        j.error_code = j.error_detail = ""
        j.run_after, j.finished_at, j.locked_by = utcnow(), None, ""
        j.progress, j.progress_msg = 0.0, ""
        if reset_attempts:
            j.attempts = 0
        s.add(JobEvent(job_id=j.id, message="人工重试"))
        s.flush()
        return j


# --------------------------------------------------------------------------- claiming
def _claim(resource: str, worker: str) -> Job | None:
    with session_scope() as s:
        now = utcnow()
        cand = s.scalar(select(Job.id).where(Job.status == JobStatus.QUEUED.value, Job.resource == resource,
                                              Job.run_after <= now).order_by(Job.priority, Job.created_at).limit(1))
        if cand is None:
            return None
        res = s.execute(update(Job).where(Job.id == cand, Job.status == JobStatus.QUEUED.value).values(
            status=JobStatus.RUNNING.value, locked_by=worker, started_at=now, heartbeat_at=now, attempts=Job.attempts + 1))
        if res.rowcount != 1:
            return None                                              # lost the race to another worker
        s.add(JobEvent(job_id=cand, message=f"开始执行（worker {worker}）"))
        s.flush()
        j = s.get(Job, cand)
        s.expunge(j)
        return j


def recover(stale_after_s: int = STALE_AFTER_S) -> int:
    """Re-queue `running` jobs whose heartbeat stopped (process crash/restart). Returns how many."""
    n = 0
    with session_scope() as s:
        limit = utcnow() - timedelta(seconds=stale_after_s)
        for j in s.scalars(select(Job).where(Job.status == JobStatus.RUNNING.value,
                                             or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < limit))):
            if j.attempts >= j.max_attempts:
                j.status, j.finished_at = JobStatus.FAILED.value, utcnow()
                j.error_code, j.error_detail = "interrupted", "任务执行中服务重启/中断，已达到最大重试次数"
                s.add(JobEvent(job_id=j.id, level="error", message="中断且已达最大重试次数 → 失败"))
                _notify_terminal(j.id)
            else:
                j.status, j.locked_by, j.run_after = JobStatus.QUEUED.value, "", utcnow()
                s.add(JobEvent(job_id=j.id, level="warn", message="检测到任务中断（服务重启），已重新排队"))
            n += 1
    return n


# --------------------------------------------------------------------------- execution
def _finish_ok(job_id: str, result: dict) -> None:
    with session_scope() as s:
        j = s.get(Job, job_id)
        j.status, j.result_json, j.progress, j.finished_at = JobStatus.SUCCEEDED.value, result or {}, 1.0, utcnow()
        j.error_code = j.error_detail = ""
        s.add(JobEvent(job_id=job_id, message="执行成功"))


def _finish_fail(job_id: str, code: str, message: str, retryable: bool, detail: str = "") -> bool:
    """Returns True if re-queued for retry."""
    with session_scope() as s:
        j = s.get(Job, job_id)
        j.error_code, j.error_detail = code, (message + (("\n" + detail) if detail else ""))[:4000]
        if retryable and j.attempts < j.max_attempts:
            back = RETRY_BACKOFF_S[min(j.attempts - 1, len(RETRY_BACKOFF_S) - 1)]
            j.status, j.locked_by, j.run_after = JobStatus.QUEUED.value, "", utcnow() + timedelta(seconds=back)
            s.add(JobEvent(job_id=job_id, level="warn", message=f"失败（{code}）：{message}；{back}s 后自动重试（第 {j.attempts}/{j.max_attempts} 次）"))
            return True
        j.status, j.finished_at = JobStatus.FAILED.value, utcnow()
        s.add(JobEvent(job_id=job_id, level="error", message=f"失败（{code}）：{message}"))
    return False


def _finish_cancel(job_id: str) -> None:
    with session_scope() as s:
        j = s.get(Job, job_id)
        j.status, j.finished_at = JobStatus.CANCELLED.value, utcnow()
        s.add(JobEvent(job_id=job_id, message="已取消"))


_TERMINAL_HOOKS: list[Callable[[str], None]] = []


def on_terminal(fn: Callable[[str], None]) -> None:
    """Register a callback(job_id) invoked after a job reaches succeeded/failed/cancelled (domain sync)."""
    _TERMINAL_HOOKS.append(fn)


def _notify_terminal(job_id: str) -> None:
    for fn in _TERMINAL_HOOKS:
        try:
            fn(job_id)
        except Exception:                                  # noqa: BLE001
            log.exception("terminal hook failed for %s", job_id)


def run_one(j: Job, worker: str) -> None:
    """Execute a claimed job synchronously (used by workers and directly by tests)."""
    from server.ai.client import ModelError
    handler = _HANDLERS.get(j.kind)
    if handler is None:
        _finish_fail(j.id, "no_handler", f"未知任务类型 {j.kind}", False)
        _notify_terminal(j.id)
        return
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_EVERY_S):
            try:
                with session_scope() as s:
                    jj = s.get(Job, j.id)
                    if jj is not None:
                        jj.heartbeat_at = utcnow()
            except Exception:                              # noqa: BLE001
                pass

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    ctx = JobContext(job_id=j.id, kind=j.kind, input=j.input_json or {}, attempt=j.attempts, worker=worker)
    started = time.time()
    requeued = False
    try:
        result = handler[0](ctx)
        if time.time() - started > j.timeout_s:
            log.warning("job %s exceeded soft timeout %ss", j.id, j.timeout_s)
        _finish_ok(j.id, result if isinstance(result, dict) else {})
    except Cancelled:
        _finish_cancel(j.id)
    except (JobError, ModelError) as e:
        code = getattr(e, "code", "error")
        requeued = _finish_fail(j.id, code, getattr(e, "message", str(e)), bool(getattr(e, "retryable", False)))
    except Exception as e:                                 # noqa: BLE001
        requeued = _finish_fail(j.id, "unexpected_error", f"{type(e).__name__}: {e}", False, traceback.format_exc()[-1500:])
    finally:
        stop.set()
    if not requeued:
        _notify_terminal(j.id)


class WorkerPool:
    """Starts POOLS[resource] threads per resource class. Safe to start once per process."""

    def __init__(self, pools: dict[str, int] | None = None, poll_s: float = 1.0):
        self.pools = pools or POOLS
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        recovered = recover()
        if recovered:
            log.warning("recovered %d interrupted job(s)", recovered)
        for res, n in self.pools.items():
            for i in range(n):
                th = threading.Thread(target=self._loop, args=(res, f"{res}-{i}"), name=f"job-{res}-{i}", daemon=True)
                th.start()
                self._threads.append(th)
        sweeper = threading.Thread(target=self._sweep, name="job-sweeper", daemon=True)
        sweeper.start()
        self._threads.append(sweeper)

    def _loop(self, resource: str, worker: str) -> None:
        while not self._stop.is_set():
            try:
                j = _claim(resource, worker)
            except Exception:                              # noqa: BLE001
                log.exception("claim failed")
                self._stop.wait(2)
                continue
            if j is None:
                self._stop.wait(self.poll_s)
                continue
            run_one(j, worker)

    def _sweep(self) -> None:
        while not self._stop.wait(20):
            try:
                recover()
            except Exception:                              # noqa: BLE001
                log.exception("recover sweep failed")

    def stop(self) -> None:
        self._stop.set()


def drain(max_jobs: int = 50, resources: tuple[str, ...] = ("asr", "ocr", "llm", "light", "style"), *, ignore_delay: bool = False) -> int:
    """Synchronously run queued jobs in the calling thread (tests / `python -m server.jobs.drain`).

    `ignore_delay=True` first pulls every queued job's `run_after` to now (tests: skip retry backoff / start delay)."""
    if ignore_delay:
        with session_scope() as s:
            for j in s.scalars(select(Job).where(Job.status == JobStatus.QUEUED.value)):
                j.run_after = utcnow()
    n = 0
    while n < max_jobs:
        progressed = False
        for r in resources:
            j = _claim(r, f"drain-{r}")
            if j is not None:
                run_one(j, f"drain-{r}")
                n += 1
                progressed = True
        if not progressed:
            break
    return n
