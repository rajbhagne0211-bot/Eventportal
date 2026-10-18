"""Server-rendered pages (the data comes from the JSON API)."""
import io
import re

import segno
from flask import Blueprint, Response, current_app, render_template

public_bp = Blueprint("public", __name__)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@public_bp.get("/")
def home():
    return render_template("home.html")


@public_bp.get("/register")
def register_page():
    return render_template("register.html")


@public_bp.get("/confirmation/<uuid:registration_id>")
def confirmation_page(registration_id):
    # The access secret lives in the URL #fragment, which browsers never send to the server.
    return render_template("confirmation.html", registration_id=str(registration_id))


@public_bp.get("/verify/<token>")
def verify_page(token):
    return render_template("verify.html", token=token if TOKEN_RE.match(token) else "")


@public_bp.get("/qr/<token>.svg")
def qr(token):
    if not TOKEN_RE.match(token):
        return Response(status=404)
    url = f"{current_app.config['APP_BASE_URL']}/verify/{token}"   # the QR holds only a verification URL
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#2a2328")
    return Response(buf.getvalue(), mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@public_bp.get("/healthz")
def health():
    return {"ok": True}
