from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from server.db import migrate
from server.db.models import AuditEvent, Patient, User
from server.domain.enums import TokenKind
from server.domain.ids import new_id, utcnow
from server.security import audit
from server.security.audit import Actor
from server.security.auth import (AuthError, issue_token, login, logout, password_problem, resolve_session,
                                  resolve_token, revoke_all_sessions, revoke_token)
from server.security.permissions import ROLE_PERMS, can


# ------------------------------------------------------------------ migrations
def test_migrations_apply_and_are_idempotent(db_engine, settings_env):
    assert migrate.current_version(db_engine) >= 1
    assert migrate.migrate(db_engine, settings_env.db_path, settings_env.backups_dir) == []


def test_foreign_keys_enforced(db):
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO patient_allergies(id,patient_id,allergen,reaction,severity,source,confirmed,active,created_at) "
                        "VALUES('a','nope','x','','','patient',0,1,CURRENT_TIMESTAMP)"))
        db.commit()
    db.rollback()


def test_status_check_constraints(db):
    from server.db.models import Appointment
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO encounters(id,encounter_no,patient_id,status,source,visit_type,inputs_version,inputs_hash,row_version,created_at,updated_at,chief_complaint,hpi,past_history,personal_family_history,physical_exam,symptoms_json,current_meds_json,vitals_json,special_status_json,tcm_four_json,patient_reported_json,patient_summary_json,created_by,cancel_reason) "
                        "VALUES('e','E1','p','BOGUS','staff','initial',1,'',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'','','','','','[]','[]','{}','{}','{}','{}','{}','','')"))
        db.commit()
    db.rollback()


# ------------------------------------------------------------------ auth
def test_login_success_and_session_roundtrip(db, make_user):
    u = make_user("dr-a", password="Secret123")
    r = login(db, "dr-a", "Secret123", ip="1.2.3.4")
    assert r.user.id == u.id and r.token
    assert resolve_session(db, r.token).id == u.id
    logout(db, r.token)
    assert resolve_session(db, r.token) is None


def test_login_wrong_password_and_unknown_user_same_error(db, make_user):
    make_user("dr-b", password="Secret123")
    with pytest.raises(AuthError) as e1:
        login(db, "dr-b", "wrong")
    with pytest.raises(AuthError) as e2:
        login(db, "nobody", "wrong")
    assert e1.value.code == e2.value.code == "bad_credentials"
    assert e1.value.message == e2.value.message          # no user enumeration


def test_account_lockout_after_repeated_failures(db, make_user, settings_env):
    make_user("dr-c", password="Secret123")
    for _ in range(settings_env.login_max_failures):
        with pytest.raises(AuthError):
            login(db, "dr-c", "bad")
    with pytest.raises(AuthError) as e:
        login(db, "dr-c", "Secret123")                    # even the right password is refused while locked
    assert e.value.code == "account_locked" and e.value.status == 429


def test_disabled_user_cannot_login(db, make_user):
    make_user("dr-d", password="Secret123", active=False)
    with pytest.raises(AuthError):
        login(db, "dr-d", "Secret123")


def test_session_revoked_on_deactivation_and_revoke_all(db, make_user):
    u = make_user("dr-e", password="Secret123")
    t1 = login(db, "dr-e", "Secret123").token
    t2 = login(db, "dr-e", "Secret123").token
    assert revoke_all_sessions(db, u.id, except_token=t2) == 1
    assert resolve_session(db, t1) is None and resolve_session(db, t2) is not None
    u.active = False
    db.commit()
    assert resolve_session(db, t2) is None


def test_session_idle_timeout(db, make_user, settings_env):
    make_user("dr-f", password="Secret123")
    r = login(db, "dr-f", "Secret123")
    from server.db.models import AuthSession
    from server.domain.ids import sha256_hex
    s = db.get(AuthSession, sha256_hex(r.token))
    s.last_seen_at = utcnow() - timedelta(minutes=settings_env.session_idle_minutes + 5)
    db.commit()
    assert resolve_session(db, r.token) is None


