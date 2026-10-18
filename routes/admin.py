"""Admin panel: pages + JSON API. Every API route checks login, role and CSRF on the server."""
import csv
import io
import re
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import limiter
from services import registration_service as rs, storage_service
from supabase_client import get_client

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

PERMS = {  # role -> sections it may use
    "superadmin": {"dashboard", "events", "registrations", "participants", "payments", "documents", "departments", "settings", "export", "manual_payment"},
    "manager": {"dashboard", "events", "registrations", "participants", "payments", "departments", "export"},
    "verifier": {"dashboard", "registrations", "participants", "documents"},
}
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def current_admin():
    return session.get("admin")


def require(section, api=True):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            admin = current_admin()
            if not admin:
                return (jsonify(error="Login required."), 401) if api else redirect(url_for("admin.login"))
            if section not in PERMS.get(admin["role"], set()):
                return (jsonify(error="You do not have permission for this."), 403) if api else ("Forbidden", 403)
            return fn(*a, **kw)
        return wrapper
    return deco


def audit(action, target_type=None, target_id=None, detail=None):
    a = current_admin() or {}
    get_client().table("audit_log").insert({"admin_id": a.get("id"), "admin_email": a.get("email"), "action": action,
                                            "target_type": target_type, "target_id": str(target_id) if target_id else None,
                                            "detail": detail, "ip": request.remote_addr}).execute()


def safe_q(q):
    return re.sub(r"[^\w@.\- ]", "", q or "")[:60]


def csv_response(rows, columns, filename):
    def cell(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s      # block spreadsheet formula injection
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([cell(r.get(c)) for c in columns])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


# ------------------------------------------------------------------ pages
@admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute; 30 per hour", methods=["POST"])
def login():
    if request.method == "GET":
        return render_template("admin/login.html", error=None)
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    rows = get_client().table("admin_users").select("*").eq("email", email).eq("active", True).limit(1).execute().data
    user = rows[0] if rows else None
    ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password) and user is not None
    if not ok:
        return render_template("admin/login.html", error="Wrong e-mail or password."), 401
    session.clear()                                  # new session on login (prevents fixation)
    session["admin"] = {"id": user["id"], "email": user["email"], "role": user["role"]}
    session.permanent = True
    get_client().table("admin_users").update({"last_login_at": datetime.now(timezone.utc).isoformat()}).eq("id", user["id"]).execute()
    audit("login")
    return redirect(url_for("admin.page", name="dashboard"))


@admin_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
def index():
    return redirect(url_for("admin.page", name="dashboard"))


PAGES = {"dashboard": "dashboard", "events": "events", "registrations": "registrations", "participants": "participants",
         "payments": "payments", "documents": "documents", "departments": "departments", "settings": "settings"}


@admin_bp.get("/<name>")
def page(name):
    if name not in PAGES:
        return "Not found", 404
    admin = current_admin()
    if not admin:
        return redirect(url_for("admin.login"))
    if PAGES[name] not in PERMS.get(admin["role"], set()):
        return "Forbidden", 403
    return render_template(f"admin/{name}.html", admin=admin, page=name, perms=sorted(PERMS[admin["role"]]))


# ------------------------------------------------------------------ dashboard
@admin_bp.get("/api/dashboard")
@require("dashboard")
def dashboard():
    sb = get_client()
    return jsonify(totals=sb.table("v_dashboard").select("*").execute().data[0],
                   events=sb.table("v_event_stats").select("*").order("id").execute().data,
                   departments=sb.table("v_department_stats").select("*").execute().data)


# ------------------------------------------------------------------ events
EVENT_TEXT = {"name": 150, "category": 60, "subcategory": 60, "description": 1000}


