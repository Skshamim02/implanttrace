import pytest

from app.models import utcnow
from app.services import dashboard
from app.services.inspection_import import import_inspection_file
from tests.conftest import PASSWORD, ROUTE, cmm, run_steps


def test_dashboard_numbers(seeded, user):
    today = utcnow().date().isoformat()
    import_inspection_file(
        seeded,
        "c.csv",
        cmm(
            f"WO-2,,Dia,12,-0.01,0.01,12,{today}T09:00:00",
            f"WO-1,,Len,5,-0.1,0.1,6,{today}T09:00:00",
        ),
    )
    run_steps(seeded, user("op1"), "WO-2", len(ROUTE))  # 2 units completed today
    run_steps(seeded, user("op1"), "WO-3", 1, scrap=1)  # 4 in, 1 scrapped at Turning

    d = dashboard.summary(seeded)
    assert d["output_7d"] == 2
    assert d["output"][-1]["units"] == 2
    assert (d["open_orders"], d["on_hold"]) == (1, 1)  # WO-3 running, WO-1 held
    turning = next(r for r in d["scrap_by_step"] if r["label"] == "Turning")
    assert (turning["scrap"], turning["total"]) == (1, 6)
    assert (d["first_pass_yield_30d"], d["checks_30d"]) == (0.5, 2)
    assert [o.order_no for o in d["holds"]] == ["WO-1"]


def login_cookie(client, username):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "path, text",
    [
        ("/", "Units completed per day"),
        ("/orders", "WO-1"),
        ("/orders?status=RELEASED&search=WO", "WO-3"),
        ("/orders/WO-1", "Route"),
        ("/orders/WO-1/traveller", "<svg"),
        ("/station?order=WO-1", "Choose machine"),
        ("/station?order=NOPE", "not found"),
        ("/quality", "Hold queue"),
        ("/trace?heat=H1", "5 implants"),
        ("/trace?serial=WO-1-0001", "Manufacturing history"),
        ("/materials", "H2"),
        ("/parts", "P1"),
        ("/machines", "CMM"),
        ("/audit?table=&record_id=&user=", "Audit trail"),
        ("/users", "op2"),
        ("/account", "Change password"),
    ],
)
def test_every_page_renders_for_admin(client, seeded, path, text):
    login_cookie(client, "admin")
    r = client.get(path)
    assert r.status_code == 200, r.text[:500]
    assert text in r.text


def test_role_restricted_pages(client, seeded):
    login_cookie(client, "op1")
    assert client.get("/audit").status_code == 403
    assert client.get("/users").status_code == 403
    assert client.get("/station").status_code == 200
    login_cookie(client, "qa")
    assert client.get("/station").status_code == 403


def test_unknown_order_page_is_404(client, seeded):
    login_cookie(client, "admin")
    assert client.get("/orders/NOPE").status_code == 404


def test_security_headers(client, seeded):
    r = client.get("/login")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-request-id"]


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
