import pytest

from app.errors import Conflict, NotFound, PermissionDenied
from app.models import OrderStatus, StepStatus
from app.schemas import MachineUpdate, WorkOrderIn
from app.services import masterdata, production
from tests.conftest import ROUTE, cmm, run_steps


def test_work_order_copies_part_route_and_allocates_serials(seeded):
    order = production.get_order(seeded, "WO-1")
    assert [s.name for s in order.steps] == ROUTE
    assert [u.serial_no for u in order.units] == ["WO-1-0001", "WO-1-0002", "WO-1-0003"]


def test_unknown_part_or_heat_rejected(seeded):
    with pytest.raises(NotFound):
        production.create_work_order(
            seeded, WorkOrderIn(order_no="WO-9", part_number="NOPE", quantity=1, heat_number="H1")
        )
    with pytest.raises(NotFound):
        production.create_work_order(
            seeded, WorkOrderIn(order_no="WO-9", part_number="P1", quantity=1, heat_number="NOPE")
        )


def test_route_is_locked_once_orders_exist(seeded, user):
    with pytest.raises(Conflict, match="new part revision"):
        masterdata.replace_route(seeded, user("sup"), "P1", ["Turning"])


def test_steps_must_run_in_order(seeded, user):
    with pytest.raises(Conflict, match="Finish earlier steps first"):
        production.start_step(seeded, user("op1"), "WO-1", 2, "M1")


def test_machine_must_exist_and_be_in_service(seeded, user):
    with pytest.raises(NotFound):
        production.start_step(seeded, user("op1"), "WO-1", 1, "NOPE")
    masterdata.update_machine(seeded, user("sup"), "M1", MachineUpdate(active=False))
    with pytest.raises(Conflict, match="out of service"):
        production.start_step(seeded, user("op1"), "WO-1", 1, "M1")


def test_quantities_flow_from_step_to_step(seeded, user):
    op = user("op1")
    production.start_step(seeded, op, "WO-3", 1, "M1")
    production.finish_step(seeded, op, "WO-3", 1, 3, 1)  # 4 in -> 3 good
    production.start_step(seeded, op, "WO-3", 2, "M2")
    with pytest.raises(Conflict, match="must equal the 3 parts"):
        production.finish_step(seeded, op, "WO-3", 2, 4, 0)
    production.finish_step(seeded, op, "WO-3", 2, 3, 0)


def test_only_the_operator_or_a_supervisor_can_finish(seeded, user):
    production.start_step(seeded, user("op1"), "WO-1", 1, "M1")
    with pytest.raises(PermissionDenied):
        production.finish_step(seeded, user("op2"), "WO-1", 1, 3, 0)
    production.finish_step(seeded, user("sup"), "WO-1", 1, 3, 0)
    log = production.get_order(seeded, "WO-1").steps[0].logs[0]
    assert (log.operator, log.finished_by) == ("op1", "sup")


def test_inspection_needs_imported_results(seeded, user):
    from app.services.inspection_import import import_inspection_file

    op = user("op1")
    run_steps(seeded, op, "WO-1", 2)
    production.start_step(seeded, op, "WO-1", 3, "CMM")
    with pytest.raises(Conflict, match="Import inspection results"):
        production.finish_step(seeded, op, "WO-1", 3, 3, 0)

    import_inspection_file(
        seeded, "ok.csv", cmm("WO-1,,Dia,12,-0.01,0.01,12.001,2026-10-16T09:00:00")
    )
    production.finish_step(seeded, op, "WO-1", 3, 3, 0)


def test_full_route_completes_order(seeded, user):
    from app.services.inspection_import import import_inspection_file

    import_inspection_file(seeded, "ok.csv", cmm("WO-2,,Dia,12,-0.01,0.01,12,2026-10-16T09:00:00"))
    run_steps(seeded, user("op1"), "WO-2", len(ROUTE))
    order = production.get_order(seeded, "WO-2")
    assert order.status == OrderStatus.COMPLETE
    assert all(s.status == StepStatus.DONE for s in order.steps)
    with pytest.raises(Conflict, match="COMPLETE"):
        production.start_step(seeded, user("op1"), "WO-2", 1, "M1")


def test_api_flow_and_errors(client, auth):
    op = auth("op1")
    base = "/api/v1/work-orders/WO-3/steps/1"
    r = client.post(f"{base}/start", json={"machine": "M1"}, headers=op)
    assert r.status_code == 200
    assert r.json()["status"] == "IN_PROGRESS"
    assert r.json()["steps"][0]["logs"][0]["operator"] == "op1"

    r = client.post(f"{base}/finish", json={"qty_good": 4, "qty_scrap": 0}, headers=op)
    assert r.status_code == 200
    assert client.get("/api/v1/work-orders/NOPE", headers=op).status_code == 404
    r = client.post("/api/v1/work-orders/WO-3/steps/3/start", json={"machine": "M1"}, headers=op)
    assert r.status_code == 409
    r = client.post(f"{base}/finish", json={"qty_good": -1, "qty_scrap": 0}, headers=op)
    assert r.status_code == 422


def test_release_order_via_api_requires_supervisor(client, auth):
    body = {
        "order_no": "WO-77",
        "part_number": "P2",
        "quantity": 2,
        "heat_number": "H2",
        "due_date": "2026-12-01",
    }
    assert client.post("/api/v1/work-orders", json=body, headers=auth("op1")).status_code == 403
    r = client.post("/api/v1/work-orders", json=body, headers=auth("sup"))
    assert r.status_code == 201
    assert r.json()["due_date"] == "2026-12-01"
    assert client.post("/api/v1/work-orders", json=body, headers=auth("sup")).status_code == 409


def test_list_orders_filters_and_paginates(client, auth):
    h = auth("op1")
    r = client.get("/api/v1/work-orders", params={"search": "H2"}, headers=h).json()
    assert [o["order_no"] for o in r["items"]] == ["WO-3"]
    r = client.get("/api/v1/work-orders", params={"limit": 2}, headers=h).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    r = client.get("/api/v1/work-orders", params={"status": "ON_HOLD"}, headers=h).json()
    assert r["total"] == 0
