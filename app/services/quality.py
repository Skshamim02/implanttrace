"""Quality decisions on held orders, signed electronically."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import audit_reason
from app.errors import Conflict, PermissionDenied
from app.models import (
    INSPECTION_STEP,
    Decision,
    Disposition,
    ElectronicSignature,
    InspectionResult,
    OrderStatus,
    StepStatus,
    User,
    WorkOrder,
)
from app.schemas import DispositionIn
from app.security import verify_password
from app.services.production import get_order

MEANINGS = {
    Decision.REWORK: "Approved rework and re-inspection",
    Decision.USE_AS_IS: "Approved use-as-is concession",
    Decision.SCRAP: "Approved scrap of the order",
}


def sign(db: Session, user: User, password: str, meaning: str, record) -> ElectronicSignature:
    """Re-authenticate the signer and bind the signature to a record."""
    if not verify_password(user.password_hash, password):
        raise PermissionDenied("Signature failed: password incorrect")
    sig = ElectronicSignature(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        meaning=meaning,
        table_name=record.__tablename__,
        record_id=record.id,
    )
    db.add(sig)
    db.flush()
    return sig


def hold_queue(db: Session) -> list[WorkOrder]:
    return list(
        db.scalars(
            select(WorkOrder)
            .where(WorkOrder.status == OrderStatus.ON_HOLD)
            .options(
                selectinload(WorkOrder.part),
                selectinload(WorkOrder.material),
                selectinload(WorkOrder.inspections).selectinload(InspectionResult.imported_file),
            )
            .order_by(WorkOrder.order_no)
        )
    )


def dispose(db: Session, user: User, order_no: str, data: DispositionIn) -> Disposition:
    order = get_order(db, order_no)
    if order.status != OrderStatus.ON_HOLD:
        raise Conflict(f"{order_no} is not on hold")

    disposition = Disposition(
        work_order=order, decision=data.decision, ncr_number=data.ncr_number, notes=data.notes
    )
    db.add(disposition)
    db.flush()
    signature = sign(db, user, data.password, MEANINGS[data.decision], disposition)

    inspection = next((s for s in order.steps if s.name == INSPECTION_STEP), None)
    reason = f"{data.decision.value} under {data.ncr_number} signed by {user.username}"
    with audit_reason(db, reason):
        disposition.signature = signature
        if data.decision == Decision.SCRAP:
            order.status = OrderStatus.SCRAPPED
        else:
            order.status = OrderStatus.IN_PROGRESS
            if inspection is not None and inspection.status == StepStatus.FAILED:
                inspection.status = (
                    StepStatus.DONE if data.decision == Decision.USE_AS_IS else StepStatus.PENDING
                )
        db.flush()
    return disposition
