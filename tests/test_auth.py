from app.config import settings
from tests.conftest import PASSWORD


def test_login_returns_token_and_sets_cookies(client, seeded):
    r = client.post("/api/v1/auth/login", json={"username": "op1", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "OPERATOR"
    assert "it_session" in r.cookies and "it_csrf" in r.cookies


def test_wrong_password_and_unknown_user_look_the_same(client, seeded):
    a = client.post("/api/v1/auth/login", json={"username": "op1", "password": "nope"})
    b = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"] == "Invalid username or password"


def test_account_locks_after_repeated_failures(client, seeded):
    for _ in range(settings.max_failed_logins):
        client.post("/api/v1/auth/login", json={"username": "op1", "password": "nope"})
    r = client.post("/api/v1/auth/login", json={"username": "op1", "password": PASSWORD})
    assert r.status_code == 401
    assert "locked" in r.json()["detail"]


def test_admin_unlocks_by_reactivating(client, seeded, auth):
    for _ in range(settings.max_failed_logins):
        client.post("/api/v1/auth/login", json={"username": "op1", "password": "nope"})
    admin = auth("admin")
    assert (
        client.patch("/api/v1/users/op1", json={"active": True}, headers=admin).status_code == 200
    )
    r = client.post("/api/v1/auth/login", json={"username": "op1", "password": PASSWORD})
    assert r.status_code == 200


def test_api_requires_authentication(client, seeded):
    assert client.get("/api/v1/work-orders").status_code == 401
    assert (
        client.get("/api/v1/work-orders", headers={"Authorization": "Bearer junk"}).status_code
        == 401
    )


def test_roles_are_enforced(client, seeded, auth):
    op = auth("op1")
    r = client.post(
        "/api/v1/materials",
        json={"heat_number": "H9", "grade": "g", "supplier": "s", "cert_ref": "c"},
        headers=op,
    )
    assert r.status_code == 403
    assert client.get("/api/v1/users", headers=op).status_code == 403
    assert client.get("/api/v1/audit", headers=op).status_code == 403
    assert client.get("/api/v1/users", headers=auth("admin")).status_code == 200


def test_cookie_session_needs_csrf_token_for_changes(client, seeded):
    client.post("/api/v1/auth/login", json={"username": "op1", "password": PASSWORD})
    body = {"machine": "M1"}
    url = "/api/v1/work-orders/WO-1/steps/1/start"

    assert client.get("/api/v1/auth/me").status_code == 200  # reads need no token
    assert client.post(url, json=body).status_code == 403
    r = client.post(url, json=body, headers={"X-CSRF-Token": client.cookies["it_csrf"]})
    assert r.status_code == 200


def test_password_change_invalidates_old_sessions(client, seeded, auth):
    old = auth("op1")
    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "NewPassword456"},
        headers=old,
    )
    assert r.status_code == 204
    assert client.get("/api/v1/auth/me", headers=old).status_code == 401
    r = client.post("/api/v1/auth/login", json={"username": "op1", "password": "NewPassword456"})
    assert r.status_code == 200


def test_password_policy(client, seeded, auth):
    r = client.post(
        "/api/v1/users",
        json={"username": "new.user", "full_name": "New", "role": "OPERATOR", "password": "short"},
        headers=auth("admin"),
    )
    assert r.status_code == 422
    assert "at least" in r.json()["detail"]


def test_admin_cannot_lock_themselves_out(client, seeded, auth):
    r = client.patch("/api/v1/users/admin", json={"active": False}, headers=auth("admin"))
    assert r.status_code == 409


def test_deactivated_user_session_stops_working(client, seeded, auth):
    op = auth("op1")
    client.patch("/api/v1/users/op1", json={"active": False}, headers=auth("admin"))
    assert client.get("/api/v1/auth/me", headers=op).status_code == 401


def test_pages_redirect_to_login(client, seeded):
    r = client.get("/orders?status=ON_HOLD", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=/orders")


def test_login_page_ignores_offsite_next(client, seeded):
    r = client.get("/login?next=//evil.example")
    assert 'data-redirect="/"' in r.text
