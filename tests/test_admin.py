import pytest

from routes import admin
from services import registration_service as rs

ENDPOINTS = ["dashboard", "events", "registrations", "participants", "payments", "documents", "departments", "settings"]


@pytest.mark.parametrize("name", ENDPOINTS)
def test_admin_api_requires_login(client, name):
    assert client.get(f"/admin/api/{name}").status_code == 401


@pytest.mark.parametrize("name", ENDPOINTS)
def test_admin_pages_redirect_to_login(client, name):
    r = client.get(f"/admin/{name}")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def login_as(client, role):
    with client.session_transaction() as s:
        s["admin"] = {"id": "u1", "email": "a@b.co", "role": role}
        s["csrf"] = "tok"


def test_write_without_csrf_is_rejected(client):
    login_as(client, "superadmin")
    assert client.post("/admin/api/departments", json={"name": "IT"}).status_code == 400


def test_verifier_cannot_manage_events_or_payments(client):
    login_as(client, "verifier")
    assert client.get("/admin/api/events").status_code == 403
    assert client.get("/admin/api/payments").status_code == 403
    assert client.get("/admin/api/settings").status_code == 403


def test_manager_cannot_open_identity_documents(client):
    login_as(client, "manager")
    assert client.get("/admin/api/documents").status_code == 403
    assert client.get("/admin/api/documents/11111111-1111-1111-1111-111111111111/url").status_code == 403


def test_manual_payment_is_superadmin_only(client):
    login_as(client, "manager")
    r = client.post("/admin/api/registrations/11111111-1111-1111-1111-111111111111/manual-payment", json={"reason": "x" * 20}, headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 403


def test_login_page_and_bad_login(client, db):
    assert client.get("/admin/login").status_code == 200
    with client.session_transaction() as s:
        s["csrf"] = "tok"
    r = client.post("/admin/login", data={"email": "no@x.co", "password": "bad", "csrf_token": "tok"})
    assert r.status_code == 401


def test_csv_blocks_formula_injection():
    out = admin.csv_response([{"a": "=HYPERLINK(1)", "b": "ok"}], ["a", "b"], "x.csv").get_data(as_text=True)
    assert "'=HYPERLINK(1)" in out


def test_clean_event_validation():
    with pytest.raises(rs.ValidationError):
        admin.clean_event({"name": "", "category": "X", "allow_team": True})
    with pytest.raises(rs.ValidationError):
        admin.clean_event({"name": "A", "category": "X", "allow_team": True, "min_team_size": 5, "max_team_size": 2})
    out = admin.clean_event({"name": "A", "category": "X", "allow_team": True, "fee": "250.50", "registration_status": "open"})
    assert out["fee_paise"] == 25050


def test_security_headers_present(client):
    r = client.get("/healthz")
    assert r.headers["X-Frame-Options"] == "DENY" and "Content-Security-Policy" in r.headers
