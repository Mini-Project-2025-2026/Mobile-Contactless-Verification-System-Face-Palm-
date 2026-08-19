"""One shared device at the door, and nobody types anything.

This replaces the workflow `test_shared_device.py` documents: sign out, sign in
as the next student using the programme password everybody in the hall knows,
mark, hand the phone on. The verification service answers 1:N — hand it a
capture, it says who — so the device can simply recognise whoever walks up.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.biometric import Challenge, VerifyResult
from app.config import settings
from app.db import engine
from app.main import app
from app.models import Attendance, AttendanceStatus, Course, Enrollment, Session as ClassSession, Student
from app.security import hash_password
from app.timeutil import now

LAT, LNG = 6.6745, -1.5716
AMA, KOFI, STRANGER = "20512345", "20599999", "20500001"


@pytest.fixture(autouse=True)
def campus():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for sid, name in ((AMA, "Ama Mensah"), (KOFI, "Kofi Owusu"), (STRANGER, "Yaa Asare")):
            db.add(Student(student_id=sid, name=name, password_hash=hash_password("pw"),
                           enrolled_modality="face"))
        course = Course(code="CS101", title="Intro", semester="2025/2026-1")
        db.add(course)
        db.commit()
        db.refresh(course)
        db.add(Enrollment(student_id=AMA, course_id=course.id))
        db.add(Enrollment(student_id=KOFI, course_id=course.id))
        # STRANGER is a student here, but not on this course.
        moment = now()
        db.add(ClassSession(course_id=course.id, title="L1", lat=LAT, lng=LNG, radius_m=70.0,
                            starts_at=moment - timedelta(minutes=5),
                            ends_at=moment + timedelta(hours=1), marks_required=2))
        db.commit()
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(biometric, "get_challenge",
                        lambda: Challenge(active=True, token="tok", instruction="turn"))
    return TestClient(app)


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "pw")
    token = client.post("/api/admin/login",
                        json={"username": "admin", "password": "pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _session_id() -> int:
    """The CS101 class — the first one opened, and the one the kiosk is for."""
    with Session(engine) as db:
        return db.exec(select(ClassSession).order_by(ClassSession.id)).first().id


@pytest.fixture
def kiosk(client, admin):
    body = client.post(f"/api/admin/sessions/{_session_id()}/kiosk", headers=admin,
                       json={"grace_minutes": 15})
    assert body.status_code == 201, body.text
    return {"Authorization": f"Bearer {body.json()['access_token']}"}


def _identifies(user_id: str, score: float = 0.91, nonce: str = "n1"):
    return lambda **kw: VerifyResult(success=True, user_id=user_id, score=score,
                                     signature_valid=True, nonce=nonce, raw={})


def _tap(client, kiosk, lat=LAT, lng=LNG):
    return client.post("/api/kiosk/verify", headers=kiosk, json={
        "token": "tok", "frames": ["a", "b"], "gps": {"lat": lat, "lng": lng}})


def test_a_student_is_marked_without_typing_anything(client, kiosk, monkeypatch):
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA))

    body = _tap(client, kiosk).json()
    assert body["ok"] is True
    assert body["student_id"] == AMA
    assert body["name"] == "Ama Mensah"
    assert body["status"] == "partial"      # start marked, end still to come
    assert "Ama Mensah" in body["message"]


def test_the_next_student_just_walks_up(client, kiosk, monkeypatch):
    """No sign-out, no password read aloud, no session to forget to close."""
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA, nonce="n-ama"))
    _tap(client, kiosk)
    monkeypatch.setattr(biometric, "identify_person", _identifies(KOFI, nonce="n-kofi"))
    second = _tap(client, kiosk).json()

    assert second["student_id"] == KOFI
    with Session(engine) as db:
        marked = {a.student_id for a in db.exec(select(Attendance)).all()}
    assert marked == {AMA, KOFI}


def test_an_unrecognised_face_is_told_so_plainly(client, kiosk, monkeypatch):
    monkeypatch.setattr(biometric, "identify_person",
                        lambda **kw: VerifyResult(success=False, user_id="", score=0.2,
                                                  signature_valid=True, nonce="", raw={}))
    body = _tap(client, kiosk).json()
    assert body["ok"] is False and body["code"] == "not_recognised"


def test_someone_the_service_knows_but_this_campus_does_not(client, kiosk, monkeypatch):
    monkeypatch.setattr(biometric, "identify_person", _identifies("99999999"))
    body = _tap(client, kiosk).json()
    assert body["code"] == "unknown_student"
    assert "not registered here" in body["message"]


def test_a_student_not_on_this_course_is_named_and_refused(client, kiosk, monkeypatch):
    monkeypatch.setattr(biometric, "identify_person", _identifies(STRANGER))
    body = _tap(client, kiosk).json()
    assert body["code"] == "not_enrolled"
    assert body["name"] == "Yaa Asare"      # they are told why, by name


def test_an_unsigned_verdict_is_refused(client, kiosk, monkeypatch):
    """1:N is still only trustworthy because the verdict is signed by the service."""
    monkeypatch.setattr(biometric, "identify_person",
                        lambda **kw: VerifyResult(success=True, user_id=AMA, score=0.99,
                                                  signature_valid=False, nonce="n", raw={}))
    assert _tap(client, kiosk).status_code == 502


def test_a_weak_match_does_not_mark_anyone(client, kiosk, monkeypatch):
    monkeypatch.setattr(settings, "min_verify_score", 0.80)
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA, score=0.55))
    body = _tap(client, kiosk).json()
    assert body["code"] == "low_confidence"
    with Session(engine) as db:
        assert db.exec(select(Attendance)).all() == []


def test_a_device_carried_out_of_the_room_stops_working(client, kiosk, monkeypatch):
    """The kiosk's own position is the geofence claim, so moving it breaks it."""
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA))
    body = _tap(client, kiosk, lat=6.70, lng=-1.60).json()
    assert body["code"] == "kiosk_out_of_place"
    assert body["distance_m"] > 70


