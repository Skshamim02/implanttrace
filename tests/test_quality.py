import pytest

from app.errors import Conflict, PermissionDenied
from app.models import Decision, ElectronicSignature, OrderStatus, StepStatus
from app.schemas import DispositionIn
from app.services import production, quality
from app.services.inspection_import import import_inspection_file
from tests.conftest import PASSWORD, cmm, run_steps

FAIL = "WO-1,WO-1-0002,Taper,12,-0.01,0.01,12.03,2026-10-16T09:00:00"
PASS = "WO-1,WO-1-0002,Taper,12,-0.01,0.01,12.001,2026-10-16T11:00:00"


@pytest.fixture
def held(seeded, user):
    """WO-1 through Milling, then a failed CMM import during Inspection."""
    run_steps(seeded, user("op1"), "WO-1", 2)
    production.start_step(seeded, user("op1"), "WO-1", 3, "CMM")
    import_inspection_file(seeded, "fail.csv", cmm(FAIL))
    seeded.commit()
    return seeded


def disposition(decision: Decision, password: str = PASSWORD) -> DispositionIn:
    return DispositionIn(
        decision=decision,
        ncr_number="NCR-1",
        notes="Root cause found and contained.",
        password=password,
    )


def test_hold_blocks_production(held, user):
    with pytest.raises(Conflict, match="ON HOLD"):
        production.start_step(held, user("op1"), "WO-1", 3, "CMM")
    assert [o.order_no for o in quality.hold_queue(held)] == ["WO-1"]


def test_wrong_password_does_not_sign(held, user):
    with pytest.raises(PermissionDenied, match="Signature failed"):
        quality.dispose(held, user("qa"), "WO-1", disposition(Decision.REWORK, "wrong"))


def test_rework_then_passing_reinspection_completes(held, user):
    d = quality.dispose(held, user("qa"), "WO-1", disposition(Decision.REWORK))
    held.commit()
    assert d.signature.username == "qa"
    assert d.signature.meaning == "Approved rework and re-inspection"

    order = production.get_order(held, "WO-1")
    assert order.status == OrderStatus.IN_PROGRESS
    assert order.steps[2].status == StepStatus.PENDING

    op = user("op1")
    production.start_step(held, op, "WO-1", 3, "CMM")
    with pytest.raises(Conflict, match="Import inspection results"):
        production.finish_step(held, op, "WO-1", 3, 3, 0)  # old failure no longer counts
    import_inspection_file(held, "recheck.csv", cmm(PASS))
    production.finish_step(held, op, "WO-1", 3, 3, 0)
    production.start_step(held, op, "WO-1", 4, "PK")
    production.finish_step(held, op, "WO-1", 4, 3, 0)
    assert production.get_order(held, "WO-1").status == OrderStatus.COMPLETE


def test_use_as_is_closes_inspection_and_quantities_carry_on(held, user):
    quality.dispose(held, user("qa"), "WO-1", disposition(Decision.USE_AS_IS))
    order = production.get_order(held, "WO-1")
    assert order.steps[2].status == StepStatus.DONE
    assert production.qty_into_step(order, order.steps[3]) == 3  # from Milling
    production.start_step(held, user("op1"), "WO-1", 4, "PK")


def test_scrap_closes_order(held, user):
    quality.dispose(held, user("qa"), "WO-1", disposition(Decision.SCRAP))
    assert production.get_order(held, "WO-1").status == OrderStatus.SCRAPPED
    with pytest.raises(Conflict, match="not on hold"):
        quality.dispose(held, user("qa"), "WO-1", disposition(Decision.SCRAP))


def test_signature_is_linked_to_its_record(held, user):
    d = quality.dispose(held, user("qa"), "WO-1", disposition(Decision.REWORK))
    sig = held.get(ElectronicSignature, d.signature_id)
    assert (sig.table_name, sig.record_id, sig.full_name) == ("disposition", d.id, "QA")


def test_disposition_api_permissions_and_validation(client, held, auth):
    url = "/api/v1/work-orders/WO-1/disposition"
    body = {
        "decision": "REWORK",
        "ncr_number": "NCR-9",
        "notes": "Re-polish and re-measure all.",
        "password": PASSWORD,
    }
    assert client.post(url, json=body, headers=auth("sup")).status_code == 403
    short = {**body, "notes": "too short"}
    assert client.post(url, json=short, headers=auth("qa")).status_code == 422
    r = client.post(url, json={**body, "password": "x"}, headers=auth("qa"))
    assert r.status_code == 403  # not 401: the session is valid, only the signature failed
    r = client.post(url, json=body, headers=auth("qa"))
    assert r.status_code == 201
    assert r.json()["signature"]["full_name"] == "QA"
    assert client.get("/api/v1/quality/holds", headers=auth("op1")).json() == []
