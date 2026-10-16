"""Data model.

Traceability chain:
    Material (heat number) -> WorkOrder -> Unit (serial number)
                                 |-> RouteStep -> OperationLog (operator, machine, times, qty)
                                 |-> InspectionResult (from CMM exports)
                                 |-> Disposition (Quality decision, e-signed)
"""

import enum
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.errors import Conflict

_clock: Callable[[], datetime] | None = None


def utcnow() -> datetime:
    """Current UTC time. Seed data and tests may replace the clock with set_clock()."""
    return _clock() if _clock else datetime.now(UTC)


def set_clock(clock: Callable[[], datetime] | None) -> None:
    global _clock
    _clock = clock


def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; every stored time is UTC, so restore it."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _enum(cls):
    return Enum(cls, native_enum=False, length=20, validate_strings=True)


class Role(enum.StrEnum):
    OPERATOR = "OPERATOR"  # runs machines, records steps
    SUPERVISOR = "SUPERVISOR"  # releases orders, receives material, manages machines/parts
    QUALITY = "QUALITY"  # imports inspection data, dispositions held orders, reads audit trail
    ADMIN = "ADMIN"  # manages users; may do everything


class OrderStatus(enum.StrEnum):
    RELEASED = "RELEASED"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD = "ON_HOLD"  # failed inspection, waiting for a Quality decision
    COMPLETE = "COMPLETE"
    SCRAPPED = "SCRAPPED"


CLOSED_STATUSES = (OrderStatus.COMPLETE, OrderStatus.SCRAPPED)


class StepStatus(enum.StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"


class Decision(enum.StrEnum):
    REWORK = "REWORK"  # send back for rework and re-inspection
    USE_AS_IS = "USE_AS_IS"  # concession: accept the deviation with justification
    SCRAP = "SCRAP"  # scrap the whole order


INSPECTION_STEP = "Inspection"


# --- People and equipment ----------------------------------------------------


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(_enum(Role))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Bumped to sign the user out everywhere (password change, deactivation, role change).
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def has_role(self, *roles: Role) -> bool:
        return self.role == Role.ADMIN or self.role in roles


class Machine(Base):
    __tablename__ = "machine"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # e.g. CNC-LATHE-02
    name: Mapped[str] = mapped_column(String(100))
    area: Mapped[str] = mapped_column(String(50))  # e.g. Turning cell
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- Product definition ------------------------------------------------------


class Part(Base):
    __tablename__ = "part"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(200))
    customer: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    route: Mapped[list["PartRouteStep"]] = relationship(
        back_populates="part", order_by="PartRouteStep.seq", cascade="all, delete-orphan"
    )
    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="part")


class PartRouteStep(Base):
    __tablename__ = "part_route_step"
    __table_args__ = (UniqueConstraint("part_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))

    part: Mapped[Part] = relationship(back_populates="route")


# --- Production ----------------------------------------------------------------


class Material(Base):
    __tablename__ = "material"

    id: Mapped[int] = mapped_column(primary_key=True)
    heat_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    grade: Mapped[str] = mapped_column(String(50))  # e.g. Ti-6Al-4V ELI, CoCrMo
    supplier: Mapped[str] = mapped_column(String(100))
    cert_ref: Mapped[str] = mapped_column(String(100))  # mill certificate reference
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="material")


class WorkOrder(Base):
    __tablename__ = "work_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("part.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus), default=OrderStatus.RELEASED, index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    part: Mapped[Part] = relationship(back_populates="work_orders")
    material: Mapped[Material] = relationship(back_populates="work_orders")
    steps: Mapped[list["RouteStep"]] = relationship(
        back_populates="work_order", order_by="RouteStep.seq"
    )
    units: Mapped[list["Unit"]] = relationship(back_populates="work_order", order_by="Unit.id")
    inspections: Mapped[list["InspectionResult"]] = relationship(
        back_populates="work_order", order_by="InspectionResult.id"
    )
    dispositions: Mapped[list["Disposition"]] = relationship(
        back_populates="work_order", order_by="Disposition.id"
    )

    @property
    def part_number(self) -> str:
        return self.part.part_number

    @property
    def heat_number(self) -> str:
        return self.material.heat_number

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_date and self.due_date < date.today() and self.status not in CLOSED_STATUSES
        )


