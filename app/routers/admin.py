from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Admin, Supervisor, current_user
from app.models import User
from app.schemas import (
    MachineIn,
    MachineOut,
    MachineUpdate,
    PartIn,
    PartOut,
    PasswordReset,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import masterdata, users

router = APIRouter(tags=["admin"])


# --- Users (ADMIN) ---------------------------------------------------------------


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(Admin)):
    return db.scalars(select(User).order_by(User.username)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db), _: User = Depends(Admin)):
    user = users.create_user(db, data)
    db.commit()
    return user


@router.patch("/users/{username}", response_model=UserOut)
def update_user(
    username: str, data: UserUpdate, db: Session = Depends(get_db), actor: User = Depends(Admin)
):
    user = users.update_user(db, actor, username, data)
    db.commit()
    return user


@router.post("/users/{username}/reset-password", status_code=204)
def reset_password(
    username: str, data: PasswordReset, db: Session = Depends(get_db), actor: User = Depends(Admin)
):
    users.reset_password(db, actor, username, data.new_password)
    db.commit()


# --- Machines ----------------------------------------------------------------------


@router.get("/machines", response_model=list[MachineOut])
def list_machines(
    active_only: bool = False, db: Session = Depends(get_db), _: User = Depends(current_user)
):
    return masterdata.list_machines(db, active_only)


@router.post("/machines", response_model=MachineOut, status_code=201)
def create_machine(data: MachineIn, db: Session = Depends(get_db), _: User = Depends(Supervisor)):
    machine = masterdata.create_machine(db, data)
    db.commit()
    return machine


@router.patch("/machines/{code}", response_model=MachineOut)
def update_machine(
    code: str, data: MachineUpdate, db: Session = Depends(get_db), actor: User = Depends(Supervisor)
):
    machine = masterdata.update_machine(db, actor, code, data)
    db.commit()
    return machine


# --- Parts -------------------------------------------------------------------------


@router.get("/parts", response_model=list[PartOut])
def list_parts(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return masterdata.list_parts(db)


@router.get("/parts/{part_number}", response_model=PartOut)
def get_part(part_number: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return masterdata.get_part(db, part_number)


@router.post("/parts", response_model=PartOut, status_code=201)
def create_part(data: PartIn, db: Session = Depends(get_db), _: User = Depends(Supervisor)):
    part = masterdata.create_part(db, data)
    db.commit()
    return part


@router.put("/parts/{part_number}/route", response_model=PartOut)
def replace_route(
    part_number: str,
    route: list[str],
    db: Session = Depends(get_db),
    actor: User = Depends(Supervisor),
):
    cleaned = PartIn(part_number=part_number, description="-", customer="-", route=route).route
    part = masterdata.replace_route(db, actor, part_number, cleaned)
    db.commit()
    return part


@router.post("/parts/{part_number}/{action}", response_model=PartOut)
def set_part_active(
    part_number: str,
    action: Literal["retire", "activate"],
    db: Session = Depends(get_db),
    actor: User = Depends(Supervisor),
):
    part = masterdata.set_part_active(db, actor, part_number, action == "activate")
    db.commit()
    return part
