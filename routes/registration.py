"""Public JSON API for browsing events, registering and uploading documents."""
from flask import Blueprint, Response, current_app, jsonify, request

from extensions import limiter
from services import pdf_service, registration_service as rs, storage_service
from supabase_client import get_client

registration_bp = Blueprint("registration_api", __name__, url_prefix="/api/astra")


def _auth(registration_id):
    return rs.authorize(str(registration_id), request.headers.get("X-Registration-Token", ""))


@registration_bp.get("/config")
def config():
    s = rs.get_settings()
    return jsonify(documents_required=bool(s["require_documents"]), manual_payment_allowed=bool(s["allow_manual_payment"]),
                   max_upload_mb=current_app.config["MAX_UPLOAD_MB"], aadhaar_allowed=current_app.config["ALLOW_AADHAAR_MASKED"])


@registration_bp.get("/categories")
def categories():
    out = {}
    for e in rs.list_events():
        c = out.setdefault(e["category"], {"name": e["category"], "events": 0, "open": 0, "sort": e["cat_sort"]})
        c["events"] += 1
        c["open"] += 1 if e["registration_open"] else 0
    return jsonify(categories=sorted(out.values(), key=lambda c: c["sort"]))


@registration_bp.get("/events")
def events():
    return jsonify(events=rs.list_events())


@registration_bp.get("/events/<int:event_id>")
def event_detail(event_id):
    item = next((e for e in rs.list_events() if e["id"] == event_id), None)
    if not item:
        raise rs.ServiceError("Event not found.", 404)
    return jsonify(item)


@registration_bp.post("/register")
@limiter.limit("10 per minute")
def register():
    result = rs.create_registration(request.get_json(silent=True))
    return jsonify(result), (200 if result.pop("replayed") else 201)


@registration_bp.post("/registrations/<uuid:registration_id>/documents")
@limiter.limit("30 per minute")
def upload(registration_id):
    reg = _auth(registration_id)
    file = request.files.get("file")
    if not file:
        raise rs.ServiceError("Choose a file to upload.", 400)
    doc = storage_service.upload_document(reg, request.form.get("participant_id") or None,
                                          request.form.get("doc_type", "college_id"), file, current_app.config)
    return jsonify(doc), 201


@registration_bp.get("/registrations/<uuid:registration_id>/documents")
def my_documents(registration_id):
    reg = _auth(registration_id)
    rows = get_client().table("v_documents_admin").select("id,participant_id,participant_name,doc_type,status,review_notes") \
        .eq("registration_id", reg["id"]).execute().data
    return jsonify(documents=rows)


@registration_bp.get("/registrations/<uuid:registration_id>/confirmation")
def confirmation(registration_id):
    return jsonify(rs.confirmation_data(_auth(registration_id)))


@registration_bp.get("/registrations/<uuid:registration_id>/confirmation.pdf")
def confirmation_pdf(registration_id):
    data = rs.confirmation_data(_auth(registration_id))
    url = f"{current_app.config['APP_BASE_URL']}/verify/{data['verification_token']}"
    return Response(pdf_service.build_pdf(data, url), mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={data['reg_code']}.pdf"})


@registration_bp.get("/registrations/verify/<token>")
@limiter.limit("30 per minute")
def verify(token):
    result = rs.verify_by_token(token)
    return jsonify(result), (200 if result["valid"] else 404)
