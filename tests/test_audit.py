import pytest
from sqlalchemy import select

from app.audit import AuditReasonRequired, audit_reason
from app.errors import Conflict
from app.models import AuditLog
from app.services import production


def test_every_create_is_logged_with_user(seeded):
    created = seeded.scalars(select(AuditLog).where(AuditLog.action == "CREATE")).all()
    tables = {a.table_name for a in created}
    assert tables >= {"app_user", "machine", "part", "material", "work_order", "route_step", "unit"}
    assert all(a.changed_by == "test" for a in created)


def test_passwords_never_reach_the_audit_log(seeded):
    rows = seeded.scalars(select(AuditLog).where(AuditLog.table_name == "app_user")).all()
    assert rows and all("argon2" not in (r.new_value or "") for r in rows)
    assert all('"password_hash": "***"' in r.new_value for r in rows if r.action == "CREATE")


def test_update_records_old_new_and_reason(seeded, user):
    seeded.info["user"] = "op1"
    production.start_step(seeded, user("op1"), "WO-1", 1, "M1")
    seeded.commit()
    row = seeded.scalar(
        select(AuditLog).where(AuditLog.table_name == "route_step", AuditLog.action == "UPDATE")
    )
    assert (row.field, row.old_value, row.new_value) == ("status", "PENDING", "IN_PROGRESS")
    assert (row.changed_by, row.reason) == ("op1", "op1 started Turning on M1")


def test_silent_edit_is_refused(seeded):
    material = production.get_material(seeded, "H1")
    material.cert_ref = "EDITED"
    with pytest.raises(AuditReasonRequired):
        seeded.flush()
    seeded.rollback()


def test_edit_with_reason_is_allowed(seeded):
    material = production.get_material(seeded, "H1")
    with audit_reason(seeded, "Supplier reissued certificate (NCR-12)"):
        material.cert_ref = "C1-rev2"
        seeded.flush()
    seeded.commit()
    row = seeded.scalar(select(AuditLog).where(AuditLog.field == "cert_ref"))
    assert (row.old_value, row.new_value) == ("C1", "C1-rev2")


def test_audit_log_is_append_only(seeded):
    row = seeded.scalar(select(AuditLog).limit(1))
    row.reason = "tampered"
    with pytest.raises(Conflict, match="append-only"):
        seeded.flush()
    seeded.rollback()
    with pytest.raises(Conflict, match="append-only"):
        seeded.delete(seeded.scalar(select(AuditLog).limit(1)))
        seeded.flush()


def test_audit_api_filters_and_csv(client, auth):
    h = auth("qa")
    r = client.get("/api/v1/audit", params={"table": "material"}, headers=h).json()
    assert r["total"] == 2
    r = client.get("/api/v1/audit", params={"table": "material", "record_id": ""}, headers=h)
    assert r.status_code == 200  # blank form fields mean "no filter"
    assert client.get("/api/v1/audit", params={"since": "garbage"}, headers=h).status_code == 422

    csv = client.get("/api/v1/audit/export.csv", params={"table": "material"}, headers=h)
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("id,at,changed_by")
    assert len(csv.text.splitlines()) == 3
