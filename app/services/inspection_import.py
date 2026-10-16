"""Import measurement results exported by inspection machines (e.g. a CMM) as CSV.

Rules:
- The whole file is validated first; one bad row rejects the file (no partial loads).
- Each file is identified by its SHA-256, so re-dropping the same file is a no-op.
- Any out-of-tolerance value fails the order's Inspection step and puts the order ON_HOLD
  for a Quality decision.

Expected columns:
    order_no,serial_no,characteristic,nominal,lower_tol,upper_tol,measured,measured_at
"""

import csv
import hashlib
import io
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import audit_reason
from app.errors import ValidationFailed
from app.models import (
    CLOSED_STATUSES,
    INSPECTION_STEP,
    ImportedFile,
    InspectionResult,
    OrderStatus,
    RouteStep,
    StepStatus,
    Unit,
    WorkOrder,
    utcnow,
)
from app.schemas import ImportSummary

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "order_no",
    "serial_no",
    "characteristic",
    "nominal",
    "lower_tol",
    "upper_tol",
    "measured",
    "measured_at",
]
MAX_ROWS = 50_000


@dataclass
class Row:
    order_no: str
    serial_no: str | None
    characteristic: str
    nominal: Decimal
    lower_tol: Decimal
    upper_tol: Decimal
    measured: Decimal
    measured_at: datetime

    @property
    def passed(self) -> bool:
        # Decimal avoids float rounding at the tolerance limit: a value exactly on the limit passes.
        return self.nominal + self.lower_tol <= self.measured <= self.nominal + self.upper_tol


def _timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip())
    # CMM exports carry local plant time without an offset; we store UTC.
    # TODO: make the plant time zone configurable (Europe/Dublin) and convert here.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_csv(text: str) -> list[Row]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValidationFailed(f"Missing columns: {', '.join(missing)}")

    rows, errors = [], []
    for line_no, raw in enumerate(reader, start=2):
        if line_no > MAX_ROWS + 1:
            raise ValidationFailed(f"File has more than {MAX_ROWS} rows; split it")
        try:
            row = Row(
                order_no=raw["order_no"].strip(),
                serial_no=raw["serial_no"].strip() or None,
                characteristic=raw["characteristic"].strip(),
                nominal=Decimal(raw["nominal"]),
                lower_tol=Decimal(raw["lower_tol"]),
                upper_tol=Decimal(raw["upper_tol"]),
                measured=Decimal(raw["measured"]),
                measured_at=_timestamp(raw["measured_at"]),
            )
        except (InvalidOperation, ValueError, TypeError, AttributeError):
            errors.append(f"line {line_no}: unreadable value in {dict(raw)}")
            continue
        if not row.order_no or not row.characteristic:
            errors.append(f"line {line_no}: order_no and characteristic are required")
        elif not all(
            v.is_finite() for v in (row.nominal, row.lower_tol, row.upper_tol, row.measured)
        ):
            errors.append(f"line {line_no}: numbers must be finite")
        elif row.lower_tol > row.upper_tol:
            errors.append(f"line {line_no}: lower_tol is greater than upper_tol")
        else:
            rows.append(row)

    if errors:
        raise ValidationFailed("File rejected; no rows were imported", errors[:50])
    if not rows:
        raise ValidationFailed("File has no data rows")
    return rows


