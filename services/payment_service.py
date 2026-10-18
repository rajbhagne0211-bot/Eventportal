"""Razorpay orders, signature verification and webhooks.

Golden rules: the amount always comes from the database; a payment counts as paid only after
(a) the Razorpay signature checks out AND (b) Razorpay's own API confirms the payment — or a signed webhook says so.
"""
import json
import logging

import razorpay
from flask import current_app

from services.registration_service import ServiceError, get_settings, rpc
from supabase_client import get_client

log = logging.getLogger(__name__)


def _rz():
    cfg = current_app.config
    if not cfg["RAZORPAY_KEY_ID"] or not cfg["RAZORPAY_KEY_SECRET"]:
        raise ServiceError("Online payment is not configured yet.", 503)
    return razorpay.Client(auth=(cfg["RAZORPAY_KEY_ID"], cfg["RAZORPAY_KEY_SECRET"]))


def _docs_ok(reg_id):
    """When documents are required, every non-substitute participant needs a live (non-rejected) ID document."""
    sb = get_client()
    people = sb.table("participants").select("id").eq("registration_id", reg_id).neq("role", "substitute").execute().data or []
    docs = sb.table("registration_documents").select("participant_id,doc_type,status").eq("registration_id", reg_id) \
        .is_("deleted_at", "null").neq("status", "rejected").execute().data or []
    have = {d["participant_id"] for d in docs if d["doc_type"] in ("college_id", "identity_proof", "aadhaar_masked")}
    return all(p["id"] in have for p in people)


def create_order(reg):
    settings = get_settings()
    if reg["payment_status"] == "paid":
        raise ServiceError("This registration is already paid.", 409)
    if settings["require_documents"] and not _docs_ok(reg["id"]):
        raise ServiceError("Upload a college ID for every participant before paying.", 409, "DOCUMENTS_REQUIRED")
    held = rpc("astra_reserve_seat", {"p_reg": reg["id"], "p_minutes": int(settings["reservation_minutes"])})
    fee = int(held["fee_paise"])                       # <- from the database, never from the browser
    sb = get_client()
    cfg = current_app.config

    def pack(row):
        return {"order_id": row["razorpay_order_id"], "amount": row["amount_paise"], "currency": row["currency"],
                "key_id": cfg["RAZORPAY_KEY_ID"], "reg_code": held["reg_code"]}

    open_orders = sb.table("payments").select("*").eq("registration_id", reg["id"]).eq("status", "created").execute().data or []
    if open_orders and open_orders[0]["amount_paise"] == fee:
        return pack(open_orders[0])                    # reuse -> no duplicate orders
    for o in open_orders:                              # fee changed since: retire the old order
        sb.table("payments").update({"status": "cancelled"}).eq("id", o["id"]).execute()
    order = _rz().order.create({"amount": fee, "currency": "INR", "receipt": held["reg_code"],
                                "notes": {"registration_id": reg["id"], "reg_code": held["reg_code"]}})
    try:
        row = sb.table("payments").insert({"registration_id": reg["id"], "razorpay_order_id": order["id"],
                                           "amount_paise": fee, "currency": "INR"}).execute().data[0]
    except Exception:                                  # lost a race with a parallel request
        row = sb.table("payments").select("*").eq("registration_id", reg["id"]).eq("status", "created").execute().data[0]
    return pack(row)


def verify_payment(reg, order_id, payment_id, signature):
    sb = get_client()
    pay = sb.table("payments").select("*").eq("razorpay_order_id", order_id).eq("registration_id", reg["id"]).limit(1).execute().data
    if not pay:
        raise ServiceError("Order does not belong to this registration.", 400)
    pay = pay[0]
    client = _rz()
    try:
        client.utility.verify_payment_signature({"razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
                                                 "razorpay_signature": signature})
    except razorpay.errors.SignatureVerificationError:
        raise ServiceError("Payment signature is invalid.", 400, "BAD_SIGNATURE")
    remote = client.payment.fetch(payment_id)          # ask Razorpay directly; do not trust the browser
    if remote.get("order_id") != order_id or remote.get("amount") != pay["amount_paise"] or remote.get("currency") != pay["currency"]:
        raise ServiceError("Payment details do not match the order.", 400, "MISMATCH")
    if remote["status"] == "authorized":
        remote = client.payment.capture(payment_id, pay["amount_paise"], {"currency": pay["currency"]})
    if remote["status"] == "captured":
        result = rpc("astra_mark_paid", {"p_order": order_id, "p_payment": payment_id, "p_method": remote.get("method"),
                                         "p_amount": pay["amount_paise"], "p_currency": pay["currency"]})
        return {"status": "paid", "already": result["already"]}
    if remote["status"] == "failed":
        rpc("astra_mark_failed", {"p_order": order_id, "p_payment": payment_id, "p_reason": remote.get("error_description")})
        return {"status": "failed"}
    return {"status": "pending"}                       # the webhook will finalise it


def handle_webhook(raw_body, signature, event_id):
    """raw_body must be the exact bytes Razorpay sent. Returns a short status string."""
    secret = current_app.config["RAZORPAY_WEBHOOK_SECRET"]
    if not secret:
        raise ServiceError("Webhook not configured.", 503)
    try:
        _rz().utility.verify_webhook_signature(raw_body.decode("utf-8"), signature or "", secret)
    except razorpay.errors.SignatureVerificationError:
        raise ServiceError("Invalid webhook signature.", 400, "BAD_SIGNATURE")
    event = json.loads(raw_body)
    etype = event.get("event", "")
    event_id = event_id or f"{etype}:{event.get('created_at')}:{json.dumps(event.get('payload', {}), sort_keys=True)[:200]}"
    sb = get_client()
    try:
        sb.table("webhook_events").insert({"event_id": event_id, "event_type": etype}).execute()
    except Exception:
        return "duplicate"                             # Razorpay retried an event we already handled
    try:
        entity = (event.get("payload", {}).get("payment") or {}).get("entity") or {}
        if etype in ("payment.captured", "order.paid") and entity.get("order_id"):
            rpc("astra_mark_paid", {"p_order": entity["order_id"], "p_payment": entity["id"], "p_method": entity.get("method"),
                                    "p_amount": entity["amount"], "p_currency": entity["currency"], "p_webhook_event": event_id})
        elif etype == "payment.failed" and entity.get("order_id"):
            rpc("astra_mark_failed", {"p_order": entity["order_id"], "p_payment": entity.get("id"),
                                      "p_reason": entity.get("error_description")})
        elif etype in ("refund.processed", "refund.created"):
            refund = (event.get("payload", {}).get("refund") or {}).get("entity") or {}
            if etype == "refund.processed" and refund.get("payment_id"):
                rpc("astra_mark_refunded", {"p_payment": refund["payment_id"]})
        else:
            return "ignored"
    except Exception:
        sb.table("webhook_events").delete().eq("event_id", event_id).execute()   # allow Razorpay's retry to reprocess
        log.exception("webhook processing failed")
        raise
    return "processed"
