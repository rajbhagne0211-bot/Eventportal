import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

from app import create_app  # noqa: E402


class FakeQuery:
    def __init__(self, db, name):
        self.db, self.name, self.op, self.filters, self.payload, self.one = db, name, "select", [], None, False

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, vals): self.op, self.payload = "update", vals; return self
    def delete(self): self.op = "delete"; return self
    def upsert(self, row): self.op, self.payload = "insert", row; return self
    def eq(self, k, v): self.filters.append((k, "eq", v)); return self
    def neq(self, k, v): self.filters.append((k, "neq", v)); return self
    def is_(self, k, v): self.filters.append((k, "null", None)); return self
    def lt(self, *a): return self
    def or_(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def single(self): self.one = True; return self

    def _match(self, row):
        for k, op, v in self.filters:
            if op == "eq" and row.get(k) != v: return False
            if op == "neq" and row.get(k) == v: return False
            if op == "null" and row.get(k) is not None: return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        class R: pass
        r = R()
        if self.op == "insert":
            uniq = self.db.unique.get(self.name)
            if uniq and any(x.get(uniq) == self.payload.get(uniq) for x in rows):
                raise Exception("duplicate key value violates unique constraint")
            row = dict(self.payload)
            if self.name == "payments":
                row.setdefault("status", "created")      # mirrors the column default in schema.sql
            rows.append(row); r.data = [dict(row)]
        elif self.op == "update":
            r.data = []
            for x in rows:
                if self._match(x): x.update(self.payload); r.data.append(x)
        elif self.op == "delete":
            self.db.tables[self.name] = [x for x in rows if not self._match(x)]; r.data = []
        else:
            r.data = [x for x in rows if self._match(x)]
            if self.one: r.data = r.data[0] if r.data else None
        return r


class FakeDB:
    def __init__(self):
        self.tables, self.unique, self.rpc_calls = {}, {"webhook_events": "event_id"}, []

    def table(self, name): return FakeQuery(self, name)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for mod in ("services.registration_service", "services.payment_service", "services.storage_service", "routes.admin", "routes.registration"):
        monkeypatch.setattr(f"{mod}.get_client", lambda: fake)
    return fake


@pytest.fixture
def app():
    return create_app({"TESTING": True, "SECRET_KEY": "test-secret", "RATELIMIT_ENABLED": False,
                       "RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "rzp_secret",
                       "RAZORPAY_WEBHOOK_SECRET": "whsec", "SUPABASE_URL": "http://x", "SUPABASE_SERVICE_ROLE_KEY": "k"})


@pytest.fixture
def client(app):
    return app.test_client()


def make_event(**kw):
    base = {"id": 1, "name": "Test Event", "category": "Gaming", "subcategory": "", "description": "", "allow_individual": False,
            "allow_team": True, "min_team_size": 4, "max_team_size": 4, "max_substitutes": 1, "fee_paise": 25000, "capacity": 10,
            "registration_status": "open", "registration_opens_at": None, "registration_closes_at": None, "is_active": True,
            "extra_fields": [{"key": "ingame_username", "label": "In-game username", "type": "text", "required": True}]}
    base.update(kw)
    return base


def person(i=0, **kw):
    p = {"name": f"Player {i}", "dept": "IT", "year": "TY", "roll": f"R{i}"}
    if i == 0:
        p.update({"email": "cap@college.edu", "phone": "9876543210"})
    p.update(kw)
    return p


def team_payload(n=4, **kw):
    d = {"format": "team", "common": {"team_name": "Local Legends", "college": "Sathaye College"},
         "members": [person(i) for i in range(n)], "subs": [], "extra": {"ingame_username": "legend"},
         "consent": True, "key": "k" * 16, "secret": "a" * 64}
    d.update(kw)
    return d
