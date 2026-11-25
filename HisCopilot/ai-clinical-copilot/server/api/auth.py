"""Staff authentication, profile, user administration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.api.serializers import user_out
from server.config import get_settings
from server.db.base import get_db
from server.db.models import User
from server.deps import Ctx, actor_of, current_user, get_ctx, require
from server.domain.enums import Role
from server.errors import ApiError, Conflict, NotFound, Unauthorized, Unprocessable
from server.security import audit
from server.security.ratelimit import rate_limit
from server.security.audit import Actor
from server.security.auth import (AuthError, SESSION_COOKIE, hash_password, login, logout, password_problem, revoke_all_sessions,
                                  verify_password)

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def _cookie_secure(request: Request) -> bool:
    mode = get_settings().cookie_secure
    if mode == "true":
        return True
    if mode == "false":
        return False
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


@router.post("/auth/login")
def do_login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    rate_limit(f"login-ip:{ctx.ip}", 30, 300)                 # credential-spraying guard on top of per-account lockout
    try:
        res = login(db, body.username, body.password, ip=ctx.ip, user_agent=ctx.user_agent)
    except AuthError as e:
        audit.record(db, Actor(kind="staff", id=body.username[:40], name=body.username[:40], ip=ctx.ip, request_id=ctx.request_id),
                     "auth.login_failed", outcome="denied", summary=e.message)
        db.commit()
        raise ApiError(e.message, code=e.code, status=e.status)
    audit.record(db, Actor.staff(res.user, ip=ctx.ip, request_id=ctx.request_id), "auth.login", summary="登录成功")
    db.commit()
    s = get_settings()
    response.set_cookie(SESSION_COOKIE, res.token, httponly=True, samesite="lax", secure=_cookie_secure(request),
                        max_age=s.session_ttl_hours * 3600, path="/")
    return {"user": user_out(res.user, with_perms=True), "must_change_password": res.must_change_password}


@router.post("/auth/logout")
def do_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    logout(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"user": user_out(user, with_perms=True)}


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


@router.post("/auth/change-password")
def change_password(body: ChangePwIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user), ctx: Ctx = Depends(get_ctx)):
    if not verify_password(body.current_password, user.password_hash):
        raise Unprocessable("当前密码不正确", code="bad_current_password")
    problem = password_problem(body.new_password)
    if problem:
        raise Unprocessable(problem, code="weak_password")
    if body.new_password == body.current_password:
        raise Unprocessable("新密码不能与当前密码相同", code="same_password")
    user.password_hash, user.must_change_password = hash_password(body.new_password), False
    revoke_all_sessions(db, user.id, except_token=request.cookies.get(SESSION_COOKIE))
    audit.record(db, actor_of(user, ctx), "auth.password_change", entity_type="user", entity_id=user.id, summary="修改密码")
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- admin: users
class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    display_name: str = Field(min_length=1, max_length=64)
    role: Role
    password: str = Field(min_length=8, max_length=72)
    dept: str = ""
    title: str = ""
    license_no: str = ""


class UserUpdateIn(BaseModel):
    display_name: str | None = None
    role: Role | None = None
    dept: str | None = None
    title: str | None = None
    license_no: str | None = None
    active: bool | None = None


@router.get("/admin/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require("user.manage"))):
    return {"items": [user_out(u) for u in db.scalars(select(User).order_by(User.role, User.username))]}


@router.post("/admin/users", status_code=201)
def create_user(body: UserCreateIn, db: Session = Depends(get_db), admin: User = Depends(require("user.manage")), ctx: Ctx = Depends(get_ctx)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise Conflict("该工号已存在", code="username_taken")
    problem = password_problem(body.password)
    if problem:
        raise Unprocessable(problem, code="weak_password")
    u = User(username=body.username, display_name=body.display_name, role=body.role.value, dept=body.dept, title=body.title,
             license_no=body.license_no, password_hash=hash_password(body.password), must_change_password=True)
    db.add(u)
    db.flush()
    audit.record(db, actor_of(admin, ctx), "user.create", entity_type="user", entity_id=u.id, summary=f"创建账号 {u.username}（{u.role}）")
    db.commit()
    return user_out(u)


@router.patch("/admin/users/{uid}")
def update_user(uid: str, body: UserUpdateIn, db: Session = Depends(get_db), admin: User = Depends(require("user.manage")), ctx: Ctx = Depends(get_ctx)):
    u = db.get(User, uid)
    if not u:
        raise NotFound("用户不存在")
    changes = body.model_dump(exclude_none=True)
    if u.id == admin.id and (changes.get("active") is False or ("role" in changes and changes["role"] != Role.ADMIN)):
        raise Conflict("不能停用或降级自己的管理员账号", code="self_lockout")
    if "role" in changes:
        changes["role"] = changes["role"].value
    for k, v in changes.items():
        setattr(u, k, v)
    if changes.get("active") is False:
        revoke_all_sessions(db, u.id)
    audit.record(db, actor_of(admin, ctx), "user.update", entity_type="user", entity_id=u.id, summary=f"修改账号 {u.username}", detail=changes)
    db.commit()
    return user_out(u)


class ResetPwIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)


@router.post("/admin/users/{uid}/reset-password")
def reset_password(uid: str, body: ResetPwIn, db: Session = Depends(get_db), admin: User = Depends(require("user.manage")), ctx: Ctx = Depends(get_ctx)):
    u = db.get(User, uid)
    if not u:
        raise NotFound("用户不存在")
    problem = password_problem(body.new_password)
    if problem:
        raise Unprocessable(problem, code="weak_password")
    u.password_hash, u.must_change_password, u.failed_logins, u.locked_until = hash_password(body.new_password), True, 0, None
    revoke_all_sessions(db, u.id)
    audit.record(db, actor_of(admin, ctx), "user.reset_password", entity_type="user", entity_id=u.id, summary=f"重置 {u.username} 的密码")
    db.commit()
    return {"ok": True}
