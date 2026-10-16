"""Automatic audit trail.

Every insert, update and delete made through a SQLAlchemy Session is written to
audit_log in the same transaction, with the user and a reason. Updates and
deletes without a reason are refused, so records cannot be changed silently.

Services set the reason with:

    with audit_reason(db, "Operator finished Milling"):
        step.status = StepStatus.DONE
        db.flush()
"""

import json
from contextlib import contextmanager
from datetime import date, datetime
from enum import Enum

from sqlalchemy import event, insert, inspect
from sqlalchemy.orm import Session

from app.errors import Conflict

# Never copy secrets into the audit trail; record only that they changed.
MASKED_FIELDS = {"password_hash"}


class AuditReasonRequired(Conflict):
    pass


def _audited(obj) -> bool:
    from app.models import AuditLog

    return not isinstance(obj, AuditLog)


def _fmt(key: str, value) -> str | None:
    if value is None:
        return None
    if key in MASKED_FIELDS:
        return "***"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _columns(obj) -> dict[str, str | None]:
    mapper = inspect(obj).mapper
    return {c.key: _fmt(c.key, getattr(obj, c.key)) for c in mapper.column_attrs}


def _changes(obj) -> list[tuple[str, str | None, str | None]]:
    state = inspect(obj)
    out = []
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].history
        if hist.has_changes():
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else None
            if old != new:
                out.append((attr.key, _fmt(attr.key, old), _fmt(attr.key, new)))
    return out


@contextmanager
def audit_reason(session: Session, reason: str):
    previous = session.info.get("reason")
    session.info["reason"] = reason
    try:
        yield
    finally:
        session.info["reason"] = previous


@event.listens_for(Session, "before_flush")
def _require_reason(session: Session, flush_context, instances) -> None:
    if session.info.get("reason"):
        return
    changed = [o for o in session.dirty if _audited(o) and _changes(o)]
    deleted = [o for o in session.deleted if _audited(o)]
    if changed or deleted:
        names = sorted({type(o).__name__ for o in changed + deleted})
        raise AuditReasonRequired(f"A reason is required to change or delete: {', '.join(names)}")


@event.listens_for(Session, "after_flush")
def _write_audit(session: Session, flush_context) -> None:
    from app.models import AuditLog, utcnow

    user = session.info.get("user", "unknown")
    reason = session.info.get("reason")
    now = utcnow()
    rows = []

    def row(obj, action, field=None, old=None, new=None):
        rows.append(
            {
                "at": now,
                "changed_by": user,
                "action": action,
                "table_name": obj.__tablename__,
                "record_id": inspect(obj).mapper.primary_key_from_instance(obj)[0],
                "field": field,
                "old_value": old,
                "new_value": new,
                "reason": reason,
            }
        )

    for obj in session.new:
        if _audited(obj):
            row(obj, "CREATE", new=json.dumps(_columns(obj)))
    for obj in session.dirty:
        if _audited(obj):
            for field, old, new in _changes(obj):
                row(obj, "UPDATE", field, old, new)
    for obj in session.deleted:
        if _audited(obj):
            row(obj, "DELETE", old=json.dumps(_columns(obj)))

    if rows:
        session.connection().execute(insert(AuditLog), rows)
