"""Registration rules: validation, creation, confirmation data. Business logic lives here, not in routes."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone

from supabase_client import get_client

PHONE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9]{32,128}$")
YEARS = {"FY", "SY", "TY", "LY", "PG"}

# Database exception codes (raised inside the SQL functions) -> (HTTP status, message shown to the user)
ERRORS = {
    "EVENT_NOT_FOUND": (404, "That event does not exist."),
    "EVENT_CLOSED": (409, "Registration for this event is closed or not open yet."),
    "EVENT_FULL": (409, "This event is full."),
    "FORMAT_NOT_ALLOWED": (400, "That registration type is not available for this event."),
    "INVALID_TEAM_SIZE": (400, "The number of participants does not match this event's team size."),
    "TOO_MANY_SUBSTITUTES": (400, "Too many substitutes for this event."),
    "DUPLICATE_REGISTRATION": (409, "This email is already registered for this event. Contact the organisers if you need help."),
    "IDEMPOTENCY_CONFLICT": (409, "This submission conflicts with an earlier one. Please reload and try again."),
    "REGISTRATION_NOT_FOUND": (404, "Registration not found."),
    "ALREADY_PAID": (409, "This registration is already paid."),
    "REGISTRATION_CLOSED": (409, "This registration can no longer accept payment."),
    "NO_FEE_DUE": (400, "No fee is due for this event."),
}


class ValidationError(Exception):
    def __init__(self, details):
        super().__init__("Validation failed")
        self.details = details


class ServiceError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def translate_db_error(exc):
    """Turn a Postgres RAISE EXCEPTION 'CODE' into a ServiceError; re-raise anything unexpected."""
    text = str(exc)
    for code, (status, message) in ERRORS.items():
        if code in text:
            return ServiceError(message, status, code)
    return None


def rpc(name, params):
    try:
        return get_client().rpc(name, params).execute().data
    except Exception as exc:  # postgrest APIError
        mapped = translate_db_error(exc)
        if mapped:
            raise mapped from exc
        raise


# ---------- settings ----------
DEFAULT_SETTINGS = {"require_documents": True, "manual_approval": False, "allow_manual_payment": False,
                    "reservation_minutes": 15, "document_retention_days": 90}


def get_settings():
    rows = get_client().table("app_settings").select("key,value").execute().data or []
    merged = dict(DEFAULT_SETTINGS)
    merged.update({r["key"]: r["value"] for r in rows})
    return merged


# ---------- events ----------
def _rupees(paise):
    return paise // 100 if paise % 100 == 0 else paise / 100


def public_event(e, seats_left, now):
    reason = None
    if e["registration_status"] != "open":
        reason = "registration not open yet"
    elif e.get("registration_opens_at") and now < _dt(e["registration_opens_at"]):
        reason = "registration opens soon"
    elif e.get("registration_closes_at") and now > _dt(e["registration_closes_at"]):
        reason = "registration closed"
    elif e["allow_team"] and not e["allow_individual"] and (e["min_team_size"] is None or e["max_team_size"] is None):
        reason = "team size to be confirmed"
    elif seats_left is not None and seats_left <= 0:
        reason = "event full"
    formats = (["individual"] if e["allow_individual"] else []) + (["team"] if e["allow_team"] else [])
    return {
        "id": e["id"], "name": e["name"], "category": e["category"], "subcategory": e["subcategory"] or "",
        "description": e["description"] or "", "formats": formats,
        "min_size": e["min_team_size"], "max_size": e["max_team_size"], "sub_max": e["max_substitutes"],
        "fee": _rupees(e["fee_paise"]), "seats_left": seats_left,
        "registration_open": reason is None, "closed_reason": reason,
        "extra_fields": e["extra_fields"] or [],
    }


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def list_events():
    sb = get_client()
    events = sb.table("events").select("*").eq("is_active", True).order("id").execute().data or []
    stats = {s["id"]: s["seats_left"] for s in (sb.table("v_event_stats").select("id,seats_left").execute().data or [])}
    now = datetime.now(timezone.utc)
    order = {}
    for e in events:
        order.setdefault(e["category"], len(order))
    out = []
    for e in events:
        item = public_event(e, stats.get(e["id"]), now)
        item["cat_sort"] = order[e["category"]]
        out.append(item)
    return out


def get_event_row(event_id):
    rows = get_client().table("events").select("*").eq("id", event_id).eq("is_active", True).limit(1).execute().data
    if not rows:
        raise ServiceError("Event not found.", 404)
    return rows[0]


# ---------- validation ----------
def team_bounds(event, fmt):
    if fmt == "individual":
        return 1, 1
    lo, hi = event["min_team_size"], event["max_team_size"]
    if lo is None or hi is None:
        raise ServiceError("Team size for this event is not confirmed yet.", 409, "EVENT_CLOSED")
    if event["allow_individual"]:
        lo = max(2, lo)
    return lo, hi


def _s(value, label, errors, maxlen=100, required=True):
    value = (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value).strip())
    if required and not value:
        errors.append(f"{label} is required")
    elif len(value) > maxlen:
        errors.append(f"{label} is too long")
    return value[:maxlen]


def _person(raw, label, role, errors, lead):
    raw = raw if isinstance(raw, dict) else {}
    p = {"full_name": _s(raw.get("name"), f"{label} name", errors, 100),
         "department": _s(raw.get("dept"), f"{label} department", errors, 80),
         "course_year": _s(raw.get("year"), f"{label} year", errors, 10),
         "roll_number": _s(raw.get("roll"), f"{label} roll number", errors, 30),
         "email": _s(raw.get("email"), f"{label} email", errors, 254, required=lead).lower(),
         "mobile": _s(raw.get("phone"), f"{label} mobile", errors, 10, required=lead), "role": role}
    if p["course_year"] and p["course_year"] not in YEARS:
        errors.append(f"{label} year is invalid")
    if p["email"] and not EMAIL_RE.match(p["email"]):
        errors.append(f"{label} email is invalid")
    if p["mobile"] and not PHONE_RE.match(p["mobile"]):
        errors.append(f"{label} mobile must be a 10-digit Indian number")
    return p


def validate_registration(data, event):
    """Returns a clean dict or raises ValidationError. Never trusts the browser."""
    errors = []
    if not isinstance(data, dict):
        raise ValidationError(["Invalid request"])
    fmt = data.get("format")
    if fmt not in ("individual", "team"):
        raise ValidationError(["Choose individual or team registration"])
    if (fmt == "individual" and not event["allow_individual"]) or (fmt == "team" and not event["allow_team"]):
        raise ValidationError(["That registration type is not available for this event"])
    lo, hi = team_bounds(event, fmt)
    common = data.get("common") if isinstance(data.get("common"), dict) else {}
    members = data.get("members") if isinstance(data.get("members"), list) else []
    subs = data.get("subs") if isinstance(data.get("subs"), list) else []
    if not lo <= len(members) <= hi:
        errors.append(f"This event needs {lo}" + (f"–{hi}" if hi != lo else "") + " participant(s)")
    max_subs = event["max_substitutes"] if fmt == "team" else 0
    if len(subs) > max_subs:
        errors.append(f"At most {max_subs} substitute(s) allowed")
    if data.get("consent") is not True:
        errors.append("Consent is required")
    key = data.get("key")
    if not isinstance(key, str) or not 8 <= len(key) <= 100:
        errors.append("Missing submission key")
    secret = data.get("secret")
    if not isinstance(secret, str) or not SECRET_RE.match(secret):
        errors.append("Missing or weak access secret")

    clean_people = []
    for i, m in enumerate(members[:hi]):
        role = "individual" if fmt == "individual" else ("captain" if i == 0 else "member")
        label = "Participant" if fmt == "individual" else ("Captain" if i == 0 else f"Member {i + 1}")
        clean_people.append(_person(m, label, role, errors, lead=(i == 0)))
    for i, m in enumerate(subs[:max_subs]):
        clean_people.append(_person(m, f"Substitute {i + 1}", "substitute", errors, lead=False))

    extra_in = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    extra = {}
    for f in event["extra_fields"] or []:
        val = _s(extra_in.get(f["key"]), f["label"], errors, 1000, required=bool(f.get("required")))
        if val and f.get("type") == "url" and not re.match(r"^https?://[^\s]+$", val):
            errors.append(f"{f['label']} must be a valid http(s) link")
        if val:
            extra[f["key"]] = val

    team_name = _s(common.get("team_name"), "Team name", errors, 80, required=(fmt == "team")) if fmt == "team" else ""
    college = _s(common.get("college"), "College name", errors, 150)
    contingent = _s(common.get("contingent_code"), "Contingent code", errors, 30, required=False)
    if errors:
        raise ValidationError(errors)
    lead = clean_people[0]
    return {"event_id": event["id"], "format": fmt, "team_name": team_name, "college_name": college,
            "contingent_code": contingent, "contact_email": lead["email"], "contact_phone": lead["mobile"],
            "extra": extra, "participants": clean_people, "idempotency_key": key, "secret": secret}


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def create_registration(data):
    event = get_event_row(int(data.get("event_id") or 0)) if str(data.get("event_id", "")).isdigit() else None
    if event is None:
        raise ServiceError("Choose an event.", 400)
    clean = validate_registration(data, event)
    settings = get_settings()
    params = {k: clean[k] for k in ("event_id", "format", "team_name", "college_name", "contingent_code",
                                    "contact_email", "contact_phone", "extra", "participants", "idempotency_key")}
    params.update({"access_token_hash": hash_secret(clean["secret"]),
                   "verification_token": secrets.token_urlsafe(24),
                   "manual_approval": bool(settings["manual_approval"]),
                   "reservation_minutes": int(settings["reservation_minutes"])})
    result = rpc("astra_create_registration", {"p": params})
    return {"registration_id": result["id"], "reg_code": result["reg_code"], "status": result["status"],
            "payment_status": result["payment_status"], "fee": _rupees(result["fee_paise"]),
            "participants": result["participants"], "event": event["name"], "replayed": result["replayed"]}


# ---------- access to one registration ----------
def authorize(registration_id, secret):
    """The registrant's browser proves ownership with the secret it generated; we compare hashes."""
    rows = get_client().table("registrations").select("*").eq("id", registration_id).limit(1).execute().data
    if not rows or not isinstance(secret, str) or not hmac.compare_digest(rows[0]["access_token_hash"], hash_secret(secret)):
        raise ServiceError("Registration not found.", 404)
    return rows[0]


