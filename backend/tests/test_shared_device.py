"""One phone, many students: sign in, mark, sign out, next person.

Students without a phone still have to be able to mark, so a classroom handset
gets passed around. Nothing about attendance rests on which device is used: the
ID is claimed by the login, and the face is what proves it.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.biometric import Challenge, VerifyResult
from app.db import engine
from app.main import app
from app.models import (
    Attendance,
    Course,
    Device,
    Enrollment,
    ProgrammeCredential,
    Session as ClassSession,
    Student,
)
from app.security import hash_password

LAT, LNG = 6.6745, -1.5716
PHONE = "classroom-handset-1"
CS_PW = "knust-cs-2026!"
AMA, KOFI = "20512345", "20512399"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for sid, name in [(AMA, "Ama"), (KOFI, "Kofi")]:
            db.add(Student(student_id=sid, name=name, password_hash="",
                           programme="Computer Science", enrolled_modality="face"))
        db.add(ProgrammeCredential(programme="computer science", password_hash=hash_password(CS_PW)))
        course = Course(code="CS101", title="Intro", semester="2025/2026-1")
        db.add(course)
        db.commit()
        db.refresh(course)
        db.add(Enrollment(student_id=AMA, course_id=course.id))
        db.add(Enrollment(student_id=KOFI, course_id=course.id))
        now = datetime.now(timezone.utc)
        db.add(ClassSession(course_id=course.id, title="L1", lat=LAT, lng=LNG, radius_m=70.0,
                            starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=1),
                            marks_required=2))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _sign_in(client, sid):
    r = client.post("/api/auth/login", json={
        "student_id": sid, "password": CS_PW, "device_uid": PHONE,
        "platform": "ios-pwa", "device_name": "Class phone"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mark(client, auth, monkeypatch, *, face_of, nonce):
    """The service answers for whoever's face was presented, not who logged in."""
    monkeypatch.setattr(biometric, "get_challenge",
                        lambda: Challenge(active=True, token="t", instruction="turn"))
    monkeypatch.setattr(biometric, "verify_student",
                        lambda student_id, **kw: VerifyResult(
                            success=True, user_id=face_of, score=0.8,
                            signature_valid=True, nonce=nonce, raw={}))
    return client.post("/api/checkin/verify", headers=auth,
                       json={"session_id": 1, "frames": ["x"], "gps": {"lat": LAT, "lng": LNG}}).json()


def test_two_students_mark_from_the_same_phone(client, monkeypatch):
    ama = _sign_in(client, AMA)
    assert _mark(client, ama, monkeypatch, face_of=AMA, nonce="a")["ok"] is True

    # she signs out, he signs in on the very same handset
    kofi = _sign_in(client, KOFI)
    assert _mark(client, kofi, monkeypatch, face_of=KOFI, nonce="b")["ok"] is True

    with Session(engine) as db:
        marked = {a.student_id for a in db.exec(select(Attendance)).all()}
    assert marked == {AMA, KOFI}


def test_the_phone_belongs_to_whoever_signed_in_last(client):
    _sign_in(client, AMA)
    _sign_in(client, KOFI)
    with Session(engine) as db:
        (device,) = db.exec(select(Device).where(Device.device_uid == PHONE)).all()
    assert device.student_id == KOFI, "a shared handset must not stay named after the first user"


def test_signing_in_as_a_classmate_still_cannot_mark_for_them(client, monkeypatch):
    """The shared password gets you the screen. The face is what marks."""
    stolen = _sign_in(client, KOFI)          # anyone on the programme can reach this
    r = _mark(client, stolen, monkeypatch, face_of=AMA, nonce="c")   # but Ama's face shows up
    assert r["ok"] is False and r["code"] == "biometric_mismatch"

    with Session(engine) as db:
        assert db.exec(select(Attendance)).all() == []
