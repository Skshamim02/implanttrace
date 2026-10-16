from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Decision, OrderStatus, Role, StepStatus

T = TypeVar("T")

Code = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# --- Auth & users ----------------------------------------------------------------


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: int
    username: str
    full_name: str
    role: Role
    active: bool
    locked_until: datetime | None = None
    created_at: datetime


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: str = Code
    full_name: str = Field(min_length=1, max_length=100)
    role: Role
    password: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Role | None = None
    active: bool | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1, max_length=200)


# --- Machines & parts --------------------------------------------------------------


class MachineIn(BaseModel):
    code: str = Code
    name: str = Field(min_length=1, max_length=100)
    area: str = Field(min_length=1, max_length=50)


class MachineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    area: str | None = Field(default=None, min_length=1, max_length=50)
    active: bool | None = None


class MachineOut(ORM):
    id: int
    code: str
    name: str
    area: str
    active: bool


class PartIn(BaseModel):
    part_number: str = Code
    description: str = Field(min_length=1, max_length=200)
    customer: str = Field(min_length=1, max_length=100)
    route: list[str] = Field(min_length=1, max_length=30)

    @field_validator("route")
    @classmethod
    def clean_route(cls, steps: list[str]) -> list[str]:
        steps = [s.strip() for s in steps if s.strip()]
        if not steps:
            raise ValueError("route needs at least one step")
        if any(len(s) > 50 for s in steps):
            raise ValueError("step names must be 50 characters or fewer")
        return steps


class RouteStepDef(ORM):
    seq: int
    name: str


class PartOut(ORM):
    id: int
    part_number: str
    description: str
    customer: str
    active: bool
    route: list[RouteStepDef]


# --- Materials & work orders ---------------------------------------------------------


class MaterialIn(BaseModel):
    heat_number: str = Code
    grade: str = Field(min_length=1, max_length=50)
    supplier: str = Field(min_length=1, max_length=100)
    cert_ref: str = Field(min_length=1, max_length=100)


class MaterialOut(ORM):
    id: int
    heat_number: str
    grade: str
    supplier: str
    cert_ref: str
    received_at: datetime


class WorkOrderIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9\-]*$")
    part_number: str
    quantity: int = Field(gt=0, le=5_000)
    heat_number: str
    due_date: date | None = None


class OperationLogOut(ORM):
    operator: str
    machine: str
    started_at: datetime
    finished_by: str | None
    finished_at: datetime | None
    qty_good: int | None
    qty_scrap: int | None


class RouteStepOut(ORM):
    seq: int
    name: str
    status: StepStatus
    logs: list[OperationLogOut] = []


class WorkOrderSummary(ORM):
    order_no: str
    part_number: str
    quantity: int
    status: OrderStatus
    heat_number: str
    due_date: date | None
    is_overdue: bool
    created_at: datetime


class WorkOrderOut(WorkOrderSummary):
    steps: list[RouteStepOut]


class StepStartIn(BaseModel):
    machine: str = Field(min_length=1, max_length=30)


class StepFinishIn(BaseModel):
    qty_good: int = Field(ge=0)
    qty_scrap: int = Field(ge=0)


# --- Inspection & quality --------------------------------------------------------------


class ImportSummary(BaseModel):
    filename: str
    status: str  # "imported" | "duplicate"
    rows: int = 0
    failed_rows: int = 0
    orders_on_hold: list[str] = []


class InspectionOut(ORM):
    serial_no: str | None
    characteristic: str
    nominal: float
    lower_tol: float
    upper_tol: float
    measured: float
    passed: bool
    measured_at: datetime


class DispositionIn(BaseModel):
    decision: Decision
    ncr_number: str = Field(min_length=1, max_length=30)
    notes: str = Field(min_length=10, max_length=4000)
    password: str = Field(min_length=1, description="Re-enter your password to sign")


class SignatureOut(ORM):
    username: str
    full_name: str
    meaning: str
    signed_at: datetime


class DispositionOut(ORM):
    decision: Decision
    ncr_number: str
    notes: str
    created_at: datetime
    signature: SignatureOut | None


# --- Traceability ----------------------------------------------------------------------


class ForwardTraceOrder(BaseModel):
    order_no: str
    part_number: str
    status: OrderStatus
    serial_numbers: list[str]
    failed_inspections: int


class ForwardTrace(BaseModel):
    heat_number: str
    grade: str
    supplier: str
    cert_ref: str
    orders: list[ForwardTraceOrder]
    total_units: int


class BackwardTrace(BaseModel):
    serial_no: str
    order: WorkOrderOut
    material: MaterialOut
    inspections: list[InspectionOut]
    dispositions: list[DispositionOut]


# --- Audit -------------------------------------------------------------------------------


class AuditOut(ORM):
    id: int
    at: datetime
    changed_by: str
    action: str
    table_name: str
    record_id: int
    field: str | None
    old_value: str | None
    new_value: str | None
    reason: str | None
