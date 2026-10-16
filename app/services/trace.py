"""Traceability queries.

Forward:  "Heat H123 is suspect - which implants were made from it?"
Backward: "Show the full history of serial WO-1001-0007."
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.errors import NotFound
from app.models import Disposition, InspectionResult, RouteStep, Unit, WorkOrder
from app.services.production import get_material


def forward(db: Session, heat_number: str) -> dict:
    material = get_material(db, heat_number)
    orders = db.scalars(
        select(WorkOrder)
        .where(WorkOrder.material_id == material.id)
        .options(selectinload(WorkOrder.units), selectinload(WorkOrder.part))
        .order_by(WorkOrder.order_no)
    ).all()
    failed_counts = dict(
        db.execute(
            select(InspectionResult.work_order_id, func.count())
            .where(
                InspectionResult.passed.is_(False),
                InspectionResult.work_order_id.in_([o.id for o in orders]),
            )
            .group_by(InspectionResult.work_order_id)
        ).all()
    )
    out = [
        {
            "order_no": o.order_no,
            "part_number": o.part_number,
            "status": o.status,
            "serial_numbers": [u.serial_no for u in o.units],
            "failed_inspections": failed_counts.get(o.id, 0),
        }
        for o in orders
    ]
    return {
        "heat_number": material.heat_number,
        "grade": material.grade,
        "supplier": material.supplier,
        "cert_ref": material.cert_ref,
        "orders": out,
        "total_units": sum(len(o["serial_numbers"]) for o in out),
    }


def backward(db: Session, serial_no: str) -> dict:
    unit = db.scalar(
        select(Unit)
        .where(Unit.serial_no == serial_no)
        .options(
            selectinload(Unit.work_order)
            .selectinload(WorkOrder.steps)
            .selectinload(RouteStep.logs),
            selectinload(Unit.work_order).selectinload(WorkOrder.material),
            selectinload(Unit.work_order).selectinload(WorkOrder.part),
            selectinload(Unit.work_order)
            .selectinload(WorkOrder.dispositions)
            .selectinload(Disposition.signature),
        )
    )
    if unit is None:
        raise NotFound(f"Serial {serial_no} not found")
    order = unit.work_order
    inspections = db.scalars(
        select(InspectionResult)
        .where(
            InspectionResult.work_order_id == order.id,
            # Batch-level results (no serial) apply to every unit in the order.
            or_(InspectionResult.serial_no == serial_no, InspectionResult.serial_no.is_(None)),
        )
        .order_by(InspectionResult.measured_at, InspectionResult.id)
    ).all()
    return {
        "serial_no": unit.serial_no,
        "order": order,
        "material": order.material,
        "inspections": inspections,
        "dispositions": order.dispositions,
    }
