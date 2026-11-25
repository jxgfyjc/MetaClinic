from fastapi import APIRouter

from server.api import auth, encounters, followup_admin, patient_portal, patients, records, style, treatment

router = APIRouter()
for m in (auth, patients, encounters, treatment, records, followup_admin, patient_portal, style):
    router.include_router(m.router)
