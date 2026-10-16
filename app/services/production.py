"""Production rules: materials, work orders and step-by-step progress on the shop floor."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.audit import audit_reason
from app.errors import Conflict, NotFound, PermissionDenied
from app.models import (
    CLOSED_STATUSES,
    INSPECTION_STEP,
    Material,
    OperationLog,
    OrderStatus,
    Part,
    Role,
    RouteStep,
    StepStatus,
    Unit,
    User,
    WorkOrder,
    aware,
    utcnow,
)
from app.schemas import MaterialIn, WorkOrderIn
from app.services.common import like, paginate
from app.services.masterdata import get_machine, get_part

# --- Materials -------------------------------------------------------------------


def get_material(db: Session, heat_number: str) -> Material:
    material = db.scalar(select(Material).where(Material.heat_number == heat_number))
    if material is None:
        raise NotFound(f"Material heat {heat_number} not found")
    return material


def list_materials(db: Session, search: str = "", limit: int = 50, offset: int = 0) -> dict:
    q = select(Material).order_by(Material.received_at.desc(), Material.id.desc())
    if search:
        term = like(search)
        q = q.where(
            or_(
                Material.heat_number.ilike(term, escape="\\"),
                Material.grade.ilike(term, escape="\\"),
                Material.supplier.ilike(term, escape="\\"),
            )
        )
    return paginate(db, q, limit, offset)


def receive_material(db: Session, data: MaterialIn) -> Material:
    if db.scalar(select(Material).where(Material.heat_number == data.heat_number)):
        raise Conflict(f"Heat {data.heat_number} already received")
    material = Material(**data.model_dump())
    db.add(material)
    db.flush()
    return material


# --- Work orders -----------------------------------------------------------------


def get_order(db: Session, order_no: str) -> WorkOrder:
    order = db.scalar(
        select(WorkOrder)
        .where(WorkOrder.order_no == order_no)
        .options(
            selectinload(WorkOrder.steps).selectinload(RouteStep.logs),
            selectinload(WorkOrder.part),
            selectinload(WorkOrder.material),
        )
    )
    if order is None:
        raise NotFound(f"Work order {order_no} not found")
    return order


def list_orders(
    db: Session,
    status: OrderStatus | None = None,
    search: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    q = (
        select(WorkOrder)
        .join(WorkOrder.part)
        .join(WorkOrder.material)
        .options(selectinload(WorkOrder.part), selectinload(WorkOrder.material))
        .order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc())
    )
    if status:
        q = q.where(WorkOrder.status == status)
    if search:
        term = like(search)
        q = q.where(
            or_(
                WorkOrder.order_no.ilike(term, escape="\\"),
                Part.part_number.ilike(term, escape="\\"),
                Material.heat_number.ilike(term, escape="\\"),
            )
        )
    return paginate(db, q, limit, offset)


def create_work_order(db: Session, data: WorkOrderIn) -> WorkOrder:
    if db.scalar(select(WorkOrder).where(WorkOrder.order_no == data.order_no)):
        raise Conflict(f"Work order {data.order_no} already exists")
    part = get_part(db, data.part_number)
    if not part.active:
        raise Conflict(f"Part {part.part_number} is retired")
    if not part.route:
        raise Conflict(f"Part {part.part_number} has no route")
    material = get_material(db, data.heat_number)

    order = WorkOrder(
        order_no=data.order_no,
        part=part,
        quantity=data.quantity,
        material=material,
        due_date=data.due_date,
    )
    db.add(order)
    # The order gets its own copy of the route, so later part changes never rewrite history.
    for step in part.route:
        db.add(RouteStep(work_order=order, seq=step.seq, name=step.name))
    for i in range(1, data.quantity + 1):
        db.add(Unit(work_order=order, serial_no=f"{data.order_no}-{i:04d}"))
    db.flush()
    return order


# --- Shop-floor steps ------------------------------------------------------------


def _step(order: WorkOrder, seq: int) -> RouteStep:
    for step in order.steps:
        if step.seq == seq:
            return step
    raise NotFound(f"Step {seq} not found on {order.order_no}")


def qty_into_step(order: WorkOrder, step: RouteStep) -> int:
    """Parts available to a step: the good output of the latest earlier step that reported one.

    A step closed by a Quality concession has no output of its own, so we look further back.
    """
    for prev in sorted((s for s in order.steps if s.seq < step.seq), key=lambda s: -s.seq):
        reported = [log for log in prev.logs if log.qty_good is not None]
        if reported:
            return reported[-1].qty_good
    return order.quantity


def open_inspection_failures(order: WorkOrder) -> tuple[int, int]:
    """(results, failures) imported since the last Quality disposition."""
    last = max((aware(d.created_at) for d in order.dispositions), default=None)
    relevant = [
        r for r in order.inspections if last is None or aware(r.imported_file.imported_at) > last
    ]
    return len(relevant), sum(not r.passed for r in relevant)


def start_step(db: Session, user: User, order_no: str, seq: int, machine_code: str) -> WorkOrder:
    order = get_order(db, order_no)
    if order.status == OrderStatus.ON_HOLD:
        raise Conflict(f"{order_no} is ON HOLD awaiting a Quality decision")
    if order.status in CLOSED_STATUSES:
        raise Conflict(f"{order_no} is {order.status.value}")

    step = _step(order, seq)
    if step.status != StepStatus.PENDING:
        raise Conflict(f"Step {seq} ({step.name}) is {step.status.value}, not PENDING")
    unfinished = [s.name for s in order.steps if s.seq < seq and s.status != StepStatus.DONE]
    if unfinished:
        raise Conflict(f"Finish earlier steps first: {', '.join(unfinished)}")
    if qty_into_step(order, step) == 0:
        raise Conflict("No good parts left to process")

    machine = get_machine(db, machine_code)
    if not machine.active:
        raise Conflict(f"Machine {machine.code} is out of service")

    with audit_reason(db, f"{user.username} started {step.name} on {machine.code}"):
        step.status = StepStatus.IN_PROGRESS
        if order.status == OrderStatus.RELEASED:
            order.status = OrderStatus.IN_PROGRESS
        db.add(OperationLog(step=step, operator=user.username, machine=machine.code))
        db.flush()
    return order


def finish_step(
    db: Session, user: User, order_no: str, seq: int, qty_good: int, qty_scrap: int
) -> WorkOrder:
    order = get_order(db, order_no)
    step = _step(order, seq)
    if step.status != StepStatus.IN_PROGRESS:
        raise Conflict(f"Step {seq} ({step.name}) is not in progress")

    log = next(entry for entry in step.logs if entry.finished_at is None)
    if log.operator != user.username and not user.has_role(Role.SUPERVISOR):
        raise PermissionDenied(f"Only {log.operator} or a supervisor can finish this step")

    available = qty_into_step(order, step)
    if qty_good + qty_scrap != available:
        raise Conflict(
            f"Good + scrap must equal the {available} parts that entered {step.name} "
            f"(got {qty_good + qty_scrap})"
        )

    if step.name == INSPECTION_STEP:
        results, failures = open_inspection_failures(order)
        if results == 0:
            raise Conflict("Import inspection results before finishing Inspection")
        if failures:
            raise Conflict(f"{failures} out-of-tolerance results need a Quality disposition")

    with audit_reason(db, f"{user.username} finished {step.name}"):
        log.finished_at = utcnow()
        log.finished_by = user.username
        log.qty_good = qty_good
        log.qty_scrap = qty_scrap
        step.status = StepStatus.DONE
        if all(s.status == StepStatus.DONE for s in order.steps):
            order.status = OrderStatus.COMPLETE
        db.flush()
    return order
