import io
from datetime import datetime, timezone

import pytest
from PIL import Image

from services import registration_service as rs, storage_service as ss
from tests.conftest import make_event, person, team_payload


def test_valid_team_passes():
    clean = rs.validate_registration(team_payload(), make_event())
    assert len(clean["participants"]) == 4 and clean["participants"][0]["role"] == "captain"
    assert clean["contact_email"] == "cap@college.edu"


def test_valid_individual_passes():
    ev = make_event(allow_individual=True, allow_team=False, min_team_size=1, max_team_size=1)
    data = {"format": "individual", "common": {"college": "Sathaye"}, "members": [person(0)], "subs": [], "extra": {"ingame_username": "x"},
            "consent": True, "key": "k" * 16, "secret": "b" * 64}
    assert rs.validate_registration(data, ev)["participants"][0]["role"] == "individual"


@pytest.mark.parametrize("n", [1, 3, 5])
def test_wrong_team_size_rejected(n):
    with pytest.raises(rs.ValidationError):
        rs.validate_registration(team_payload(n=n), make_event())


def test_too_many_substitutes_rejected():
    with pytest.raises(rs.ValidationError):
        rs.validate_registration(team_payload(subs=[person(5), person(6)]), make_event())


def test_bad_phone_and_missing_consent_rejected():
    bad = team_payload(consent=False)
    bad["members"][0]["phone"] = "12345"
    with pytest.raises(rs.ValidationError) as e:
        rs.validate_registration(bad, make_event())
    assert any("mobile" in d for d in e.value.details) and any("Consent" in d for d in e.value.details)


def test_required_event_field_enforced():
    with pytest.raises(rs.ValidationError):
        rs.validate_registration(team_payload(extra={}), make_event())


def test_format_not_allowed_rejected():
    with pytest.raises(rs.ValidationError):
        rs.validate_registration(team_payload(), make_event(allow_team=False, allow_individual=True))


def test_closed_and_full_events_not_open():
    now = datetime.now(timezone.utc)
    assert rs.public_event(make_event(registration_status="draft"), None, now)["registration_open"] is False
    assert rs.public_event(make_event(), 0, now)["closed_reason"] == "event full"
    assert rs.public_event(make_event(), 3, now)["registration_open"] is True


def test_unconfirmed_team_size_is_closed():
    e = rs.public_event(make_event(min_team_size=None, max_team_size=None), None, datetime.now(timezone.utc))
    assert e["registration_open"] is False


def test_fee_is_formatted_in_rupees():
    assert rs.public_event(make_event(fee_paise=25000), None, datetime.now(timezone.utc))["fee"] == 250


def test_api_returns_422_for_invalid_payload(client, monkeypatch):
    monkeypatch.setattr(rs, "get_event_row", lambda _id: make_event())
    r = client.post("/api/astra/register", json={**team_payload(n=2), "event_id": 1})
    assert r.status_code == 422 and r.get_json()["details"]


# ---------- file validation ----------
def png_bytes():
    buf = io.BytesIO(); Image.new("RGB", (10, 10), "red").save(buf, "PNG"); return buf.getvalue()


def test_real_png_accepted():
    assert ss.validate_file("id.PNG", "image/png", png_bytes(), 5_000_000) == ("png", "image/png")


def test_real_pdf_accepted():
    assert ss.validate_file("id.pdf", "application/pdf", b"%PDF-1.4\n%%EOF", 5_000_000)[0] == "pdf"


def test_script_renamed_to_png_rejected():
    with pytest.raises(rs.ServiceError):
        ss.validate_file("evil.png", "image/png", b"<script>alert(1)</script>", 5_000_000)


def test_extension_mismatch_rejected():
    with pytest.raises(rs.ServiceError):
        ss.validate_file("id.jpg", "image/jpeg", png_bytes(), 5_000_000)


def test_pdf_with_javascript_rejected():
    with pytest.raises(rs.ServiceError):
        ss.validate_file("a.pdf", "application/pdf", b"%PDF-1.4 /JavaScript (app.alert(1))", 5_000_000)


def test_oversize_and_bad_extension_rejected():
    with pytest.raises(rs.ServiceError) as e:
        ss.validate_file("a.png", "image/png", png_bytes(), 10)
    assert e.value.status == 413
    with pytest.raises(rs.ServiceError):
        ss.validate_file("a.exe", "application/octet-stream", b"MZ", 5_000_000)


def test_confirmation_requires_matching_secret(client, db):
    db.tables["registrations"] = [{"id": "11111111-1111-1111-1111-111111111111", "access_token_hash": rs.hash_secret("s" * 40)}]
    url = "/api/astra/registrations/11111111-1111-1111-1111-111111111111/confirmation"
    assert client.get(url).status_code == 404
    assert client.get(url, headers={"X-Registration-Token": "wrong" * 8}).status_code == 404


def test_masking_and_verify_rejects_garbage(client):
    assert rs.mask_name("Rajesh Sharma") == "Rajesh S****"
    assert client.get("/api/astra/registrations/verify/short").status_code == 404
