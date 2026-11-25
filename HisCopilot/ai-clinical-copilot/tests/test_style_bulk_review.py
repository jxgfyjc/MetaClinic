"""Batch approval (POST /api/v1/style/profiles/{pid}/entries/bulk-review) & example.md parsing tests."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

import server.jobs.handlers  # noqa: F401
import server.style.jobs  # noqa: F401
from server.db.base import session_scope
from server.db.models import AuditEvent, StyleEntry
from server.style import cases as C, extract as X, induce as I, parse as P
from tests.conftest_http import err, ok
from tests.style_fixtures.synth import corpus
from tests.test_style_flow import U_entry, U_profiles, make_personal, run_import


def _bulk_csv() -> bytes:
    """Mix of 肝阳上亢 (clean, >=3 cases) and 寒湿痹阻 (critical safety flags)."""
    rows = ["姓名,病历号,日期,主诉,舌脉,证型,中医治法,处方,备注"]
    cs = [c for c in corpus() if c["kind"] == "case"]
    gan = [c for c in cs if c["group_key"] == "肝阳上亢"][:6]
    han = [c for c in cs if c["group_key"] == "寒湿痹阻"][:4]
    for i, c in enumerate(gan + han):
        herbs = " ".join(f"{h['name']}{h['dose_g']:g}g" for h in c["herbs"])
        f = c["fields"]
        rows.append(f"患者{i},MZ{20240000 + i},2024-0{1 + i % 9}-1{i % 9},{f['symptoms'][0]},{f['tongue']}{f['pulse']},{f['syndrome']},{f['principle']},"
                    f"\"{f['formula']}加减：{herbs}\",")
    return "\n".join(rows).encode()


def _has_critical(e: dict) -> bool:
    return any(f.get("severity") == "critical" for f in (e.get("flags") or []))


def test_bulk_review_mixed_batch_audit_and_idempotency(app_client, roles):
    dr = roles.as_("dr-001")
    p = make_personal(dr)
    run_import(dr, p["id"], _bulk_csv())
    es = ok(dr.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    clean = [e for e in es if not _has_critical(e)]
    crit = [e for e in es if _has_critical(e)]
    assert len(clean) >= 3 and len(crit) >= 1

    # Pre-approve one and reject one so the batch contains mixed statuses
    ok(dr.post(U_entry(clean[0]["id"], "/review"), json={"action": "approve", "row_version": clean[0]["row_version"]}))
    ok(dr.post(U_entry(clean[1]["id"], "/review"), json={"action": "reject", "row_version": clean[1]["row_version"], "note": "不采纳"}))

    target_clean = clean[2:]
    stale_entry = target_clean[-1]
    items = (
        [{"id": e["id"], "row_version": e["row_version"]} for e in target_clean[:-1]]
        + [{"id": stale_entry["id"], "row_version": stale_entry["row_version"] + 99}]
        + [{"id": crit[0]["id"], "row_version": crit[0]["row_version"]}]
        + [{"id": clean[0]["id"], "row_version": clean[0]["row_version"]}]
        + [{"id": clean[1]["id"], "row_version": clean[1]["row_version"]}]
        + [{"id": "ste_nonexistent", "row_version": 1}]
    )
    res = ok(dr.post(U_profiles(p["id"], "/entries/bulk-review"), json={"action": "approve", "request_id": "req-mix-1", "items": items}))
    assert res["summary"]["requested"] == len(items)
    assert res["summary"]["approved"] == len(target_clean) - 1
    assert res["summary"]["skipped"] == 5

    by_id = {r["id"]: r for r in res["results"]}
    for e in target_clean[:-1]:
        assert by_id[e["id"]]["outcome"] == "approved"
        assert by_id[e["id"]]["review_status"] == "approved"
        assert by_id[e["id"]]["reviewed_by"] is not None
        assert by_id[e["id"]]["reviewed_at"] is not None
    assert by_id[stale_entry["id"]]["code"] == "stale"
    assert by_id[crit[0]["id"]]["code"] == "critical_needs_individual_review"
    assert by_id[clean[0]["id"]]["code"] == "not_pending"
    assert by_id[clean[1]["id"]]["code"] == "not_pending"
    assert by_id["ste_nonexistent"]["code"] == "not_found"

    # Re-posting the exact same payload approves 0
    res2 = ok(dr.post(U_profiles(p["id"], "/entries/bulk-review"), json={"action": "approve", "request_id": "req-mix-1", "items": items}))
    assert res2["summary"]["approved"] == 0

    with session_scope() as s:
        bulk_audits = s.scalars(select(AuditEvent).where(AuditEvent.action == "style.entry.bulk_approve", AuditEvent.entity_id == p["id"])).all()
        assert len(bulk_audits) == 2
        app_audits = s.scalars(select(AuditEvent).where(AuditEvent.action == "style.entry.approve")).all()
        assert len(app_audits) >= len(target_clean) - 1 + 1
        for e in target_clean[:-1]:
            row = s.get(StyleEntry, e["id"])
            assert row.review_status == "approved" and row.reviewed_by and row.reviewed_at


def test_bulk_review_permissions_and_immutable_after_publish(app_client, roles):
    adm = roles.as_("admin")
    dr1 = roles.as_("dr-001")
    dr3 = roles.as_("dr-003")
    dr1_id = ok(dr1.get("/api/v1/auth/me"))["user"]["id"]
    p = ok(adm.post(U_profiles(), json={"kind": "master", "subject_name": "名医代建", "reviewer_id": dr1_id}), 201)["profile"]
    run_import(dr1, p["id"], _bulk_csv())
    es = ok(dr1.get(U_profiles(p["id"], "/entries?version=draft")))["entries"]
    clean = [e for e in es if not _has_critical(e)]
    payload = {"action": "approve", "request_id": "req-perm", "items": [{"id": clean[0]["id"], "row_version": clean[0]["row_version"]}]}

    # Admin cannot clinically review a proxy profile
    err(adm.post(U_profiles(p["id"], "/entries/bulk-review"), json=payload), 403)
    # Another doctor cannot review someone else's unpublished profile
    assert dr3.post(U_profiles(p["id"], "/entries/bulk-review"), json=payload).status_code in (403, 404)

    # Approve all pending entries, publish, then verify published entries return version_immutable
    for e in es:
        if _has_critical(e):
            ok(dr1.post(U_entry(e["id"], "/review"), json={"action": "approve_no_ai", "row_version": e["row_version"]}))
        else:
            ok(dr1.post(U_entry(e["id"], "/review"), json={"action": "approve", "row_version": e["row_version"]}))
    ok(dr1.post(U_profiles(p["id"], "/publish"), json={"note": "v1"}))

    res_pub = ok(dr1.post(U_profiles(p["id"], "/entries/bulk-review"), json=payload))
    assert res_pub["summary"]["approved"] == 0
    assert res_pub["results"][0]["code"] == "version_immutable"


def test_example_md_splitting_lexicon_and_induce():
    p = Path(__file__).resolve().parent / "style_fixtures" / "example_cases.md"
    raw = p.read_bytes()
    ps = P.parse(raw, "example.md")
    rcs = C.cases_from_blocks(ps.blocks, "example.md")
    cases = [r for r in rcs if r.kind == "case"]
    notes = [r for r in rcs if r.kind == "note"]

    assert len(cases) == 40
    assert len(notes) == 40
    assert all(not n.text.lstrip().startswith(("#", "**案")) for n in notes)
    assert all(c.section for c in cases)
    assert all("案" in c.label for c in cases)

    c1_herbs = {h["name"] for h in cases[0].herbs}
    assert {"淫羊藿", "当归", "土鳖虫"}.issubset(c1_herbs)

    # Induce with rule-extracted fields
    case_dicts = []
    for idx, r in enumerate(rcs):
        ex = X.extract_rules(r.text, r.herbs) if r.kind == "case" else {"fields": {}}
        fields = ex["fields"]
        case_dicts.append({
            "id": f"ex_{idx}", "kind": r.kind, "text": r.text, "fields": fields,
            "herbs": r.herbs, "visit_date": r.date, "section": r.section,
            "group_key": X.group_key(fields) if r.kind == "case" else "",
        })
    entries, stats = I.build(case_dicts)
    group_names = {g["group"] for g in stats["groups"]}
    assert "痹证与骨关节病" in group_names
    assert stats["section_fallback"] > 0
    assert len(entries) > 0
