import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import Quality, current_user
from app.models import AuditLog, User
from app.schemas import (
    AuditOut,
    BackwardTrace,
    DispositionIn,
    DispositionOut,
    ForwardTrace,
    ImportSummary,
    InspectionOut,
    Page,
    WorkOrderSummary,
)
from app.services import production, quality, trace
from app.services.common import paginate
from app.services.inspection_import import import_inspection_file
from app.services.notifications import notify_hold

router = APIRouter(tags=["quality"])


@router.post("/inspections/import", response_model=ImportSummary)
async def import_inspections(
    file: UploadFile,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(Quality),
):
    limit = settings.max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"File larger than {settings.max_upload_mb} MB")
    filename = (file.filename or "upload.csv")[:255]
    summary = import_inspection_file(db, filename, content)
    db.commit()
    if summary.orders_on_hold:
        background.add_task(notify_hold, summary.orders_on_hold, filename)
    return summary


@router.get("/work-orders/{order_no}/inspections", response_model=list[InspectionOut])
def order_inspections(
    order_no: str, db: Session = Depends(get_db), _: User = Depends(current_user)
):
    return production.get_order(db, order_no).inspections


@router.get("/quality/holds", response_model=list[WorkOrderSummary])
def holds(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return quality.hold_queue(db)


@router.post("/work-orders/{order_no}/disposition", response_model=DispositionOut, status_code=201)
def dispose(
    order_no: str, data: DispositionIn, db: Session = Depends(get_db), user: User = Depends(Quality)
):
    disposition = quality.dispose(db, user, order_no, data)
    db.commit()
    return disposition


@router.get("/trace/heat/{heat_number}", response_model=ForwardTrace)
def trace_forward(heat_number: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return trace.forward(db, heat_number)


@router.get("/trace/serial/{serial_no}", response_model=BackwardTrace)
def trace_backward(serial_no: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return trace.backward(db, serial_no)


def _parse_time(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"{name}: expected an ISO date/time") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def audit_query(
    table: str | None = None,
    record_id: str | None = None,
    user: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    """Filters arrive from an HTML form too, so blank values mean "no filter"."""
    q = select(AuditLog).order_by(AuditLog.id.desc())
    if table:
        q = q.where(AuditLog.table_name == table)
    if record_id:
        if not record_id.isdigit():
            raise HTTPException(422, "record_id: expected a number")
        q = q.where(AuditLog.record_id == int(record_id))
    if user:
        q = q.where(AuditLog.changed_by == user.strip())
    if start := _parse_time(since, "since"):
        q = q.where(AuditLog.at >= start)
    if end := _parse_time(until, "until"):
        q = q.where(AuditLog.at <= end)
    return q


@router.get("/audit", response_model=Page[AuditOut])
def audit_log(
    q=Depends(audit_query),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(Quality),
):
    return paginate(db, q, limit, offset)


@router.get("/audit/export.csv")
def audit_export(q=Depends(audit_query), db: Session = Depends(get_db), _: User = Depends(Quality)):
    columns = [
        "id",
        "at",
        "changed_by",
        "action",
        "table_name",
        "record_id",
        "field",
        "old_value",
        "new_value",
        "reason",
    ]

    # Read everything now: the DB session closes before a streamed response finishes.
    entries = db.scalars(q.limit(200_000)).all()

    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for entry in entries:
            writer.writerow([getattr(entry, c) for c in columns])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
        yield buf.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
