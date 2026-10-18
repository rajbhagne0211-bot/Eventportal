"""Razorpay endpoints: create order, verify payment, webhook."""
from flask import Blueprint, current_app, jsonify, request

from extensions import limiter
from services import email_service, payment_service, registration_service as rs

payments_bp = Blueprint("payments_api", __name__, url_prefix="/api/astra")


def _reg(registration_id):
    return rs.authorize(str(registration_id), request.headers.get("X-Registration-Token", ""))


@payments_bp.post("/registrations/<uuid:registration_id>/create-order")
@limiter.limit("10 per minute")
def create_order(registration_id):
    return jsonify(payment_service.create_order(_reg(registration_id)))   # no amount is read from the request


@payments_bp.post("/payments/verify")
@limiter.limit("20 per minute")
def verify():
    body = request.get_json(silent=True) or {}
    reg_id = body.get("registration_id", "")
    reg = rs.authorize(str(reg_id), request.headers.get("X-Registration-Token", ""))
    result = payment_service.verify_payment(reg, str(body.get("razorpay_order_id", "")),
                                            str(body.get("razorpay_payment_id", "")), str(body.get("razorpay_signature", "")))
    if result["status"] == "paid" and not result.get("already"):
        c = rs.confirmation_data(reg)
        email_service.send_confirmation(reg["contact_email"], reg["reg_code"], c["event"],
                                        f"{current_app.config['APP_BASE_URL']}/confirmation/{reg['id']}")
    return jsonify(result), (202 if result["status"] == "pending" else 200)


@payments_bp.post("/payments/webhook")
@limiter.exempt
def webhook():
    raw = request.get_data()                        # the exact bytes Razorpay signed
    outcome = payment_service.handle_webhook(raw, request.headers.get("X-Razorpay-Signature"),
                                             request.headers.get("X-Razorpay-Event-Id"))
    return jsonify(status=outcome), 200
