"""The first enrolment is the step that ties a face to a student ID.

Sign-in passwords are shared across a programme, so a classmate knows both
halves of the credential. If the first enrolment were self-served, they could
bind their own face to an unenrolled classmate's ID and mark for them all
semester. It needs an admin one-time code instead, issued in person.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric, enrolment
from app.biometric import EnrollResult
from app.config import settings
from app.db import engine
from app.main import app
from app.models import EnrollGrant, Student
from app.security import hash_password

SID = "20577777"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="New Student", password_hash=hash_password("pw"),
                       programme="Computer Science"))
        db.commit()
    yield


@pytest.fixture(autouse=True)
def mocked_service(monkeypatch):
    monkeypatch.setattr(biometric, "enroll_user",
                        lambda uid, images, source="auto": EnrollResult(enrolled=len(images), of=len(images), samples=2, raw={}))
    monkeypatch.setattr(biometric, "list_enrolled_user_ids", lambda page=500: set())
    enrolment.reset_cache()
    yield
    enrolment.reset_cache()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/login", json={
        "student_id": SID, "password": "pw", "device_uid": "devA", "platform": "t", "device_name": "t"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _enroll(client, auth, grant=""):
    return client.post("/api/enroll", headers=auth,
                       json={"modality": "face", "images": ["a", "b", "c"], "grant_token": grant}).json()


def test_first_enrolment_is_refused_without_an_admin_code(client, auth):
    r = _enroll(client, auth)
    assert r["ok"] is False and r["code"] == "grant_required"
    assert "first enrolment" in r["message"].lower()

    with Session(engine) as db:  # nothing was bound
        assert db.exec(select(Student).where(Student.student_id == SID)).one().enrolled_modality == ""


def test_an_admin_code_lets_the_first_enrolment_through_once(client, auth):
    with Session(engine) as db:
        db.add(EnrollGrant(token="FIRST123", student_id=SID,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()

    assert _enroll(client, auth, grant="FIRST123")["ok"] is True
    assert client.get("/api/enroll/status", headers=auth).json()["can_mark"] is True
    # the code is spent, so it cannot re-bind a second face
    assert _enroll(client, auth, grant="FIRST123")["code"] == "grant_required"


def test_the_gate_can_be_lifted_where_passwords_are_private(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "enroll_requires_grant", False)
    assert _enroll(client, auth)["ok"] is True
