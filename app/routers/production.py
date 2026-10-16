from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Operator, QualityOrSupervisor, Supervisor, current_user
from app.models import OrderStatus, User
from app.schemas import (
    MaterialIn,
    MaterialOut,
    Page,
    StepFinishIn,
    StepStartIn,
    WorkOrderIn,
    WorkOrderOut,
    WorkOrderSummary,
)
from app.services import production

router = APIRouter(tags=["production"])


@router.get("/materials", response_model=Page[MaterialOut])
def list_materials(
    search: str = "",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    return production.list_materials(db, search, limit, offset)


@router.post("/materials", response_model=MaterialOut, status_code=201)
def receive_material(
    data: MaterialIn, db: Session = Depends(get_db), _: User = Depends(QualityOrSupervisor)
):
    material = production.receive_material(db, data)
    db.commit()
    return material


@router.get("/materials/{heat_number}", response_model=MaterialOut)
def get_material(heat_number: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return production.get_material(db, heat_number)


@router.get("/work-orders", response_model=Page[WorkOrderSummary])
def list_orders(
    status: OrderStatus | None = None,
    search: str = "",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    return production.list_orders(db, status, search, limit, offset)


@router.post("/work-orders", response_model=WorkOrderOut, status_code=201)
def create_work_order(
    data: WorkOrderIn, db: Session = Depends(get_db), _: User = Depends(Supervisor)
):
    order = production.create_work_order(db, data)
    db.commit()
    return production.get_order(db, order.order_no)


@router.get("/work-orders/{order_no}", response_model=WorkOrderOut)
def get_work_order(order_no: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return production.get_order(db, order_no)


@router.post("/work-orders/{order_no}/steps/{seq}/start", response_model=WorkOrderOut)
def start_step(
    order_no: str,
    seq: int,
    data: StepStartIn,
    db: Session = Depends(get_db),
    user: User = Depends(Operator),
):
    order = production.start_step(db, user, order_no, seq, data.machine)
    db.commit()
    return order


@router.post("/work-orders/{order_no}/steps/{seq}/finish", response_model=WorkOrderOut)
def finish_step(
    order_no: str,
    seq: int,
    data: StepFinishIn,
    db: Session = Depends(get_db),
    user: User = Depends(Operator),
):
    order = production.finish_step(db, user, order_no, seq, data.qty_good, data.qty_scrap)
    db.commit()
    return order
