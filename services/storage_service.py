"""Private document uploads (Supabase Storage). Files are validated by content, never by filename."""
import io
import uuid

from PIL import Image

from services.registration_service import ServiceError
from supabase_client import get_client

ALLOWED = {  # extension -> (MIME type, magic-bytes check)
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf",
}
DOC_TYPES = {"college_id", "identity_proof", "payment_screenshot", "aadhaar_masked"}
BAD_PDF_MARKERS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/AA")


def sniff(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def validate_file(filename, claimed_mime, data, max_bytes):
    """Return (extension, mime) or raise ServiceError."""
    if not data:
        raise ServiceError("The file is empty.", 400)
    if len(data) > max_bytes:
        raise ServiceError(f"File is too large (max {max_bytes // (1024 * 1024)} MB).", 413)
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in ALLOWED:
        raise ServiceError("Only JPG, PNG or PDF files are accepted.", 400)
    real = sniff(data)
    if real is None or real != ALLOWED[ext] or (claimed_mime and claimed_mime != real):
        raise ServiceError("The file content does not match its type.", 400)
    if real == "application/pdf":
        if any(m in data for m in BAD_PDF_MARKERS):
            raise ServiceError("This PDF contains active content and was rejected.", 400)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
        except Exception:
            raise ServiceError("The image is corrupted or not a real image.", 400)
    return ("jpg" if ext == "jpeg" else ext), real


def upload_document(reg, participant_id, doc_type, file, cfg):
    if doc_type not in DOC_TYPES or (doc_type == "aadhaar_masked" and not cfg["ALLOW_AADHAAR_MASKED"]):
        raise ServiceError("That document type is not accepted.", 400)
    sb = get_client()
    if participant_id:
        owner = sb.table("participants").select("id").eq("id", participant_id).eq("registration_id", reg["id"]).execute().data
        if not owner:
            raise ServiceError("Participant does not belong to this registration.", 400)
    q = sb.table("registration_documents").select("id,status").eq("registration_id", reg["id"]).eq("doc_type", doc_type).is_("deleted_at", "null")
    q = q.eq("participant_id", participant_id) if participant_id else q.is_("participant_id", "null")
    if any(d["status"] != "rejected" for d in (q.execute().data or [])):
        raise ServiceError("A document of this type is already uploaded (re-upload is allowed only after rejection).", 409)

    data = file.read(cfg["MAX_UPLOAD_MB"] * 1024 * 1024 + 1)
    ext, mime = validate_file(file.filename, file.mimetype, data, cfg["MAX_UPLOAD_MB"] * 1024 * 1024)
    doc_id = str(uuid.uuid4())
    path = f"{reg['id']}/{doc_id}.{ext}"          # our own safe name; never the uploaded filename
    sb.storage.from_(cfg["DOCUMENT_BUCKET"]).upload(path, data, {"content-type": mime, "upsert": "false"})
    row = {"id": doc_id, "registration_id": reg["id"], "participant_id": participant_id or None, "doc_type": doc_type,
           "storage_path": path, "content_type": mime, "size_bytes": len(data)}
    sb.table("registration_documents").insert(row).execute()
    return {"id": doc_id, "doc_type": doc_type, "status": "pending"}


def signed_url(path, cfg, seconds=60):
    res = get_client().storage.from_(cfg["DOCUMENT_BUCKET"]).create_signed_url(path, seconds)
    return res.get("signedURL") or res.get("signedUrl")


def delete_objects(paths, cfg):
    if paths:
        get_client().storage.from_(cfg["DOCUMENT_BUCKET"]).remove(paths)