def clean_event(body, partial=False):
    out, errors = {}, []
    for k, n in EVENT_TEXT.items():
        if k in body:
            v = str(body[k] or "").strip()
            if k in ("name", "category") and not v:
                errors.append(f"{k} is required")
            out[k] = v[:n]
        elif not partial and k in ("name", "category"):
            errors.append(f"{k} is required")
    for k in ("allow_individual", "allow_team", "is_active"):
        if k in body:
            out[k] = bool(body[k])
    for k in ("min_team_size", "max_team_size", "capacity", "max_substitutes", "department_id"):
        if k in body:
            v = body[k]
            if v in (None, ""):
                out[k] = 0 if k == "max_substitutes" else None
            elif isinstance(v, (int, str)) and str(v).isdigit() and int(v) >= 0:
                out[k] = int(v)
            else:
                errors.append(f"{k} must be a whole number")
    if "fee" in body:
        try:
            paise = round(float(body["fee"] or 0) * 100)
            if paise < 0:
                raise ValueError
            out["fee_paise"] = paise
        except (TypeError, ValueError):
            errors.append("fee must be a positive amount in rupees")
    for k in ("registration_opens_at", "registration_closes_at"):
        if k in body:
            out[k] = body[k] or None
    if "registration_status" in body:
        if body["registration_status"] not in ("draft", "open", "closed"):
            errors.append("registration_status must be draft, open or closed")
        out["registration_status"] = body["registration_status"]
    if "extra_fields" in body:
        fields = body["extra_fields"] if isinstance(body["extra_fields"], list) else []
        for f in fields:
            if not (isinstance(f, dict) and re.match(r"^[a-z_]{2,30}$", str(f.get("key", ""))) and f.get("label")
                    and f.get("type") in ("text", "url", "textarea")):
                errors.append("extra_fields entries need key (a-z_), label and type (text/url/textarea)")
                break
        out["extra_fields"] = fields
    if out.get("min_team_size") and out.get("max_team_size") and out["min_team_size"] > out["max_team_size"]:
        errors.append("min team size cannot exceed max")
    if not partial and not (out.get("allow_individual") or out.get("allow_team")):
        errors.append("enable individual and/or team registration")
    if errors:
        raise rs.ValidationError(errors)
    return out


@admin_bp.get("/api/events")
@require("events")
def events_list():
    rows = get_client().table("events").select("*").order("id").execute().data
    stats = {s["id"]: s for s in get_client().table("v_event_stats").select("id,registered,seats_left").execute().data}
    for r in rows:
        r.update({"registered": stats[r["id"]]["registered"], "seats_left": stats[r["id"]]["seats_left"], "fee": r["fee_paise"] / 100})
    return jsonify(events=rows)


@admin_bp.post("/api/events")
@require("events")
def events_create():
    data = clean_event(request.get_json(silent=True) or {})
    row = get_client().table("events").insert(data).execute().data[0]
    audit("event.create", "event", row["id"], data)
    return jsonify(row), 201


@admin_bp.put("/api/events/<int:event_id>")
@require("events")
def events_update(event_id):
    data = clean_event(request.get_json(silent=True) or {}, partial=True)
    if not data:
        raise rs.ServiceError("Nothing to update.", 400)
    rows = get_client().table("events").update(data).eq("id", event_id).execute().data
    if not rows:
        raise rs.ServiceError("Event not found.", 404)
    audit("event.update", "event", event_id, data)
    return jsonify(rows[0])


@admin_bp.delete("/api/events/<int:event_id>")
@require("events")
def events_deactivate(event_id):                     # soft delete: registrations keep their event
    get_client().table("events").update({"is_active": False, "registration_status": "closed"}).eq("id", event_id).execute()
    audit("event.deactivate", "event", event_id)
    return jsonify(ok=True)


PCOLS = ["reg_code", "event_name", "category", "reg_status", "payment_status", "team_name", "college_name", "full_name", "role",
         "department", "course_year", "roll_number", "email", "mobile"]


@admin_bp.get("/api/events/<int:event_id>/export.csv")
@require("export")
def event_export(event_id):
    rows = get_client().table("v_participants_admin").select("*").eq("event_id", event_id).execute().data or []
    regs = {r["id"]: r for r in get_client().table("registrations").select("id,team_name").eq("event_id", event_id).execute().data or []}
    for r in rows:
        r["team_name"] = regs.get(r["registration_id"], {}).get("team_name")
    audit("export.event", "event", event_id)
    return csv_response(rows, PCOLS, f"astra-event-{event_id}.csv")


# ------------------------------------------------------------------ registrations
def _reg_query():
    q = get_client().table("v_registrations_admin").select("*")
    s = safe_q(request.args.get("q"))
    if s:
        q = q.or_(",".join(f"{c}.ilike.*{s}*" for c in ("reg_code", "contact_email", "lead_name", "team_name", "event_name", "college_name")))
    for arg, col in (("category", "category"), ("event_id", "event_id"), ("payment_status", "payment_status"), ("status", "status")):
        if request.args.get(arg):
            q = q.eq(col, request.args[arg])
    return q.order("created_at", desc=True)


