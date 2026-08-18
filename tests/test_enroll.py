"""Enrolment policy: face-compulsory, first-enrol binds device, re-enrol/new
device needs an admin one-time grant. Biometric service is mocked."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.biometric import EnrollResult
from app.db import engine
from app.main import app
from app.models import EnrollGrant, Student
from app.security import hash_password

SID = "20599999"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Test", password_hash=hash_password("pw")))
        db.commit()
    yield


@pytest.fixture(autouse=True)
def mock_enroll(monkeypatch):
    monkeypatch.setattr(
        biometric, "enroll_user",
        lambda uid, images, source="auto": EnrollResult(enrolled=len(images), of=len(images), samples=2, raw={}),
    )


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, device):
    r = client.post("/api/auth/login", json={
        "student_id": SID, "password": "pw", "device_uid": device, "platform": "t", "device_name": "t"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _enroll(client, H, modality, grant=""):
    return client.post("/api/enroll", headers=H,
                       json={"modality": modality, "images": ["a", "b", "c"], "grant_token": grant}).json()


def test_face_first_then_optional_palm_free(client):
    A = _login(client, "devA")
    assert _enroll(client, A, "face")["ok"] is True
    assert client.get("/api/enroll/status", headers=A).json()["can_mark"] is True
    # adding a not-yet-held modality from the same device is free
    r = _enroll(client, A, "palm")
    assert r["ok"] is True
    st = client.get("/api/enroll/status", headers=A).json()
    assert st["face_enrolled"] and st["palm_enrolled"]


def test_reenroll_existing_modality_needs_grant(client):
    A = _login(client, "devA")
    _enroll(client, A, "face")
    r = _enroll(client, A, "face")  # redo face
    assert r["ok"] is False and r["code"] == "grant_required"


def test_new_device_needs_grant(client):
    _enroll(client, _login(client, "devA"), "face")
    r = _enroll(client, _login(client, "devB"), "face")
    assert r["ok"] is False and r["code"] == "grant_required"


def test_grant_unlocks_and_is_single_use(client):
    _enroll(client, _login(client, "devA"), "face")
    with Session(engine) as db:
        db.add(EnrollGrant(token="CODE1234", student_id=SID,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()
    B = _login(client, "devB")
    assert _enroll(client, B, "face", grant="CODE1234")["ok"] is True   # consumed, re-binds devB
    assert _enroll(client, B, "face", grant="CODE1234")["code"] == "grant_required"  # single-use


def test_expired_grant_rejected(client):
    _enroll(client, _login(client, "devA"), "face")
    with Session(engine) as db:
        db.add(EnrollGrant(token="OLD12345", student_id=SID,
                           expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
    r = _enroll(client, _login(client, "devB"), "face", grant="OLD12345")
    assert r["code"] == "grant_required"
