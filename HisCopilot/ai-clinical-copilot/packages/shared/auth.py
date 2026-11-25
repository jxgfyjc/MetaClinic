"""Authentication module — JWT + bcrypt (updatev2_2 §1).

Uses already-installed python-jose + bcrypt (no new downloads).
Note: passlib 1.7.4 is incompatible with bcrypt 5.x (passlib expects
bcrypt.__about__.__version__), so we use the bcrypt library directly.

- Doctor login: password-verified, JWT in httpOnly cookie "doctor_token".
- Patient token: lightweight JWT issued on encounter creation (source=patient),
  scoped to a single encounter+patient, 24h expiry.

All audit actors must come from the verified token, not the request body.
"""
from __future__ import annotations

import time
from typing import Any

import bcrypt
from fastapi import HTTPException, Request, status
from jose import jwt, JWTError

from .config import get_settings

# Cookie names
DOCTOR_COOKIE = "doctor_token"
PATIENT_COOKIE = "patient_token"


def hash_password(pw: str) -> str:
    """bcrypt hash. Truncate to 72 bytes (bcrypt limit)."""
    pw_bytes = pw.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    if not hashed:
        return False
    pw_bytes = pw.encode("utf-8")[:72]
    try:
        hashed_bytes = hashed.encode("utf-8") if isinstance(hashed, str) else hashed
        return bcrypt.checkpw(pw_bytes, hashed_bytes)
    except Exception:
        return False


def create_access_token(user_id: str, role: str, name: str,
                        extra: dict | None = None) -> str:
    """Doctor JWT. 8h expiry (access_token_expire_minutes)."""
    s = get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "name": name,
        "iat": now,
        "exp": now + s.access_token_expire_minutes * 60,
        "type": "doctor",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_alg)


def create_patient_token(encounter_id: str, patient_id: str) -> str:
    """Patient-scoped JWT. 24h expiry. Only allows access to own encounter."""
    s = get_settings()
    now = int(time.time())
    payload = {
        "sub": patient_id,
        "encounter_id": encounter_id,
        "iat": now,
        "exp": now + 24 * 3600,
        "type": "patient",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_alg)


def decode_token(token: str) -> dict | None:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_alg])
    except JWTError:
        return None


def _extract_token(request: Request, cookie_name: str) -> str | None:
    """Try Authorization: Bearer <token> first, then cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get(cookie_name)


async def get_current_user(request: Request) -> dict | None:
    """FastAPI dependency. Returns {id, name, role} or None.

    Does NOT raise — callers decide whether None is an error (require_doctor)
    or acceptable (patient-accessible endpoints).
    """
    token = _extract_token(request, DOCTOR_COOKIE)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "doctor":
        return None
    return {
        "id": payload.get("sub", ""),
        "name": payload.get("name", ""),
        "role": payload.get("role", ""),
    }


async def require_doctor(request: Request) -> dict:
    """FastAPI dependency: require a logged-in doctor/admin. Raises 401/403."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或令牌已过期，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user["role"] not in ("DOCTOR", "ADMIN", "NURSE"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足，需要医生角色",
        )
    return user


async def get_patient_context(request: Request) -> dict | None:
    """FastAPI dependency for patient-accessible endpoints.
    Returns {patient_id, encounter_id} if a valid patient token exists."""
    token = _extract_token(request, PATIENT_COOKIE)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "patient":
        return None
    return {
        "patient_id": payload.get("sub", ""),
        "encounter_id": payload.get("encounter_id", ""),
    }


def set_doctor_cookie(response, token: str) -> None:
    """Set httpOnly cookie for doctor token."""
    s = get_settings()
    response.set_cookie(
        key=DOCTOR_COOKIE, value=token, httponly=True, samesite="lax",
        max_age=s.access_token_expire_minutes * 60,
        secure=False,  # HTTPS proxy terminates TLS; backend is HTTP
    )


def set_patient_cookie(response, token: str) -> None:
    """Set httpOnly cookie for patient token."""
    response.set_cookie(
        key=PATIENT_COOKIE, value=token, httponly=True, samesite="lax",
        max_age=24 * 3600, secure=False,
    )


def clear_doctor_cookie(response) -> None:
    response.delete_cookie(key=DOCTOR_COOKIE)
