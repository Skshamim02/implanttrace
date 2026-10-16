"""Load a realistic two-week demo history. Run: python -m scripts.seed

Creates users, machines, parts and material, then replays production through the real
service layer (so every rule and audit entry is genuine), with the clock moved back in time.
Synthetic data only.
"""

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.migrate import upgrade
from app.models import INSPECTION_STEP, Decision, OrderStatus, Role, User, set_clock
from app.schemas import DispositionIn, MachineIn, MaterialIn, PartIn, UserCreate, WorkOrderIn
from app.services import masterdata, production, quality, users
from app.services.inspection_import import import_inspection_file

DEMO_PASSWORD = "Implant2026"

USERS = [
    ("admin", "Plant Administrator", Role.ADMIN),
    ("s.oconnor", "Siobhan O'Connor", Role.SUPERVISOR),
    ("q.byrne", "Ciara Byrne", Role.QUALITY),
    ("op.murphy", "Sean Murphy", Role.OPERATOR),
    ("op.walsh", "Aoife Walsh", Role.OPERATOR),
    ("op.ryan", "Liam Ryan", Role.OPERATOR),
]
OPERATORS = ["op.murphy", "op.walsh", "op.ryan"]

MACHINES = {
    "Turning": [("CNC-LATHE-01", "Citizen L32 sliding head"), ("CNC-LATHE-02", "Star SR-32J")],
    "Milling": [("CNC-MILL-04", "DMG Mori DMU 50"), ("CNC-MILL-05", "Hermle C 22")],
    "Polishing": [
        ("POLISH-01", "Robotic polishing cell 1"),
        ("POLISH-02", "Manual polishing bench"),
    ],
    "Inspection": [("CMM-01", "Zeiss Contura CMM")],
    "Cleaning": [("WASH-01", "Ultrasonic cleaning line")],
    "Packing": [("PACK-01", "Cleanroom packing station")],
}

ROUTE = ["Turning", "Milling", "Polishing", "Inspection", "Cleaning", "Packing"]
PARTS = [
    ("HIP-STEM-12", "Femoral hip stem, size 12", "Demo Ortho Inc.", ROUTE),
    ("HIP-STEM-14", "Femoral hip stem, size 14", "Demo Ortho Inc.", ROUTE),
    ("KNEE-FEM-L3", "Femoral knee component, left, size 3", "Demo Joints GmbH", ROUTE),
    ("ACET-CUP-52", "Acetabular cup, 52 mm", "Demo Ortho Inc.", ROUTE),
]
CHECKS = {
    "HIP-STEM-12": [("Taper diameter", 12.000, 0.010), ("Neck length", 32.500, 0.050)],
    "HIP-STEM-14": [("Taper diameter", 14.000, 0.010), ("Neck length", 34.000, 0.050)],
    "KNEE-FEM-L3": [("Condyle radius", 22.000, 0.020), ("Box width", 18.500, 0.030)],
    "ACET-CUP-52": [("Outer diameter", 52.000, 0.015), ("Rim thickness", 4.200, 0.020)],
}
MATERIALS = [
    ("H-24-0815", "Ti-6Al-4V ELI (ASTM F136)", "Demo Titanium Ltd", "MC-77812"),
    ("H-24-0921", "CoCrMo (ASTM F1537)", "Demo Alloys GmbH", "MC-80455"),
    ("H-24-1003", "Ti-6Al-4V ELI (ASTM F136)", "Demo Titanium Ltd", "MC-78120"),
    ("H-24-1011", "CoCrMo (ASTM F1537)", "Demo Alloys GmbH", "MC-80611"),
]

# order, part, qty, heat, start (days ago), steps to complete, scenario
ORDERS = [
    ("WO-1001", "HIP-STEM-12", 12, "H-24-0815", 15, 6, "ok"),
    ("WO-1002", "HIP-STEM-14", 10, "H-24-0815", 14, 6, "ok"),
    ("WO-1003", "KNEE-FEM-L3", 8, "H-24-0921", 13, 6, "rework"),
    ("WO-1004", "ACET-CUP-52", 16, "H-24-0921", 12, 6, "ok"),
    ("WO-1005", "HIP-STEM-12", 14, "H-24-1003", 10, 6, "ok"),
    ("WO-1006", "HIP-STEM-14", 12, "H-24-1003", 9, 6, "ok"),
    ("WO-1007", "KNEE-FEM-L3", 10, "H-24-1011", 8, 6, "ok"),
    ("WO-1008", "ACET-CUP-52", 18, "H-24-1011", 7, 6, "ok"),
    ("WO-1009", "HIP-STEM-12", 15, "H-24-1003", 5, 6, "ok"),
    ("WO-1010", "HIP-STEM-14", 12, "H-24-0815", 4, 6, "ok"),
    ("WO-1011", "KNEE-FEM-L3", 9, "H-24-1011", 3, 4, "fail"),  # on hold now
    ("WO-1012", "ACET-CUP-52", 14, "H-24-0921", 2, 3, "ok"),  # waiting for inspection
    ("WO-1013", "HIP-STEM-12", 10, "H-24-1003", 1, 1, "ok"),  # in progress
    ("WO-1014", "HIP-STEM-14", 8, "H-24-0815", 0, 0, "ok"),  # released only
]

