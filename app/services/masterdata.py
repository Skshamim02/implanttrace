"""Machines and parts (with their production routes)."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import audit_reason
from app.errors import Conflict, NotFound
from app.models import Machine, Part, PartRouteStep, User, WorkOrder
from app.schemas import MachineIn, MachineUpdate, PartIn


def list_machines(db: Session, active_only: bool = False) -> list[Machine]:
    q = select(Machine).order_by(Machine.area, Machine.code)
    if active_only:
        q = q.where(Machine.active.is_(True))
    return list(db.scalars(q))


def get_machine(db: Session, code: str) -> Machine:
    machine = db.scalar(select(Machine).where(Machine.code == code))
    if machine is None:
        raise NotFound(f"Machine {code} not found")
    return machine


def create_machine(db: Session, data: MachineIn) -> Machine:
    if db.scalar(select(Machine).where(Machine.code == data.code)):
        raise Conflict(f"Machine {data.code} already exists")
    machine = Machine(**data.model_dump())
    db.add(machine)
    db.flush()
    return machine


def update_machine(db: Session, actor: User, code: str, data: MachineUpdate) -> Machine:
    machine = get_machine(db, code)
    with audit_reason(db, f"Machine updated by {actor.username}"):
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(machine, key, value)
        db.flush()
    return machine


def list_parts(db: Session) -> list[Part]:
    return list(
        db.scalars(select(Part).options(selectinload(Part.route)).order_by(Part.part_number))
    )


def get_part(db: Session, part_number: str) -> Part:
    part = db.scalar(select(Part).where(Part.part_number == part_number))
    if part is None:
        raise NotFound(f"Part {part_number} not found")
    return part


def create_part(db: Session, data: PartIn) -> Part:
    if db.scalar(select(Part).where(Part.part_number == data.part_number)):
        raise Conflict(f"Part {data.part_number} already exists")
    part = Part(part_number=data.part_number, description=data.description, customer=data.customer)
    part.route = [PartRouteStep(seq=i, name=name) for i, name in enumerate(data.route, start=1)]
    db.add(part)
    db.flush()
    return part


def set_part_active(db: Session, actor: User, part_number: str, active: bool) -> Part:
    part = get_part(db, part_number)
    with audit_reason(db, f"Part {'activated' if active else 'retired'} by {actor.username}"):
        part.active = active
        db.flush()
    return part


def replace_route(db: Session, actor: User, part_number: str, route: list[str]) -> Part:
    """Routes are locked once orders exist; a changed process needs a new part revision."""
    part = get_part(db, part_number)
    if db.scalar(select(WorkOrder.id).where(WorkOrder.part_id == part.id).limit(1)):
        raise Conflict(
            f"{part_number} already has work orders; create a new part revision to change its route"
        )
    with audit_reason(db, f"Route replaced by {actor.username}"):
        part.route.clear()
        db.flush()
        part.route.extend(PartRouteStep(seq=i, name=n) for i, n in enumerate(route, start=1))
        db.flush()
    return part
