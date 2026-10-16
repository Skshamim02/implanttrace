from pathlib import Path

import pytest

from app.errors import ValidationFailed
from app.models import InspectionResult, OrderStatus, StepStatus
from app.services import production
from app.services.inspection_import import import_inspection_file, parse_csv, process_inbox
from tests.conftest import cmm, run_steps


def test_value_exactly_on_limit_passes():
    rows = parse_csv(
        cmm(
            "WO-1,,Dia,12.000,-0.010,0.010,12.010,2026-10-16T09:00:00",
            "WO-1,,Dia,12.000,-0.010,0.010,11.990,2026-10-16T09:00:00",
            "WO-1,,Dia,12.000,-0.010,0.010,12.0101,2026-10-16T09:00:00",
        ).decode()
    )
    assert [r.passed for r in rows] == [True, True, False]


def test_out_of_tolerance_puts_order_on_hold(seeded, user):
    run_steps(seeded, user("op1"), "WO-1", 2)
    production.start_step(seeded, user("op1"), "WO-1", 3, "CMM")
    summary = import_inspection_file(
        seeded,
        "cmm.csv",
        cmm(
            "WO-1,WO-1-0001,Dia,12.000,-0.010,0.010,12.004,2026-10-16T09:00:00",
            "WO-1,WO-1-0002,Dia,12.000,-0.010,0.010,12.020,2026-10-16T09:05:00",
            "WO-2,WO-2-0001,Dia,12.000,-0.010,0.010,12.001,2026-10-16T09:10:00",
        ),
    )
    assert (summary.rows, summary.failed_rows, summary.orders_on_hold) == (3, 1, ["WO-1"])

    wo1 = production.get_order(seeded, "WO-1")
    inspection = wo1.steps[2]
    assert wo1.status == OrderStatus.ON_HOLD
    assert inspection.status == StepStatus.FAILED
    assert inspection.logs[0].finished_by == "system:on-hold"  # running inspection aborted
    assert production.get_order(seeded, "WO-2").status == OrderStatus.RELEASED


def test_same_file_twice_is_ignored(seeded):
    content = cmm("WO-1,,Dia,12,-0.01,0.01,12,2026-10-16T09:00:00")
    assert import_inspection_file(seeded, "a.csv", content).status == "imported"
    assert import_inspection_file(seeded, "a-copy.csv", content).status == "duplicate"
    assert seeded.query(InspectionResult).count() == 1


def test_one_bad_row_rejects_whole_file(seeded):
    with pytest.raises(ValidationFailed) as exc:
        import_inspection_file(
            seeded,
            "bad.csv",
            cmm(
                "WO-1,,Dia,12,-0.01,0.01,12.001,2026-10-16T09:00:00",
                "WO-1,,Dia,12,-0.01,0.01,abc,2026-10-16T09:00:00",
            ),
        )
    assert "line 3" in exc.value.details[0]
    assert seeded.query(InspectionResult).count() == 0


@pytest.mark.parametrize(
    "row, message",
    [
        ("WO-404,,Dia,12,-0.01,0.01,12,2026-10-16T09:00:00", "unknown work order WO-404"),
        ("WO-1,WO-2-0001,Dia,12,-0.01,0.01,12,2026-10-16T09:00:00", "does not belong to WO-1"),
        ("WO-1,,Dia,12,0.01,-0.01,12,2026-10-16T09:00:00", "lower_tol is greater"),
        ("WO-1,,Dia,12,-0.01,0.01,NaN,2026-10-16T09:00:00", "finite"),
        ("WO-1,,Dia,12,-0.01,0.01,12,yesterday", "unreadable"),
    ],
)
def test_invalid_content_rejected(seeded, row, message):
    with pytest.raises(ValidationFailed) as exc:
        import_inspection_file(seeded, "x.csv", cmm(row))
    assert any(message in d for d in exc.value.details)


def test_missing_columns_and_binary_rejected(seeded):
    with pytest.raises(ValidationFailed, match="Missing columns"):
        import_inspection_file(seeded, "x.csv", b"order_no,measured\nWO-1,12\n")
    with pytest.raises(ValidationFailed, match="UTF-8"):
        import_inspection_file(seeded, "x.csv", b"\xff\xfe\x00\x01")


def test_inbox_moves_files_and_alerts(tmp_path: Path, seeded, session_factory):
    (tmp_path / "good.csv").write_bytes(cmm("WO-1,,Dia,12,-0.01,0.01,13,2026-10-16T09:00:00"))
    (tmp_path / "bad.csv").write_bytes(cmm("WO-1,,Dia,12,-0.01,0.01,oops,2026-10-16T09:00:00"))
    alerts = []

    results = process_inbox(session_factory, tmp_path, on_hold=lambda o, f: alerts.append((o, f)))

    assert [r.filename for r in results] == ["good.csv"]
    assert alerts == [(["WO-1"], "good.csv")]
    assert len(list((tmp_path / "processed").glob("*_good.csv"))) == 1
    assert len(list((tmp_path / "failed").glob("*_bad.csv"))) == 1
    error_report = next((tmp_path / "failed").glob("*.error.txt")).read_text()
    assert "line 2" in error_report


def test_upload_endpoint_requires_quality_and_size_limit(client, auth):
    content = cmm("WO-3,,Dia,12,-0.01,0.01,12.5,2026-10-16T09:00:00")
    files = {"file": ("cmm.csv", content)}
    assert (
        client.post("/api/v1/inspections/import", files=files, headers=auth("op1")).status_code
        == 403
    )

    r = client.post("/api/v1/inspections/import", files=files, headers=auth("qa"))
    assert r.status_code == 200
    assert r.json()["orders_on_hold"] == ["WO-3"]

    r = client.post(
        "/api/v1/inspections/import",
        files={"file": ("x.csv", b"nonsense\n1\n")},
        headers=auth("qa"),
    )
    assert r.status_code == 422
