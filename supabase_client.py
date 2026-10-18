"""One server-side Supabase client using the service-role key.

The service-role key bypasses Row Level Security, so it must only ever live on the
server. Every route that uses it must do its own validation / authorization.
"""
from functools import lru_cache
from supabase import create_client
from flask import current_app


@lru_cache(maxsize=1)
def _client(url, key):
    return create_client(url, key)


def get_client():
    cfg = current_app.config
    if not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_SERVICE_ROLE_KEY"):
        raise RuntimeError("Supabase is not configured. Fill SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
    return _client(cfg["SUPABASE_URL"], cfg["SUPABASE_SERVICE_ROLE_KEY"])
