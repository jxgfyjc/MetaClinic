"""Authentication: bcrypt passwords, server-side revocable sessions, lockout, scoped patient tokens.

Staff cookie `xy_session` holds an opaque random token; only sha256(token) is stored, so a database
leak does not yield usable sessions, and logout / admin revoke take effect immediately.
Patients never log in: they hold hashed, scoped `AccessToken`s (intake link / intake session / portal).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.models import AccessToken, AuthSession, User
from server.domain.enums import TokenKind
from server.domain.ids import new_token, sha256_hex, utcnow

SESSION_COOKIE = "xy_session"
PORTAL_HEADER = "x-xy-token"           # patient-facing tokens travel in this header (never in cookies / logs)

# Constant-time dummy hash so unknown usernames cost the same as wrong passwords.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=10)).decode()


# ---------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    if len(pw.encode("utf-8")) > 72:
        raise ValueError("password too long (bcrypt limit 72 bytes)")
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], (hashed or _DUMMY_HASH).encode()) and bool(hashed)
    except Exception:
        return False


def password_problem(pw: str) -> str | None:
    """Return a human-readable reason a new password is unacceptable, else None."""
    if len(pw) < 8:
        return "密码至少 8 位"
    if len(pw.encode("utf-8")) > 72:
        return "密码过长"
    if pw.isdigit() or pw.isalpha():
        return "密码需同时包含字母和数字"
    return None


# ---------------------------------------------------------------- staff sessions
class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class LoginResult:
    token: str
    user: User
    must_change_password: bool


def login(db: Session, username: str, password: str, *, ip: str = "", user_agent: str = "") -> LoginResult:
    s = get_settings()
    user = db.scalar(select(User).where(User.username == username.strip()))
    now = utcnow()
    if user is not None and user.locked_until and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        verify_password(password, "")                      # keep timing uniform
        raise AuthError("account_locked", f"登录失败次数过多，账号已锁定，请 {mins} 分钟后再试", 429)
    ok = verify_password(password, user.password_hash if user else "")
    if user is None or not ok or not user.active:
        if user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= s.login_max_failures:
                user.locked_until = now + timedelta(minutes=s.login_lock_minutes)
                user.failed_logins = 0
            db.commit()
        raise AuthError("bad_credentials", "工号或密码错误")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    token = new_token(32)
    db.add(AuthSession(id=sha256_hex(token), user_id=user.id, created_at=now,
                       expires_at=now + timedelta(hours=s.session_ttl_hours), last_seen_at=now,
                       ip=ip[:64], user_agent=user_agent[:255]))
    db.commit()
    return LoginResult(token=token, user=user, must_change_password=bool(user.must_change_password))


def resolve_session(db: Session, token: str | None) -> User | None:
    """Return the active user for a session token (and slide the idle window), or None."""
    if not token:
        return None
    s = get_settings()
    sess = db.get(AuthSession, sha256_hex(token))
    now = utcnow()
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
        return None
    if (now - sess.last_seen_at) > timedelta(minutes=s.session_idle_minutes):
        sess.revoked_at = now
        db.commit()
        return None
    user = sess.user
    if user is None or not user.active:
        return None
    if (now - sess.last_seen_at) > timedelta(minutes=2):   # avoid a write per request
        sess.last_seen_at = now
        db.commit()
    return user


def logout(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.get(AuthSession, sha256_hex(token))
    if sess and sess.revoked_at is None:
        sess.revoked_at = utcnow()
        db.commit()


def revoke_all_sessions(db: Session, user_id: str, *, except_token: str | None = None) -> int:
    keep = sha256_hex(except_token) if except_token else None
    n = 0
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id,
                                                     AuthSession.revoked_at.is_(None))):
        if sess.id != keep:
            sess.revoked_at = utcnow()
            n += 1
    db.commit()
    return n


# ---------------------------------------------------------------- patient-facing access tokens
def issue_token(db: Session, *, kind: TokenKind, ttl: timedelta, doctor_id: str | None = None,
                patient_id: str | None = None, encounter_id: str | None = None, parent_id: str | None = None,
                max_uses: int = 0, label: str = "", created_by: str | None = None, meta: dict | None = None
                ) -> tuple[str, AccessToken]:
    """Create a scoped token. Returns (plaintext, row). The plaintext is never stored."""
    plain = new_token(24)
    row = AccessToken(token_hash=sha256_hex(plain), kind=kind.value, doctor_id=doctor_id, patient_id=patient_id,
                      encounter_id=encounter_id, parent_id=parent_id, max_uses=max_uses, label=label,
                      created_by=created_by, expires_at=utcnow() + ttl, meta_json=meta or {})
    db.add(row)
    db.flush()
    return plain, row


def resolve_token(db: Session, plain: str | None, *, kind: TokenKind, count_use: bool = False) -> AccessToken | None:
    if not plain or len(plain) > 200:
        return None
    row = db.scalar(select(AccessToken).where(AccessToken.token_hash == sha256_hex(plain)))
    now = utcnow()
    if row is None or row.kind != kind.value or row.revoked_at is not None or row.expires_at <= now:
        return None
    if row.max_uses and row.use_count >= row.max_uses:
        return None
    if count_use:
        row.use_count += 1
        row.last_used_at = now
    return row


def revoke_token(db: Session, token_id: str) -> None:
    row = db.get(AccessToken, token_id)
    if row and row.revoked_at is None:
        row.revoked_at = utcnow()
        db.flush()