def test_only_token_hash_is_stored(db, make_user):
    r = login(db, make_user("dr-g", password="Secret123").username, "Secret123")
    from server.db.models import AuthSession
    assert db.scalar(select(AuthSession.id).where(AuthSession.id == r.token)) is None


def test_password_policy():
    assert password_problem("short1") and password_problem("12345678") and password_problem("abcdefgh")
    assert password_problem("abcd1234") is None


# ------------------------------------------------------------------ scoped patient tokens
def test_access_token_scope_expiry_revoke_and_uses(db, make_user):
    doc = make_user("dr-h")
    plain, row = issue_token(db, kind=TokenKind.INTAKE_LINK, ttl=timedelta(hours=1), doctor_id=doc.id, max_uses=2)
    db.commit()
    assert resolve_token(db, plain, kind=TokenKind.INTAKE_LINK, count_use=True) is not None
    assert resolve_token(db, plain, kind=TokenKind.PORTAL) is None            # wrong kind
    assert resolve_token(db, plain, kind=TokenKind.INTAKE_LINK, count_use=True) is not None
    assert resolve_token(db, plain, kind=TokenKind.INTAKE_LINK) is None       # max_uses reached
    plain2, row2 = issue_token(db, kind=TokenKind.INTAKE_LINK, ttl=timedelta(hours=1), doctor_id=doc.id)
    db.commit()
    revoke_token(db, row2.id)
    assert resolve_token(db, plain2, kind=TokenKind.INTAKE_LINK) is None
    plain3, row3 = issue_token(db, kind=TokenKind.PORTAL, ttl=timedelta(seconds=-1))
    db.commit()
    assert resolve_token(db, plain3, kind=TokenKind.PORTAL) is None           # expired
    assert resolve_token(db, "garbage", kind=TokenKind.PORTAL) is None


# ------------------------------------------------------------------ permission matrix
def test_permission_matrix_separation_of_duties():
    assert can("DOCTOR", "rx.sign") and can("DOCTOR", "record.finalize")
    assert not can("ADMIN", "rx.sign") and not can("ADMIN", "record.finalize") and not can("ADMIN", "dx.review")
    assert not can("NURSE", "rx.sign") and not can("NURSE", "dx.review") and not can("NURSE", "plan.confirm")
    assert can("PHARMACIST", "pharmacy.dispense") and not can("PHARMACIST", "encounter.read")
    assert not can("DOCTOR", "pharmacy.dispense") and not can("DOCTOR", "user.manage")
    assert not can("GHOST", "rx.read")
    assert set(ROLE_PERMS) == {"DOCTOR", "NURSE", "PHARMACIST", "ADMIN"}


# ------------------------------------------------------------------ audit chain
def test_audit_chain_verifies_then_detects_tampering(db):
    a = Actor(kind="staff", id="u1", name="张医生", role="DOCTOR")
    for i in range(5):
        audit.record(db, a, "test.action", entity_type="x", entity_id=str(i), summary=f"event {i}")
    db.commit()
    assert audit.verify_chain(db) == {"ok": True, "checked": 5, "first_bad_id": None, "reason": ""}
    # in-place edit
    ev = db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()[2]
    db.execute(text("UPDATE audit_events SET summary='tampered' WHERE id=:i"), {"i": ev.id})
    db.commit()
    res = audit.verify_chain(db)
    assert res["ok"] is False and res["first_bad_id"] == ev.id


def test_audit_chain_detects_deleted_row(db):
    a = Actor.system()
    ids = [audit.record(db, a, "t", summary=str(i)).id for i in range(4)]
    db.commit()
    db.execute(text("DELETE FROM audit_events WHERE id=:i"), {"i": ids[1]})
    db.commit()
    res = audit.verify_chain(db)
    assert res["ok"] is False
