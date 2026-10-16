import os

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.security as security_module
from app.db import Base, get_db
from app.main import app
from app.models import Role
from app.routers import auth as auth_router
from app.schemas import MachineIn, MaterialIn, PartIn, UserCreate, WorkOrderIn
from app.services import masterdata, production, users

PASSWORD = "Password123"
ROUTE = ["Turning", "Milling", "Inspection", "Packing"]
HEADER = "order_no,serial_no,characteristic,nominal,lower_tol,upper_tol,measured,measured_at\n"

# Fast hashing for tests only.
security_module._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)


def cmm(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode()


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    auth_router._ATTEMPTS.clear()


@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
    else:
        eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        session.info["user"] = "test"
        yield session


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            session.info["user"] = "anonymous"
            yield session

    app.dependency_overrides[get_db] = override
    # No `with` block: skip the lifespan so nothing touches the real database.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db):
    """Users for every role, machines, two parts, two heats and three orders."""
    for username, role in [
        ("admin", Role.ADMIN),
        ("sup", Role.SUPERVISOR),
        ("qa", Role.QUALITY),
        ("op1", Role.OPERATOR),
        ("op2", Role.OPERATOR),
    ]:
        users.create_user(
            db,
            UserCreate(username=username, full_name=username.upper(), role=role, password=PASSWORD),
        )
    for code, area in [
        ("M1", "Turning"),
        ("M2", "Milling"),
        ("CMM", "Metrology"),
        ("PK", "Packing"),
    ]:
        masterdata.create_machine(db, MachineIn(code=code, name=f"Machine {code}", area=area))
    for pn in ("P1", "P2"):
        masterdata.create_part(
            db, PartIn(part_number=pn, description=pn, customer="C", route=ROUTE)
        )
    production.receive_material(
        db, MaterialIn(heat_number="H1", grade="Ti-6Al-4V ELI", supplier="S1", cert_ref="C1")
    )
    production.receive_material(
        db, MaterialIn(heat_number="H2", grade="CoCrMo", supplier="S2", cert_ref="C2")
    )
    for order_no, part, qty, heat in [
        ("WO-1", "P1", 3, "H1"),
        ("WO-2", "P1", 2, "H1"),
        ("WO-3", "P2", 4, "H2"),
    ]:
        production.create_work_order(
            db, WorkOrderIn(order_no=order_no, part_number=part, quantity=qty, heat_number=heat)
        )
    db.commit()
    return db


@pytest.fixture
def user(seeded):
    from app.services.users import get_user

    return lambda username: get_user(seeded, username)


@pytest.fixture
def auth(client, seeded):
    """auth("qa") -> headers with a bearer token for that user."""

    def login(username: str) -> dict:
        r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return login


def run_steps(db, user, order_no: str, upto_seq: int, scrap: int = 0) -> None:
    """Start and finish steps 1..upto_seq with machine M1, as `user`."""
    order = production.get_order(db, order_no)
    for step in order.steps[:upto_seq]:
        production.start_step(db, user, order_no, step.seq, "M1")
        available = production.qty_into_step(order, step)
        production.finish_step(db, user, order_no, step.seq, available - scrap, scrap)
    db.commit()
