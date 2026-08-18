"""End-to-end check-in flow with the biometric service mocked.

Asserts the full substitution works: geofence gate → (mocked) verify → signature
trust → double-mark → present. Also covers the geofence-reject and
identity-mismatch branches.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.biometric import VerifyResult, Challenge
from app.config import settings
from app.db import engine
from app.main import app
from app.models import Course, Enrollment, Session as ClassSession, Student
from app.security import hash_password

LAT, LNG = 6.6745, -1.5716
SID = "20512345"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Ama", password_hash=hash_password("pw"),
                       semester="2025/2026-1", enrolled_modality="face"))
        course = Course(code="CS101", title="Intro", semester="2025/2026-1", lecturer_name="Dr. Osei")
        db.add(course)
        db.commit()
        db.refresh(course)
        db.add(Enrollment(student_id=SID, course_id=course.id))
        now = datetime.now(timezone.utc)
        db.add(ClassSession(
            course_id=course.id, title="L1", lat=LAT, lng=LNG, radius_m=70.0,
            starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=1), marks_required=2,
        ))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def token(client):
    r = client.post("/api/auth/login", json={"student_id": SID, "password": "pw", "device_uid": "dev-1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _mock_verify(monkeypatch, *, success=True, user_id=SID, score=0.72, nonce="n1", sig_valid=True):
    monkeypatch.setattr(biometric, "get_challenge", lambda: Challenge(active=True, token="t", instruction="turn"))
    monkeypatch.setattr(
        biometric, "verify_student",
        lambda student_id, **kw: VerifyResult(
            success=success, user_id=user_id, score=score,
            signature_valid=sig_valid, nonce=nonce, raw={},
        ),
    )


def test_double_mark_reaches_present(client, token, monkeypatch):
    _mock_verify(monkeypatch, nonce="a")
    gps = {"lat": LAT, "lng": LNG}

    r1 = client.post("/api/checkin/verify", headers=_auth(token),
                     json={"session_id": 1, "token": "t", "frames": ["x"], "gps": gps})
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "partial"
    assert r1.json()["marks_count"] == 1

    _mock_verify(monkeypatch, nonce="b")  # a fresh (different) signed capture
    r2 = client.post("/api/checkin/verify", headers=_auth(token),
                     json={"session_id": 1, "token": "t", "frames": ["x"], "gps": gps})
    assert r2.json()["status"] == "present"
    assert r2.json()["marks_count"] == 2


def test_replayed_signature_not_double_counted(client, token, monkeypatch):
    _mock_verify(monkeypatch, nonce="same")
    gps = {"lat": LAT, "lng": LNG}
    client.post("/api/checkin/verify", headers=_auth(token),
                json={"session_id": 1, "frames": ["x"], "gps": gps})
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "frames": ["x"], "gps": gps})
    assert r.json()["code"] == "duplicate"
    assert r.json()["marks_count"] == 1


def test_outside_geofence_rejected(client, token, monkeypatch):
    _mock_verify(monkeypatch)
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "frames": ["x"], "gps": {"lat": 6.70, "lng": -1.57}})
    assert r.json()["ok"] is False
    assert r.json()["code"] == "not_in_geofence"


def test_identity_mismatch_rejected(client, token, monkeypatch):
    _mock_verify(monkeypatch, user_id="99999999")
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "frames": ["x"], "gps": {"lat": LAT, "lng": LNG}})
    assert r.json()["code"] == "biometric_mismatch"


def test_bad_signature_is_gateway_error(client, token, monkeypatch):
    _mock_verify(monkeypatch, sig_valid=False)
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "frames": ["x"], "gps": {"lat": LAT, "lng": LNG}})
    assert r.status_code == 502


def test_face_required_when_no_face(client, token, monkeypatch):
    with Session(engine) as db:
        s = db.exec(select(Student).where(Student.student_id == SID)).first()
        s.enrolled_modality = "palm"  # palm only, no face
        db.add(s)
        db.commit()
    _mock_verify(monkeypatch)
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "modality": "palm", "image": "x", "gps": {"lat": LAT, "lng": LNG}})
    assert r.json()["code"] == "face_required"
    assert r.json()["ok"] is False


def test_low_gps_accuracy_rejected(client, token, monkeypatch):
    monkeypatch.setattr(settings, "max_gps_accuracy_m", 50.0)
    _mock_verify(monkeypatch)
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "frames": ["x"], "gps": {"lat": LAT, "lng": LNG, "accuracy_m": 200}})
    assert r.json()["code"] == "low_gps_accuracy"


def test_good_gps_accuracy_allowed(client, token, monkeypatch):
    monkeypatch.setattr(settings, "max_gps_accuracy_m", 50.0)
    _mock_verify(monkeypatch, nonce="acc-ok")
    r = client.post("/api/checkin/verify", headers=_auth(token),
                    json={"session_id": 1, "frames": ["x"], "gps": {"lat": LAT, "lng": LNG, "accuracy_m": 12}})
    assert r.json()["ok"] is True