def test_the_same_capture_cannot_be_replayed(client, kiosk, monkeypatch):
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA, nonce="same"))
    assert _tap(client, kiosk).json()["code"] == "ok"
    assert _tap(client, kiosk).json()["code"] == "duplicate"


def test_both_windows_complete_the_attendance(client, kiosk, admin, monkeypatch):
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA, nonce="n-start"))
    _tap(client, kiosk)
    client.post(f"/api/admin/sessions/{_session_id()}/phase", headers=admin,
                json={"phase": "end"})
    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA, nonce="n-end"))
    body = _tap(client, kiosk).json()

    assert body["status"] == AttendanceStatus.present.value
    assert "Attendance complete" in body["message"]


def test_a_kiosk_token_cannot_do_anything_else(client, kiosk):
    """It marks attendance for one class. It is not a login."""
    assert client.get("/api/admin/students", headers=kiosk).status_code == 403
    assert client.get("/api/profile", headers=kiosk).status_code == 401
    assert client.post("/api/enroll", headers=kiosk,
                       json={"modality": "face", "images": ["i"]}).status_code == 401


def test_a_student_token_cannot_act_as_a_kiosk(client):
    token = client.post("/api/auth/login", json={
        "student_id": AMA, "password": "pw", "device_uid": "d"}).json()["access_token"]
    response = client.post("/api/kiosk/verify",
                           headers={"Authorization": f"Bearer {token}"},
                           json={"frames": ["a"], "gps": {"lat": LAT, "lng": LNG}})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "kiosk_token_required"


def test_a_kiosk_token_is_bound_to_its_own_class(client, admin, kiosk, monkeypatch):
    """A device issued for one lecture cannot mark for the one next door."""
    with Session(engine) as db:
        other = Course(code="MATH151", title="Algebra", semester="2025/2026-1")
        db.add(other)
        db.commit()
        db.refresh(other)
        db.add(Enrollment(student_id=AMA, course_id=other.id))
        moment = now()
        db.add(ClassSession(course_id=other.id, title="M1", lat=LAT, lng=LNG, radius_m=70.0,
                            starts_at=moment - timedelta(minutes=5),
                            ends_at=moment + timedelta(hours=1)))
        db.commit()

    monkeypatch.setattr(biometric, "identify_person", _identifies(AMA))
    _tap(client, kiosk)

    with Session(engine) as db:
        rows = db.exec(select(Attendance)).all()
    # exactly one mark, against the class the token names
    assert len(rows) == 1
    assert rows[0].session_id == _session_id()