rng = random.Random(42)


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


def _cmm_csv(order, step_time: datetime, fail: bool) -> bytes:
    lines = ["order_no,serial_no,characteristic,nominal,lower_tol,upper_tol,measured,measured_at"]
    for i, unit in enumerate(order.units[:5]):
        for name, nominal, tol in CHECKS[order.part_number]:
            dev = rng.uniform(-0.6, 0.6) * tol
            if fail and i == 2 and name == CHECKS[order.part_number][0][0]:
                dev = tol * 1.4
            t = (
                (step_time + timedelta(minutes=3 * i))
                .replace(tzinfo=None)
                .isoformat(timespec="seconds")
            )
            lines.append(
                f"{order.order_no},{unit.serial_no},{name},{nominal:.3f},"
                f"{-tol:.3f},{tol:.3f},{nominal + dev:.4f},{t}"
            )
    return ("\n".join(lines) + "\n").encode()


def run_order(db, clock: Clock, people: dict, order_no: str, steps: int, scenario: str) -> None:
    order = production.get_order(db, order_no)
    for step in order.steps[:steps]:
        op = people[rng.choice(OPERATORS)]
        machine = rng.choice(MACHINES[step.name])[0]
        db.info["user"] = op.username
        production.start_step(db, op, order_no, step.seq, machine)
        clock.advance(rng.uniform(40, 150))

        if step.name == INSPECTION_STEP:
            db.info["user"] = "system:inbox-watcher"
            fail = scenario in ("fail", "rework")
            name = f"cmm_{order_no}_{clock.now:%Y%m%d%H%M}.csv"
            import_inspection_file(db, name, _cmm_csv(order, clock.now, fail))
            if fail:
                if scenario == "fail":
                    return
                clock.advance(90)
                q = people["q.byrne"]
                db.info["user"] = q.username
                quality.dispose(
                    db,
                    q,
                    order_no,
                    DispositionIn(
                        decision=Decision.REWORK,
                        ncr_number="NCR-2026-017",
                        notes=f"{CHECKS[order.part_number][0][0]} out of tolerance on one "
                        "unit; worn finishing cutter. Re-polish the batch and re-measure 100%.",
                        password=DEMO_PASSWORD,
                    ),
                )
                scenario = "ok"
                clock.advance(240)
                db.info["user"] = op.username
                production.start_step(db, op, order_no, step.seq, "CMM-01")
                clock.advance(60)
                db.info["user"] = "system:inbox-watcher"
                import_inspection_file(
                    db, f"cmm_{order_no}_recheck.csv", _cmm_csv(order, clock.now, False)
                )
            db.info["user"] = op.username

        qty_in = production.qty_into_step(order, step)
        scrap = 1 if rng.random() < 0.25 and qty_in > 1 else 0
        production.finish_step(db, op, order_no, step.seq, qty_in - scrap, scrap)
        clock.advance(rng.uniform(30, 300))


def main() -> None:
    upgrade()
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            print("Database already has data; seed skipped.")
            return

        today = datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0)
        clock = Clock(today - timedelta(days=17))
        set_clock(clock)
        try:
            db.info["user"] = "seed-script"
            people = {
                u: users.create_user(
                    db, UserCreate(username=u, full_name=n, role=r, password=DEMO_PASSWORD)
                )
                for u, n, r in USERS
            }
            for area, machines in MACHINES.items():
                for code, name in machines:
                    masterdata.create_machine(
                        db, MachineIn(code=code, name=name, area=f"{area} cell")
                    )
            for pn, desc, cust, route in PARTS:
                masterdata.create_part(
                    db, PartIn(part_number=pn, description=desc, customer=cust, route=route)
                )
            for heat, grade, supplier, cert in MATERIALS:
                production.receive_material(
                    db, MaterialIn(heat_number=heat, grade=grade, supplier=supplier, cert_ref=cert)
                )
            db.commit()

            for order_no, part, qty, heat, days_ago, steps, scenario in ORDERS:
                clock.now = today - timedelta(days=days_ago) + timedelta(minutes=rng.uniform(0, 90))
                db.info["user"] = "s.oconnor"
                production.create_work_order(
                    db,
                    WorkOrderIn(
                        order_no=order_no,
                        part_number=part,
                        quantity=qty,
                        heat_number=heat,
                        due_date=(
                            clock.now
                            + timedelta(days=2 if scenario == "fail" else 10 if days_ago < 3 else 5)
                        ).date(),
                    ),
                )
                run_order(db, clock, people, order_no, steps, scenario)
                db.commit()
        finally:
            set_clock(None)

        held = [o for o in ORDERS if o[6] == "fail"]
        print(f"Seeded {len(ORDERS)} work orders ({len(held)} on hold).")
        print(f"Demo users (password {DEMO_PASSWORD!r}): " + ", ".join(u for u, _, _ in USERS))
        assert production.get_order(db, held[0][0]).status == OrderStatus.ON_HOLD


if __name__ == "__main__":
    main()
