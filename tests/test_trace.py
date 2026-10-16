from app.services.inspection_import import import_inspection_file
from tests.conftest import cmm, run_steps


def test_forward_trace_finds_every_implant_from_a_heat(client, auth):
    body = client.get("/api/v1/trace/heat/H1", headers=auth("op1")).json()
    assert [o["order_no"] for o in body["orders"]] == ["WO-1", "WO-2"]
    assert body["total_units"] == 5
    assert "WO-3-0001" not in str(body)  # different heat


def test_forward_trace_counts_failed_inspections(client, seeded, auth):
    import_inspection_file(seeded, "c.csv", cmm("WO-2,,Dia,12,-0.01,0.01,13,2026-10-16T09:00:00"))
    seeded.commit()
    orders = {
        o["order_no"]: o
        for o in client.get("/api/v1/trace/heat/H1", headers=auth("qa")).json()["orders"]
    }
    assert orders["WO-2"]["failed_inspections"] == 1
    assert orders["WO-2"]["status"] == "ON_HOLD"


def test_backward_trace_shows_material_operators_and_own_results(client, seeded, user, auth):
    run_steps(seeded, user("op2"), "WO-1", 1)
    import_inspection_file(
        seeded,
        "c.csv",
        cmm(
            "WO-1,WO-1-0002,Dia,12,-0.01,0.01,12.001,2026-10-16T09:00:00",
            "WO-1,WO-1-0003,Dia,12,-0.01,0.01,12.002,2026-10-16T09:01:00",
            "WO-1,,Surface finish Ra,0.2,-0.2,0,0.15,2026-10-16T09:02:00",
        ),
    )
    seeded.commit()

    body = client.get("/api/v1/trace/serial/WO-1-0002", headers=auth("qa")).json()
    assert body["material"]["heat_number"] == "H1"
    assert body["order"]["steps"][0]["logs"][0]["operator"] == "op2"
    assert body["order"]["steps"][0]["logs"][0]["machine"] == "M1"
    # Its own result plus the batch-level one, not WO-1-0003's.
    assert sorted(i["characteristic"] for i in body["inspections"]) == ["Dia", "Surface finish Ra"]


def test_unknown_serial_404(client, auth):
    assert client.get("/api/v1/trace/serial/NOPE", headers=auth("qa")).status_code == 404
