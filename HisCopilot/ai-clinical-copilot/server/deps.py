"""FastAPI dependencies: db session, current staff user, permission gates, patient-token actors,
request context, optimistic-lock helper and request idempotency."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.base import get_db
from server.db.models import AccessToken, Encounter, IdempotencyKey, User
from server.domain.enums import Role, TokenKind
from server.domain.ids import hash_obj, utcnow
from server.errors import Conflict, Forbidden, NotFound, StaleVersion, Unauthorized
from server.security import audit
from server.security.audit import Actor
from server.security.auth import PORTAL_HEADER, SESSION_COOKIE, resolve_session, resolve_token
from server.security.permissions import can, perms_for


# ---------------------------------------------------------------- request context
@dataclass
class Ctx:
    request_id: str
    ip: str
    user_agent: str


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def get_ctx(request: Request) -> Ctx:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = uuid.uuid4().hex[:12]
        request.state.request_id = rid
    return Ctx(request_id=rid, ip=client_ip(request), user_agent=request.headers.get("user-agent", "")[:255])


# ---------------------------------------------------------------- staff auth
PASSWORD_CHANGE_ALLOWED = {"/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password"}

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    user = resolve_session(db, token)
    if user is None:
        raise Unauthorized("未登录或登录已过期，请重新登录", code="unauthenticated")
    request.state.user_id = user.id
    # Server-side enforcement (not just a UI hint): an account flagged must_change_password can do NOTHING except
    # read its own profile, log out, and change the password.
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED:
        raise Forbidden("首次登录或密码已重置，请先修改密码后再继续", code="password_change_required")
    return user


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    return resolve_session(db, token) if token else None


def require(permission: str) -> Callable[..., User]:
    """Dependency factory: `user: User = Depends(require("rx.sign"))`."""
    def dep(user: User = Depends(current_user)) -> User:
        if not can(user.role, permission):
            raise Forbidden("当前角色无权执行该操作", code="permission_denied",
                            details={"required": permission, "role": user.role})
        return user
    dep.__name__ = f"require_{permission.replace('.', '_')}"
    return dep


def actor_of(user: User, ctx: Ctx) -> Actor:
    return Actor.staff(user, ip=ctx.ip, request_id=ctx.request_id)


# ---------------------------------------------------------------- encounter ownership
def load_encounter_for(db: Session, user: User, encounter_id: str, *, write: bool = False) -> Encounter:
    """Fetch an encounter enforcing ownership.

    DOCTOR: own encounters, or unassigned ones (waiting pool). Reading someone else's is denied (and the
    denial is audited by the exception handler's caller via audit 'denied' outcome). NURSE/ADMIN/PHARMACIST
    follow the permission matrix only (no per-doctor ownership) but writes by non-doctors are limited to facts
    by the endpoint's own permission.
    """
    enc = db.get(Encounter, encounter_id)
    if enc is None:
        raise NotFound("就诊记录不存在", code="encounter_not_found")
    if user.role == Role.DOCTOR.value and enc.doctor_id and enc.doctor_id != user.id:
        audit.record(db, Actor.staff(user), "access.denied", entity_type="encounter", entity_id=enc.id,
                     encounter_id=enc.id, patient_id=enc.patient_id, outcome="denied",
                     summary="尝试访问其他医师的就诊")
        db.commit()
        raise Forbidden("该就诊属于其他医师", code="not_your_encounter")
    return enc


def check_version(current: int, expected: int | None, what: str = "记录", *, db: Session | None = None, obj=None) -> None:
    """Optimistic locking: the client sends the row_version it edited; mismatch → 409 stale_version.

    When `db` and `obj` are given the check is ATOMIC: take the write lock first, re-read the row's version under it, then
    compare — so of N concurrent writers holding the same version exactly one passes, the rest get stale_version (no silent
    last-write-wins). Callers that pass only `current` keep the old, non-atomic behaviour (use only where a race is harmless)."""
    if expected is None:
        return
    if db is not None and obj is not None:
        from server.db.base import acquire_write_lock
        acquire_write_lock(db)
        db.refresh(obj, attribute_names=["row_version"])
        current = obj.row_version
    if expected != current:
        raise StaleVersion(f"该{what}已被他人或其他窗口修改，请刷新后重试",
                           details={"current_version": current, "expected_version": expected})


# ---------------------------------------------------------------- patient-facing tokens
@dataclass
class PatientActor:
    token: AccessToken
    kind: TokenKind

    @property
    def patient_id(self) -> str | None:
        return self.token.patient_id


def _token_from(request: Request) -> str | None:
    return request.headers.get(PORTAL_HEADER) or None


def intake_link_actor(request: Request, db: Session = Depends(get_db)) -> PatientActor:
    tok = resolve_token(db, _token_from(request), kind=TokenKind.INTAKE_LINK)
    if tok is None:
        raise Unauthorized("问诊二维码无效或已过期，请向医护人员重新获取", code="bad_intake_link")
    return PatientActor(tok, TokenKind.INTAKE_LINK)


def intake_session_actor(request: Request, db: Session = Depends(get_db)) -> PatientActor:
    tok = resolve_token(db, _token_from(request), kind=TokenKind.INTAKE_SESSION)
    if tok is None:
        raise Unauthorized("问诊会话已失效，请重新扫码", code="bad_intake_session")
    return PatientActor(tok, TokenKind.INTAKE_SESSION)


def portal_actor(request: Request, db: Session = Depends(get_db)) -> PatientActor:
    tok = resolve_token(db, _token_from(request), kind=TokenKind.PORTAL)
    if tok is None:
        raise Unauthorized("随访链接无效或已过期，请联系诊所", code="bad_portal_token")
    return PatientActor(tok, TokenKind.PORTAL)


# ---------------------------------------------------------------- idempotency
class Idempotent:
    """Replay-safe mutation guard keyed by the `Idempotency-Key` header.

        idem = Idempotent(db, request, actor_key=user.id, key=idempotency_key, body=payload)
        if idem.replay is not None: return idem.replay          # same key + same body → stored response
        ... perform work ...
        idem.complete(201, result)                              # persist for future replays

    Same key with a DIFFERENT body → 409 (client bug). A request still in flight under the same key → 409.
    """

    STALE_AFTER_S = 120          # an unfinished key older than this belongs to a crashed request → take it over
    WAIT_S = 20                  # a duplicate waits up to this long for the original to finish, then replays its response

    def __init__(self, db: Session, request: Request, *, actor_key: str, key: str | None, body: object):
        self.db, self.row, self.replay = db, None, None
        if not key:
            return
        scope = f"{actor_key}|{request.method} {request.url.path}"
        req_hash = hash_obj(body)
        mine = False
        self.row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key))
        if self.row is None:
            try:
                self.row = IdempotencyKey(scope=scope, key=key, request_hash=req_hash, status_code=0)
                db.add(self.row)
                db.commit()
                mine = True                       # WE inserted it: we are the one request that does the work
            except IntegrityError:                # lost the insert race to an identical request
                db.rollback()
                self.row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key))
        if self.row is None:
            return
        if self.row.request_hash != req_hash:
            raise Conflict("同一个幂等键被用于不同的请求内容", code="idempotency_key_reuse")
        if mine:
            return
        # someone else owns this key: replay their finished response; if they are still working, wait for them
        import time
        deadline = time.time() + self.WAIT_S
        while not self.row.status_code:
            if (utcnow() - self.row.created_at).total_seconds() > self.STALE_AFTER_S:
                self.row.created_at = utcnow()    # abandoned by a crashed request → this one takes over
                db.commit()
                return
            if time.time() > deadline:
                raise Conflict("相同请求正在处理中，请稍候几秒后刷新查看结果", code="request_in_progress")
            time.sleep(0.1)
            db.rollback()                         # end our snapshot so the next read sees the owner's commit
            db.refresh(self.row)
        self.replay = (self.row.status_code, self.row.response_json)

    def complete(self, status_code: int, response: object) -> None:
        if self.row is None:
            return
        self.row.status_code = status_code
        self.row.response_json = json.loads(json.dumps(response, default=str))
        self.row.completed_at = utcnow()
        self.db.commit()

    def abort(self) -> None:
        """Release the key after a failed attempt so the client may retry with the same key."""
        if self.row is not None and self.row.status_code == 0:
            self.db.delete(self.row)
            self.db.commit()


def idem_header(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    if idempotency_key and len(idempotency_key) > 120:
        raise Conflict("Idempotency-Key 过长", code="bad_idempotency_key")
    return idempotency_key
