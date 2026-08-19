"""Two check-ins arriving at the same instant.

A hall of students taps "mark" the moment a lecturer opens a window, and one
student on a flaky connection taps twice. The database now refuses the second
attendance row and the second use of a signed verdict, so what these cover is
the other half of that: the request that loses the race must read the winner's
record, not turn a successful face verification into a 500.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.biometric import Challenge, VerifyResult
from app.db import engine
from app.main import app
from app.models import (
    Attendance,
    AttendanceMark,
    AttendanceStatus,
    Course,
    Enrollment,
    Student,
)
from app.models import (
    Session as ClassSession,
)
from app.routers import checkin
from app.security import hash_password
from app.timeutil import now

LAT, LNG = 6.6745, -1.5716
SID = "20512345"


@pytest.fixture(autouse=True)
def campus():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Ama", password_hash=hash_password("pw"),
                       enrolled_modality="face"))
        course = Course(code="CS101", title="Intro", semester="2025/2026-1")
        db.add(course)
        db.commit()
        db.refresh(course)
        db.add(Enrollment(student_id=SID, course_id=course.id))
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


def _headers(client):
    token = client.post("/api/auth/login", json={
        "student_id": SID, "password": "pw", "device_uid": "dev-1"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _session_id() -> int:
    with Session(engine) as db:
        return db.exec(select(ClassSession)).one().id


def _verdict(nonce: str):
    return lambda student_id, **kw: VerifyResult(
        success=True, user_id=student_id, score=0.93, signature_valid=True,
        nonce=nonce, raw={})


def _mark(client, headers, nonce="n1"):
    return client.post("/api/checkin/verify", headers=headers, json={
        "session_id": _session_id(), "token": "tok", "frames": ["a", "b"],
        "gps": {"lat": LAT, "lng": LNG}})


def test_the_same_signed_verdict_is_never_counted_twice(client, monkeypatch):
    monkeypatch.setattr(biometric, "verify_student", _verdict("same-nonce"))
    headers = _headers(client)

    first = _mark(client, headers).json()
    second = _mark(client, headers).json()

    assert first["code"] == "ok"
    assert second["code"] == "duplicate"
    with Session(engine) as db:
        assert len(db.exec(select(AttendanceMark)).all()) == 1


def test_losing_the_race_to_create_the_row_reads_the_winners(client, monkeypatch):
    """The constraint refuses the second insert; the loser must not 500."""
    monkeypatch.setattr(biometric, "verify_student", _verdict("n-race"))
    headers = _headers(client)
    session_id = _session_id()

    # Stand in for the request that got there first: the row appears between our
    # lookup finding nothing and our insert landing.
    original = checkin._find_attendance
    calls = []

    def racing_find(db, sid, student_id):
        calls.append(1)
        if len(calls) == 1:
            with Session(engine) as other:
                other.add(Attendance(session_id=sid, student_id=student_id,
                                     status=AttendanceStatus.absent))
                other.commit()
            return None          # we looked before they committed
        return original(db, sid, student_id)

    monkeypatch.setattr(checkin, "_find_attendance", racing_find)

    response = _mark(client, headers)
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "ok"

    with Session(engine) as db:
        rows = db.exec(select(Attendance).where(Attendance.session_id == session_id)).all()
        assert len(rows) == 1          # the constraint held
        assert rows[0].marks_count == 1  # and the mark landed on it


def test_a_verdict_that_slips_past_the_nonce_check_is_caught_by_the_database(client, monkeypatch):
    """The check-then-insert has a gap; the unique index is what closes it."""
    monkeypatch.setattr(biometric, "verify_student", _verdict("n-slip"))
    headers = _headers(client)

    assert _mark(client, headers).json()["code"] == "ok"

    # Now make the pre-check blind, exactly as it is for a request that ran its
    # check before the other one committed.
    real_exec = Session.exec

    def blind_to_the_nonce(self, statement, *a, **kw):
        rendered = str(statement)
        if "attendancemark" in rendered.lower() and "sig_nonce" in rendered.lower():
            class _Empty:
                def first(self_inner):
                    return None

                def all(self_inner):
                    return []
            return _Empty()
        return real_exec(self, statement, *a, **kw)

    monkeypatch.setattr(Session, "exec", blind_to_the_nonce)
    response = _mark(client, headers)
    monkeypatch.undo()

    assert response.status_code == 200, response.text
    assert response.json()["code"] == "duplicate"
    with Session(engine) as db:
        assert len(db.exec(select(AttendanceMark)).all()) == 1


def test_the_database_itself_refuses_a_second_row(client):
    """Belt and braces: the constraint is real, not just handled in Python."""
    with Session(engine) as db:
        db.add(Attendance(session_id=1, student_id=SID))
        db.commit()
        db.add(Attendance(session_id=1, student_id=SID))
        with pytest.raises(IntegrityError):
            db.commit()