@admin_bp.get("/api/registrations")
@require("registrations")
def registrations_list():
    return jsonify(registrations=_reg_query().limit(300).execute().data)


RCOLS = ["reg_code", "event_name", "category", "format", "team_name", "lead_name", "participant_count", "college_name",
         "contact_email", "contact_phone", "status", "payment_status", "created_at"]


@admin_bp.get("/api/registrations/export.csv")
@require("export")
def registrations_export():
    audit("export.registrations", detail=dict(request.args))
    return csv_response(_reg_query().limit(5000).execute().data, RCOLS, "astra-registrations.csv")


@admin_bp.get("/api/registrations/<uuid:rid>")
@require("registrations")
def registration_detail(rid):
    sb = get_client()
    reg = sb.table("registrations").select("id,reg_code,event_id,format,team_name,college_name,contingent_code,contact_email,contact_phone,extra,status,payment_status,needs_review,created_at,consent_at") \
        .eq("id", str(rid)).limit(1).execute().data
    if not reg:
        raise rs.ServiceError("Not found.", 404)
    return jsonify(registration=reg[0],
                   event=sb.table("events").select("name,category,fee_paise").eq("id", reg[0]["event_id"]).single().execute().data,
                   participants=sb.table("participants").select("*").eq("registration_id", str(rid)).order("created_at").execute().data,
                   payments=sb.table("v_payments_admin").select("*").eq("registration_id", str(rid)).execute().data,
                   documents=sb.table("v_documents_admin").select("*").eq("registration_id", str(rid)).execute().data)


@admin_bp.post("/api/registrations/<uuid:rid>/status")
@require("registrations")
def registration_status(rid):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("pending", "confirmed", "rejected", "cancelled", "waitlisted"):
        raise rs.ServiceError("Invalid status.", 400)
    sb = get_client()
    reg = sb.table("registrations").select("payment_status,event_id").eq("id", str(rid)).single().execute().data
    fee = sb.table("events").select("fee_paise").eq("id", reg["event_id"]).single().execute().data["fee_paise"]
    if status == "confirmed" and fee > 0 and reg["payment_status"] != "paid":
        raise rs.ServiceError("A paid event cannot be confirmed until payment is verified.", 409)
    sb.table("registrations").update({"status": status}).eq("id", str(rid)).execute()
    audit("registration.status", "registration", rid, {"status": status})
    return jsonify(ok=True)


@admin_bp.post("/api/registrations/<uuid:rid>/manual-payment")
@require("manual_payment")
def manual_payment(rid):
    """Only when the college explicitly allows manual payment review. Superadmin only; always audited."""
    if not rs.get_settings()["allow_manual_payment"]:
        raise rs.ServiceError("Manual payment review is switched off in Settings.", 403)
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason", "")).strip()
    if len(reason) < 10:
        raise rs.ServiceError("Give a reason (min 10 characters), e.g. the screenshot's transaction reference.", 400)
    sb = get_client()
    reg = sb.table("registrations").select("id,event_id,payment_status,requires_approval").eq("id", str(rid)).single().execute().data
    if reg["payment_status"] == "paid":
        raise rs.ServiceError("Already paid.", 409)
    fee = sb.table("events").select("fee_paise").eq("id", reg["event_id"]).single().execute().data["fee_paise"]
    sb.table("payments").update({"status": "cancelled"}).eq("registration_id", str(rid)).eq("status", "created").execute()
    sb.table("payments").insert({"registration_id": str(rid), "amount_paise": fee, "status": "paid", "method": "manual",
                                 "verified_at": datetime.now(timezone.utc).isoformat(), "failure_reason": reason[:300]}).execute()
    sb.table("registrations").update({"payment_status": "paid", "reserved_until": None,
                                      "status": "pending" if reg["requires_approval"] else "confirmed"}).eq("id", str(rid)).execute()
    audit("payment.manual", "registration", rid, {"reason": reason})
    return jsonify(ok=True)