def import_inspection_file(db: Session, filename: str, content: bytes) -> ImportSummary:
    digest = hashlib.sha256(content).hexdigest()
    if db.scalar(select(ImportedFile).where(ImportedFile.sha256 == digest)):
        return ImportSummary(filename=filename, status="duplicate")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed("File is not UTF-8 text") from exc
    rows = parse_csv(text)

    order_nos = {r.order_no for r in rows}
    orders = {
        o.order_no: o
        for o in db.scalars(
            select(WorkOrder)
            .where(WorkOrder.order_no.in_(order_nos))
            .options(selectinload(WorkOrder.steps).selectinload(RouteStep.logs))
        )
    }
    errors = [f"unknown work order {n}" for n in sorted(order_nos - orders.keys())]
    errors += [
        f"work order {o.order_no} is {o.status.value}"
        for o in orders.values()
        if o.status in CLOSED_STATUSES
    ]
    serials = {r.serial_no for r in rows if r.serial_no}
    owner = dict(
        db.execute(
            select(Unit.serial_no, WorkOrder.order_no)
            .join(Unit.work_order)
            .where(Unit.serial_no.in_(serials))
        ).all()
    )
    for r in rows:
        if r.serial_no and owner.get(r.serial_no) != r.order_no:
            errors.append(f"serial {r.serial_no} does not belong to {r.order_no}")
    if errors:
        raise ValidationFailed("File rejected; no rows were imported", sorted(set(errors))[:50])

    record = ImportedFile(
        filename=filename,
        sha256=digest,
        row_count=len(rows),
        imported_by=db.info.get("user", "unknown"),
    )
    db.add(record)
    for r in rows:
        db.add(
            InspectionResult(
                work_order=orders[r.order_no],
                serial_no=r.serial_no,
                characteristic=r.characteristic,
                nominal=float(r.nominal),
                lower_tol=float(r.lower_tol),
                upper_tol=float(r.upper_tol),
                measured=float(r.measured),
                passed=r.passed,
                measured_at=r.measured_at,
                imported_file=record,
            )
        )
    db.flush()  # write results before the status changes, so each audit row gets its own reason

    failed_orders = sorted({r.order_no for r in rows if not r.passed})
    for order_no in failed_orders:
        order = orders[order_no]
        with audit_reason(db, f"Out-of-tolerance result in {filename}"):
            order.status = OrderStatus.ON_HOLD
            for step in order.steps:
                if step.name == INSPECTION_STEP and step.status != StepStatus.DONE:
                    step.status = StepStatus.FAILED
                    for entry in step.logs:  # abort a running inspection
                        if entry.finished_at is None:
                            entry.finished_at = utcnow()
                            entry.finished_by = "system:on-hold"
            db.flush()
        log.warning("Order %s put ON_HOLD after failed inspection (%s)", order_no, filename)

    return ImportSummary(
        filename=filename,
        status="imported",
        rows=len(rows),
        failed_rows=sum(not r.passed for r in rows),
        orders_on_hold=failed_orders,
    )


def process_inbox(
    db_factory: Callable[[], Session],
    inbox: Path,
    on_hold: Callable[[list[str], str], None] | None = None,
) -> list[ImportSummary]:
    """Import every CSV waiting in `inbox`, then move it to processed/ or failed/.

    Each file gets its own transaction, so one bad file never blocks the others.
    """
    processed, failed = inbox / "processed", inbox / "failed"
    processed.mkdir(parents=True, exist_ok=True)
    failed.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    results = []
    for path in sorted(inbox.glob("*.csv")):
        with db_factory() as db:
            db.info["user"] = "system:inbox-watcher"
            try:
                summary = import_inspection_file(db, path.name, path.read_bytes())
                db.commit()
            except ValidationFailed as exc:
                db.rollback()
                target = failed / f"{stamp}_{path.name}"
                shutil.move(path, target)
                target.with_suffix(".error.txt").write_text(
                    "\n".join([str(exc), *exc.details]), encoding="utf-8"
                )
                log.error("Rejected %s: %s", path.name, exc)
                continue
            except Exception:
                db.rollback()
                log.exception("Unexpected error importing %s; will retry next scan", path.name)
                continue
        shutil.move(path, processed / f"{stamp}_{path.name}")
        results.append(summary)
        log.info("Imported %s: %s", path.name, summary.model_dump())
        if summary.orders_on_hold and on_hold:
            on_hold(summary.orders_on_hold, path.name)
    return results
