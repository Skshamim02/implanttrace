"""Production and quality KPIs for the dashboard."""

from datetime import date, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CLOSED_STATUSES,
    AuditLog,
    InspectionResult,
    OperationLog,
    OrderStatus,
    RouteStep,
    WorkOrder,
    utcnow,
)

OUTPUT_DAYS = 14
WINDOW_DAYS = 30


def _finished_logs(since):
    return (
        select()
        .select_from(OperationLog)
        .join(OperationLog.step)
        .where(OperationLog.finished_at >= since, OperationLog.qty_good.is_not(None))
    )


def _scrap_by(db: Session, column, since) -> list[dict]:
    rows = db.execute(
        _finished_logs(since)
        .add_columns(
            column,
            func.sum(OperationLog.qty_good),
            func.sum(OperationLog.qty_scrap),
        )
        .group_by(column)
    ).all()
    out = []
    for key, good, scrap in rows:
        total = (good or 0) + (scrap or 0)
        if total:
            out.append(
                {"label": key, "scrap": scrap or 0, "total": total, "rate": (scrap or 0) / total}
            )
    return sorted(out, key=lambda r: (-r["rate"], r["label"]))


def recent_activity(db: Session, limit: int = 8) -> list[dict]:
    """One line per user action: an action that changes five fields is still one event."""
    rows = db.scalars(
        select(AuditLog).where(AuditLog.action != "CREATE").order_by(AuditLog.id.desc()).limit(200)
    ).all()
    events: list[dict] = []
    for row in rows:
        key = (row.changed_by, row.reason, row.at)
        if events and events[-1]["key"] == key:
            events[-1]["changes"] += 1
            continue
        if len(events) == limit:
            break
        events.append(
            {
                "key": key,
                "user": row.changed_by,
                "reason": row.reason or f"{row.table_name} {row.action.lower()}",
                "at": row.at,
                "changes": 1,
            }
        )
    return events


def summary(db: Session, today: date | None = None) -> dict:
    today = today or utcnow().date()
    now = utcnow()

    counts = {s: 0 for s in OrderStatus}
    counts.update(
        dict(db.execute(select(WorkOrder.status, func.count()).group_by(WorkOrder.status)).all())
    )
    overdue = db.scalar(
        select(func.count()).where(
            WorkOrder.due_date < today, WorkOrder.status.not_in(CLOSED_STATUSES)
        )
    )

    # Output = good parts leaving the final step of each order's route.
    first_day = today - timedelta(days=OUTPUT_DAYS - 1)
    last_step = (
        select(RouteStep.work_order_id, func.max(RouteStep.seq).label("max_seq"))
        .group_by(RouteStep.work_order_id)
        .subquery()
    )
    day = func.date(OperationLog.finished_at)
    output_rows = db.execute(
        select(day, func.sum(OperationLog.qty_good))
        .join(OperationLog.step)
        .join(
            last_step,
            and_(
                RouteStep.work_order_id == last_step.c.work_order_id,
                RouteStep.seq == last_step.c.max_seq,
            ),
        )
        .where(
            OperationLog.finished_at >= now - timedelta(days=OUTPUT_DAYS + 1),
            OperationLog.qty_good.is_not(None),
        )
        .group_by(day)
    ).all()
    by_day = {str(d): int(q or 0) for d, q in output_rows}
    output = [
        {
            "day": first_day + timedelta(days=i),
            "units": by_day.get(str(first_day + timedelta(days=i)), 0),
        }
        for i in range(OUTPUT_DAYS)
    ]

    since = now - timedelta(days=WINDOW_DAYS)
    total_checks, passed_checks = db.execute(
        select(
            func.count(InspectionResult.id),
            func.coalesce(func.sum(case((InspectionResult.passed.is_(True), 1), else_=0)), 0),
        ).where(InspectionResult.measured_at >= since)
    ).one()

    scrap_steps = _scrap_by(db, RouteStep.name, since)
    good_30 = sum(r["total"] - r["scrap"] for r in scrap_steps)
    scrap_30 = sum(r["scrap"] for r in scrap_steps)

    holds = db.scalars(
        select(WorkOrder)
        .where(WorkOrder.status == OrderStatus.ON_HOLD)
        .options(selectinload(WorkOrder.part))
        .order_by(WorkOrder.order_no)
    ).all()
    recent = recent_activity(db)

    return {
        "counts": counts,
        "open_orders": counts[OrderStatus.RELEASED] + counts[OrderStatus.IN_PROGRESS],
        "on_hold": counts[OrderStatus.ON_HOLD],
        "overdue": overdue or 0,
        "output": output,
        "output_7d": sum(d["units"] for d in output[-7:]),
        "output_max": max((d["units"] for d in output), default=0),
        "scrap_rate_30d": scrap_30 / (good_30 + scrap_30) if good_30 + scrap_30 else None,
        "first_pass_yield_30d": passed_checks / total_checks if total_checks else None,
        "checks_30d": total_checks,
        "scrap_by_step": scrap_steps,
        "scrap_by_machine": _scrap_by(db, OperationLog.machine, since),
        "holds": holds,
        "recent": recent,
    }
