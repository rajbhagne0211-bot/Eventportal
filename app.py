"""ASTRA FEST 2026 registration backend. Run locally: flask --app app run --debug"""
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import limiter
from services.registration_service import ServiceError, ValidationError


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("FLASK_SECRET_KEY is not set. Copy .env.example to .env and fill it in.")
    if app.config["IS_PROD"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # correct client IPs behind Render/Railway
    limiter.init_app(app)
    logging.basicConfig(level=logging.INFO)

    from routes.admin import admin_bp
    from routes.payments import payments_bp
    from routes.public import public_bp
    from routes.registration import registration_bp
    for bp in (public_bp, registration_bp, payments_bp, admin_bp):
        app.register_blueprint(bp)

    if app.config["CORS_ORIGINS"]:   # only needed when the frontend lives on another domain
        @app.after_request
        def cors(resp):
            origin = request.headers.get("Origin")
            if origin in app.config["CORS_ORIGINS"] and request.path.startswith("/api/"):
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Registration-Token"
                resp.headers["Vary"] = "Origin"
            return resp

    # ---- CSRF for the admin area (cookie-session based) ----
    @app.before_request
    def csrf_protect():
        if request.path.startswith("/admin") and request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not session.get("csrf") or not hmac.compare_digest(sent, session["csrf"]):
                return jsonify(error="Invalid or missing CSRF token."), 400

    @app.context_processor
    def inject_csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf"]}

    # ---- secure headers ----
    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com; "
            "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com; "
            "frame-src https://api.razorpay.com https://checkout.razorpay.com; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'self'")
        if app.config["IS_PROD"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith(("/api/", "/admin", "/confirmation", "/verify")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- JSON errors ----
    @app.errorhandler(ServiceError)
    def service_error(e):
        return jsonify(error=e.message, code=e.code), e.status

    @app.errorhandler(ValidationError)
    def validation_error(e):
        return jsonify(error="Please fix the highlighted problems.", details=e.details), 422

    @app.errorhandler(HTTPException)
    def http_error(e):
        if request.path.startswith(("/api/", "/admin/api/")):
            msg = {413: "File too large.", 429: "Too many requests. Please slow down."}.get(e.code, e.description)
            return jsonify(error=msg), e.code
        return e

    @app.errorhandler(Exception)
    def unexpected(e):
        app.logger.exception("Unhandled error")        # details go to the log, never to the user
        return jsonify(error="Something went wrong. Please try again."), 500

    # ---- command-line helpers ----
    @app.cli.command("create-admin")
    def create_admin():
        """flask --app app create-admin   (asks for e-mail, password, role)"""
        import click
        from werkzeug.security import generate_password_hash
        from supabase_client import get_client
        email = click.prompt("Admin e-mail").strip().lower()
        password = click.prompt("Password (min 12 chars)", hide_input=True, confirmation_prompt=True)
        role = click.prompt("Role", type=click.Choice(["superadmin", "manager", "verifier"]), default="superadmin")
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        get_client().table("admin_users").insert({"email": email, "password_hash": generate_password_hash(password), "role": role}).execute()
        click.echo(f"Created {role} {email}")

    @app.cli.command("purge-documents")
    def purge_documents():
        """Delete ID documents older than the retention period (run daily from a scheduler)."""
        import click
        from services import storage_service
        from services.registration_service import get_settings
        from supabase_client import get_client
        days = int(get_settings()["document_retention_days"])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sb = get_client()
        old = sb.table("registration_documents").select("id,storage_path").lt("uploaded_at", cutoff).is_("deleted_at", "null").execute().data or []
        storage_service.delete_objects([d["storage_path"] for d in old], app.config)
        for d in old:
            sb.table("registration_documents").update({"deleted_at": datetime.now(timezone.utc).isoformat()}).eq("id", d["id"]).execute()
        click.echo(f"Purged {len(old)} document(s) older than {days} days")

    return app


app = create_app() if __name__ != "__main__" and Config.SECRET_KEY else None

if __name__ == "__main__":
    create_app().run(debug=True)