class RouteStep(Base):
    __tablename__ = "route_step"
    __table_args__ = (UniqueConstraint("work_order_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus), default=StepStatus.PENDING)

    work_order: Mapped[WorkOrder] = relationship(back_populates="steps")
    logs: Mapped[list["OperationLog"]] = relationship(
        back_populates="step", order_by="OperationLog.id"
    )


class OperationLog(Base):
    """Who ran a step, on which machine, when, and with what yield.

    Operator and machine are stored as their codes at the time of work, so the record stays
    readable even if a user or machine is later renamed or deactivated.
    """

    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    route_step_id: Mapped[int] = mapped_column(ForeignKey("route_step.id"), index=True)
    operator: Mapped[str] = mapped_column(String(50))
    machine: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_by: Mapped[str | None] = mapped_column(String(50))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    qty_good: Mapped[int | None] = mapped_column(Integer)
    qty_scrap: Mapped[int | None] = mapped_column(Integer)

    step: Mapped[RouteStep] = relationship(back_populates="logs")


class Unit(Base):
    """One serialised implant."""

    __tablename__ = "unit"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id"), index=True)

    work_order: Mapped[WorkOrder] = relationship(back_populates="units")


# --- Quality -------------------------------------------------------------------


class ImportedFile(Base):
    """Makes inspection imports idempotent: the same file content is never loaded twice."""

    __tablename__ = "imported_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    row_count: Mapped[int] = mapped_column(Integer)
    imported_by: Mapped[str] = mapped_column(String(50))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InspectionResult(Base):
    __tablename__ = "inspection_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id"), index=True)
    serial_no: Mapped[str | None] = mapped_column(String(40))
    characteristic: Mapped[str] = mapped_column(String(80))  # e.g. "Taper diameter"
    nominal: Mapped[float] = mapped_column(Float)
    lower_tol: Mapped[float] = mapped_column(Float)  # signed, e.g. -0.010
    upper_tol: Mapped[float] = mapped_column(Float)  # signed, e.g. +0.010
    measured: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_file_id: Mapped[int] = mapped_column(ForeignKey("imported_file.id"))

    work_order: Mapped[WorkOrder] = relationship(back_populates="inspections")
    imported_file: Mapped[ImportedFile] = relationship()


class ElectronicSignature(Base):
    """21 CFR Part 11 style signature: who, when, what it means, and which record it signs."""

    __tablename__ = "electronic_signature"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    username: Mapped[str] = mapped_column(String(50))
    full_name: Mapped[str] = mapped_column(String(100))
    meaning: Mapped[str] = mapped_column(String(200))
    table_name: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Disposition(Base):
    __tablename__ = "disposition"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id"), index=True)
    decision: Mapped[Decision] = mapped_column(_enum(Decision))
    ncr_number: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text)
    signature_id: Mapped[int | None] = mapped_column(ForeignKey("electronic_signature.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    work_order: Mapped[WorkOrder] = relationship(back_populates="dispositions")
    signature: Mapped[ElectronicSignature | None] = relationship()


# --- Audit -----------------------------------------------------------------------


class AuditLog(Base):
    """Append-only record of every change. Written by app.audit, never edited."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    changed_by: Mapped[str] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(10))  # CREATE / UPDATE / DELETE
    table_name: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    field: Mapped[str | None] = mapped_column(String(50))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)


@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
def _audit_log_is_append_only(mapper, connection, target):
    # Application-level guard. On PostgreSQL the initial migration also adds a trigger.
    raise Conflict("audit_log is append-only")