def mask_name(name):
    return " ".join((w[0] + "*" * min(len(w) - 1, 4)) if i else w for i, w in enumerate((name or "").split())) or "—"


def confirmation_data(reg):
    sb = get_client()
    ev = sb.table("events").select("name,category,subcategory,fee_paise").eq("id", reg["event_id"]).single().execute().data
    people = sb.table("participants").select("full_name,department,role").eq("registration_id", reg["id"]).order("created_at").execute().data or []
    lead = next((p for p in people if p["role"] in ("individual", "captain")), people[0] if people else {})
    docs = sb.table("registration_documents").select("status,doc_type").eq("registration_id", reg["id"]).is_("deleted_at", "null").execute().data or []
    return {
        "reg_code": reg["reg_code"], "event": ev["name"], "category": ev["category"], "subcategory": ev["subcategory"],
        "format": reg["format"], "team_name": reg["team_name"], "lead_name": lead.get("full_name"),
        "participant_count": len([p for p in people if p["role"] != "substitute"]),
        "college": reg["college_name"], "department": lead.get("department"),
        "registered_on": reg["created_at"], "status": reg["status"], "payment_status": reg["payment_status"],
        "fee": _rupees(ev["fee_paise"]), "verification_token": reg["verification_token"],
        "documents_pending": any(d["status"] == "pending" for d in docs),
        "documents_rejected": any(d["status"] == "rejected" for d in docs),
        "college_name": "Sathaye College, Vile Parle, Mumbai",
    }


def verify_by_token(token):
    """Public: reveals the minimum needed to confirm a registration is genuine."""
    if not isinstance(token, str) or not 16 <= len(token) <= 64:
        return {"valid": False}
    rows = get_client().table("registrations").select("id,reg_code,event_id,status,payment_status,format,team_name") \
        .eq("verification_token", token).limit(1).execute().data
    if not rows:
        return {"valid": False}
    r = rows[0]
    ev = get_client().table("events").select("name").eq("id", r["event_id"]).single().execute().data
    people = get_client().table("participants").select("full_name,role").eq("registration_id", r["id"]).execute().data or []
    lead = next((p["full_name"] for p in people if p["role"] in ("individual", "captain")), "")
    return {"valid": True, "reg_code": r["reg_code"], "event": ev["name"], "status": r["status"],
            "payment_status": r["payment_status"], "holder": r["team_name"] or mask_name(lead)}