# ------------------------------------------------------------------ participants / payments
@admin_bp.get("/api/participants")
@require("participants")
def participants_list():
    q = get_client().table("v_participants_admin").select("*")
    s = safe_q(request.args.get("q"))
    if s:
        q = q.or_(",".join(f"{c}.ilike.*{s}*" for c in ("full_name", "reg_code", "email", "roll_number", "event_name")))
    if request.args.get("event_id"):
        q = q.eq("event_id", request.args["event_id"])
    return jsonify(participants=q.limit(500).execute().data)


@admin_bp.get("/api/payments")
@require("payments")
def payments_list():
    q = get_client().table("v_payments_admin").select("*").order("created_at", desc=True)
    if request.args.get("status"):
        q = q.eq("status", request.args["status"])
    return jsonify(payments=q.limit(500).execute().data)


# ------------------------------------------------------------------ documents (verifier / superadmin only)
@admin_bp.get("/api/documents")
@require("documents")
def documents_list():
    q = get_client().table("v_documents_admin").select("*").order("uploaded_at", desc=True)
    for arg in ("status", "event_id"):
        if request.args.get(arg):
            q = q.eq(arg, request.args[arg])
    return jsonify(documents=q.limit(500).execute().data)


@admin_bp.get("/api/documents/<uuid:did>/url")
@require("documents")
def document_url(did):
    row = get_client().table("registration_documents").select("storage_path").eq("id", str(did)).is_("deleted_at", "null").limit(1).execute().data
    if not row:
        raise rs.ServiceError("Not found.", 404)
    audit("document.view", "document", did)          # every view of an identity document is logged
    return jsonify(url=storage_service.signed_url(row[0]["storage_path"], current_app.config, 60), expires_in=60)


@admin_bp.post("/api/documents/<uuid:did>/review")
@require("documents")
def document_review(did):
    body = request.get_json(silent=True) or {}
    status, notes = body.get("status"), str(body.get("notes", "")).strip()[:500]
    if status not in ("pending", "verified", "rejected"):
        raise rs.ServiceError("Invalid status.", 400)
    if status == "rejected" and not notes:
        raise rs.ServiceError("Give a reason so the participant knows what to re-upload.", 400)
    get_client().table("registration_documents").update({"status": status, "review_notes": notes or None,
        "reviewed_by": current_admin()["id"], "reviewed_at": datetime.now(timezone.utc).isoformat()}).eq("id", str(did)).execute()
    audit("document.review", "document", did, {"status": status, "notes": notes})
    return jsonify(ok=True)


# ------------------------------------------------------------------ departments & settings
@admin_bp.get("/api/departments")
@require("departments")
def departments_list():
    return jsonify(departments=get_client().table("departments").select("*").order("name").execute().data)


@admin_bp.post("/api/departments")
@require("departments")
def departments_create():
    name = str((request.get_json(silent=True) or {}).get("name", "")).strip()[:80]
    if not name:
        raise rs.ServiceError("Name is required.", 400)
    row = get_client().table("departments").insert({"name": name}).execute().data[0]
    audit("department.create", "department", row["id"], {"name": name})
    return jsonify(row), 201


@admin_bp.put("/api/departments/<int:did>")
@require("departments")
def departments_update(did):
    body = request.get_json(silent=True) or {}
    data = {}
    if "name" in body and str(body["name"]).strip():
        data["name"] = str(body["name"]).strip()[:80]
    if "active" in body:
        data["active"] = bool(body["active"])
    get_client().table("departments").update(data).eq("id", did).execute()
    audit("department.update", "department", did, data)
    return jsonify(ok=True)


SETTING_TYPES = {"require_documents": bool, "manual_approval": bool, "allow_manual_payment": bool,
                 "reservation_minutes": int, "document_retention_days": int}


@admin_bp.get("/api/settings")
@require("settings")
def settings_get():
    return jsonify(rs.get_settings())


@admin_bp.put("/api/settings")
@require("settings")
def settings_put():
    body = request.get_json(silent=True) or {}
    for k, v in body.items():
        t = SETTING_TYPES.get(k)
        if not t or not isinstance(v, t) or (t is int and (isinstance(v, bool) or not 1 <= v <= 3650)):
            raise rs.ServiceError(f"Invalid setting: {k}", 400)
        get_client().table("app_settings").upsert({"key": k, "value": v}).execute()
    audit("settings.update", detail=body)
    return jsonify(rs.get_settings())
