"""All configuration comes from environment variables (see .env.example)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "")
    IS_PROD = os.getenv("FLASK_ENV", "development") == "production"

    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    DOCUMENT_BUCKET = os.getenv("DOCUMENT_BUCKET", "astra-documents")
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "5"))
    ALLOW_AADHAAR_MASKED = _bool("ALLOW_AADHAAR_MASKED", False)
    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

    # Flask
    MAX_CONTENT_LENGTH = (MAX_UPLOAD_MB + 1) * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PROD          # cookies only over HTTPS in production
    PERMANENT_SESSION_LIFETIME = 2 * 60 * 60  # admin sessions last 2 hours
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
