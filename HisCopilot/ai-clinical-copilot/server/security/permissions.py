"""Role → permission matrix (the single source of truth for authorisation).

Endpoints declare the permission they need (`Depends(require("rx.sign"))`); nothing in the API layer
checks role names directly. The frontend receives the caller's permission list from /auth/me and uses it
only to hide/disable UI — enforcement is always server-side.

Ownership rule (applied in services, not here): a DOCTOR may only touch encounters assigned to them or
unassigned ones in the waiting pool; ADMIN may read everything but cannot sign prescriptions or finalise
records (separation of duties); PHARMACIST sees only signed prescriptions (+ minimal patient/allergy info);
NURSE can register and collect facts but cannot make diagnoses or sign.
"""
from __future__ import annotations

from server.domain.enums import Role

P = {
    # --- patients / registration
    "patient.read", "patient.write", "patient.merge", "appointment.manage",
    # --- encounter workspace
    "encounter.read", "encounter.create", "encounter.write_facts", "encounter.claim",
    "analysis.run", "dx.review", "plan.write", "plan.confirm",
    "rx.read", "rx.write", "rx.sign", "rx.void",
    "record.read", "record.write", "record.finalize", "record.amend", "record.print",
    # --- pharmacy
    "pharmacy.queue", "pharmacy.review", "pharmacy.dispense", "catalog.read", "catalog.write",
    # --- follow-up
    "followup.read", "followup.write", "followup.reply",
    # --- admin
    "user.manage", "audit.read", "settings.manage", "stats.read", "job.read", "job.manage", "intake.link",
    # --- 医师经验画像（风格学习）：read = 浏览/选用；write = 导入与管理自己的画像（细粒度归属在服务层判断）；admin = 共享备案/代建/外发开关
    "style.read", "style.write", "style.admin",
}

_DOCTOR = {
    "style.read", "style.write",
    "patient.read", "patient.write", "appointment.manage",
    "encounter.read", "encounter.create", "encounter.write_facts", "encounter.claim",
    "analysis.run", "dx.review", "plan.write", "plan.confirm",
    "rx.read", "rx.write", "rx.sign", "rx.void",
    "record.read", "record.write", "record.finalize", "record.amend", "record.print",
    "catalog.read", "followup.read", "followup.write", "followup.reply", "stats.read", "job.read", "intake.link",
}
_NURSE = {
    "patient.read", "patient.write", "appointment.manage",
    "encounter.read", "encounter.create", "encounter.write_facts",
    "rx.read", "record.read", "record.print", "catalog.read", "followup.read", "intake.link", "job.read",
}
_PHARMACIST = {
    "rx.read", "pharmacy.queue", "pharmacy.review", "pharmacy.dispense", "catalog.read", "catalog.write",
    "record.print",
}
_ADMIN = {
    "patient.read", "patient.merge", "encounter.read", "rx.read", "record.read", "record.print", "catalog.read",
    "catalog.write", "followup.read", "user.manage", "audit.read", "settings.manage", "stats.read",
    "job.read", "job.manage", "appointment.manage", "intake.link",
    "style.read", "style.write", "style.admin",      # ADMIN 可代建画像并备案共享，但不能审核临床条目（职责分离，见 style/service.py）
}

ROLE_PERMS: dict[str, frozenset[str]] = {
    Role.DOCTOR.value: frozenset(_DOCTOR),
    Role.NURSE.value: frozenset(_NURSE),
    Role.PHARMACIST.value: frozenset(_PHARMACIST),
    Role.ADMIN.value: frozenset(_ADMIN),
}

for _r, _perms in ROLE_PERMS.items():           # guard against typos in the matrix
    unknown = set(_perms) - P
    assert not unknown, f"{_r}: unknown permissions {unknown}"


def perms_for(role: str) -> frozenset[str]:
    return ROLE_PERMS.get(role, frozenset())


def can(role: str, permission: str) -> bool:
    return permission in perms_for(role)
