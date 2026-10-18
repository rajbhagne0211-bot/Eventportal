import hashlib
import hmac
import json

import razorpay

from services import payment_service as ps
from tests.conftest import make_event

REG = {"id": "11111111-1111-1111-1111-111111111111", "payment_status": "pending", "status": "pending", "contact_email": "a@b.co", "reg_code": "ASTRA26-00001"}


class FakeRz:
    def __init__(self, sig_ok=True, remote=None):
        self.created, self.sig_ok, self.remote = [], sig_ok, remote
        outer = self
        class Order:
            def create(self, data): outer.created.append(data); return {"id": "order_123"}
        class Util:
            def verify_payment_signature(self, d):
                if not outer.sig_ok: raise razorpay.errors.SignatureVerificationError("bad")
            def verify_webhook_signature(self, body, sig, secret):
                if not hmac.compare_digest(hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest(), sig):
                    raise razorpay.errors.SignatureVerificationError("bad")
        class Pay:
            def fetch(self, pid): return outer.remote
        self.order, self.utility, self.payment = Order(), Util(), Pay()


def setup_pay(monkeypatch, rz, rpc_calls):
    monkeypatch.setattr(ps, "_rz", lambda: rz)
    monkeypatch.setattr(ps, "get_settings", lambda: {"require_documents": False, "reservation_minutes": 15})
    monkeypatch.setattr(ps, "rpc", lambda name, params: rpc_calls.append((name, params)) or
                        ({"fee_paise": 25000, "reg_code": "ASTRA26-00001", "event_name": "E"} if name == "astra_reserve_seat" else {"already": False}))


def test_order_amount_comes_from_database(app, db, monkeypatch):
    rz, calls = FakeRz(), []
    setup_pay(monkeypatch, rz, calls)
    with app.app_context():
        out = ps.create_order(REG)
    assert rz.created[0]["amount"] == 25000 and out["amount"] == 25000 and out["order_id"] == "order_123"
    assert db.tables["payments"][0]["amount_paise"] == 25000


def test_open_order_is_reused_not_duplicated(app, db, monkeypatch):
    rz, calls = FakeRz(), []
    setup_pay(monkeypatch, rz, calls)
    with app.app_context():
        ps.create_order(REG); ps.create_order(REG)
    assert len(rz.created) == 1


def test_create_order_ignores_browser_amount(app, client, db, monkeypatch):
    from services import registration_service as rs
    rz, calls = FakeRz(), []
    setup_pay(monkeypatch, rz, calls)
    monkeypatch.setattr("routes.payments.rs.authorize", lambda i, t: REG)
    r = client.post(f"/api/astra/registrations/{REG['id']}/create-order", json={"amount": 1})
    assert r.status_code == 200 and r.get_json()["amount"] == 25000


def test_invalid_signature_rejected(app, db, monkeypatch):
    db.tables["payments"] = [{"registration_id": REG["id"], "razorpay_order_id": "order_1", "amount_paise": 25000, "currency": "INR", "status": "created"}]
    calls = []
    setup_pay(monkeypatch, FakeRz(sig_ok=False), calls)
    with app.app_context():
        try:
            ps.verify_payment(REG, "order_1", "pay_1", "bad")
            raise AssertionError("should have raised")
        except ps.ServiceError as e:
            assert e.status == 400
    assert not [c for c in calls if c[0] == "astra_mark_paid"]


def test_amount_mismatch_never_marks_paid(app, db, monkeypatch):
    db.tables["payments"] = [{"registration_id": REG["id"], "razorpay_order_id": "order_1", "amount_paise": 25000, "currency": "INR", "status": "created"}]
    calls = []
    setup_pay(monkeypatch, FakeRz(remote={"order_id": "order_1", "amount": 100, "currency": "INR", "status": "captured"}), calls)
    with app.app_context():
        try:
            ps.verify_payment(REG, "order_1", "pay_1", "sig")
            raise AssertionError("should have raised")
        except ps.ServiceError:
            pass
    assert not [c for c in calls if c[0] == "astra_mark_paid"]


def test_valid_payment_marks_paid(app, db, monkeypatch):
    db.tables["payments"] = [{"registration_id": REG["id"], "razorpay_order_id": "order_1", "amount_paise": 25000, "currency": "INR", "status": "created"}]
    calls = []
    setup_pay(monkeypatch, FakeRz(remote={"order_id": "order_1", "amount": 25000, "currency": "INR", "status": "captured", "method": "upi"}), calls)
    with app.app_context():
        assert ps.verify_payment(REG, "order_1", "pay_1", "sig")["status"] == "paid"
    assert [c for c in calls if c[0] == "astra_mark_paid"]


def signed(body, secret="whsec"):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def hook_body(event="payment.captured"):
    return json.dumps({"event": event, "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_1", "amount": 25000, "currency": "INR", "method": "upi"}}}}).encode()


def test_webhook_bad_signature_rejected(client, db, monkeypatch):
    r = client.post("/api/astra/payments/webhook", data=hook_body(), headers={"X-Razorpay-Signature": "nope", "X-Razorpay-Event-Id": "e1"})
    assert r.status_code == 400


def test_webhook_is_idempotent(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(ps, "rpc", lambda name, params: calls.append(name) or {"already": False})
    body = hook_body()
    h = {"X-Razorpay-Signature": signed(body), "X-Razorpay-Event-Id": "evt_1", "Content-Type": "application/json"}
    r1 = client.post("/api/astra/payments/webhook", data=body, headers=h)
    r2 = client.post("/api/astra/payments/webhook", data=body, headers=h)   # Razorpay retry
    assert r1.get_json()["status"] == "processed" and r2.get_json()["status"] == "duplicate"
    assert calls.count("astra_mark_paid") == 1


def test_failed_webhook_processing_can_be_retried(client, db, monkeypatch):
    def boom(name, params): raise RuntimeError("db down")
    monkeypatch.setattr(ps, "rpc", boom)
    body = hook_body()
    h = {"X-Razorpay-Signature": signed(body), "X-Razorpay-Event-Id": "evt_2"}
    assert client.post("/api/astra/payments/webhook", data=body, headers=h).status_code == 500
    assert db.tables["webhook_events"] == []     # claim released so Razorpay's retry is processed
