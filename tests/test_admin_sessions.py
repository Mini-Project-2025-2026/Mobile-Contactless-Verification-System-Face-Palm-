"""What the console needs to warn a lecturer before a class expires in START.

Present requires both windows. A session that runs out of time while still in
the START phase leaves everyone who marked stuck on partial, so the session list
reports the partial/present split and the class can be given more time.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.config import settings
from app.db import engine
from app.main import app
from app.models import (
    Attendance,
    AttendanceStatus,
    Course,
    Enrollment,
    Session as ClassSession,
    Student,
)
from app.security import hash_password

LAT, LNG = 6.6745, -1.5716
SID = "20512345"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Ama", password_hash=hash_password("pw")))
        db.add(Course(code="MATH151", title="Algebra & Trig", semester="2025/2026-1"))
        db.commit()
        db.add(Enrollment(student_id=SID, course_id=1))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/api/admin/login", json={
        "username": settings.admin_username, "password": settings.admin_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _session(*, ends_in_minutes: float, phase="start", active=True) -> int:
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        s = ClassSession(
            course_id=1, title="lecture 2", lat=LAT, lng=LNG, radius_m=70.0,
            starts_at=now - timedelta(minutes=30), ends_at=now + timedelta(minutes=ends_in_minutes),
            marks_required=2, phase=phase, active=active,
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id


def _attendance(session_id: int, status: AttendanceStatus) -> None:
    with Session(engine) as db:
        db.add(Attendance(session_id=session_id, student_id=SID, marks_count=1, status=status))
        db.commit()


def test_session_list_splits_partial_from_present(client, auth):
    """The warning has to name a number, so the split has to come from the API."""
    sid = _session(ends_in_minutes=4)
    _attendance(sid, AttendanceStatus.partial)

    (row,) = client.get("/api/admin/sessions", headers=auth).json()
    assert row["phase"] == "start" and row["live"] is True
    assert row["checked_in"] == 1 and row["partial"] == 1 and row["present"] == 0

    # the console's condition: live, still in START, minutes left below the threshold
    left = (datetime.fromisoformat(row["ends_at"]) - datetime.now(timezone.utc)).total_seconds() / 60
    assert 0 < left <= 10


def test_extend_buys_time_from_now_when_the_class_already_expired(client, auth):
    sid = _session(ends_in_minutes=-5, active=False)
    before = client.get("/api/admin/sessions", headers=auth).json()[0]

    r = client.post(f"/api/admin/sessions/{sid}/extend", headers=auth, json={"minutes": 15})
    assert r.status_code == 200

    after = client.get("/api/admin/sessions", headers=auth).json()[0]
    ends = datetime.fromisoformat(after["ends_at"])
    left = (ends - datetime.now(timezone.utc)).total_seconds() / 60
    assert 14 <= left <= 15, "the added minutes must be usable, not spent before the click"
    assert ends > datetime.fromisoformat(before["ends_at"])
    assert after["live"] is True, "an expired class has to come back live to be completable"


def test_extend_on_a_live_class_adds_to_its_own_end(client, auth):
    sid = _session(ends_in_minutes=20)
    before = datetime.fromisoformat(client.get("/api/admin/sessions", headers=auth).json()[0]["ends_at"])

    client.post(f"/api/admin/sessions/{sid}/extend", headers=auth, json={"minutes": 15})
    after = datetime.fromisoformat(client.get("/api/admin/sessions", headers=auth).json()[0]["ends_at"])
    assert 14.9 <= (after - before).total_seconds() / 60 <= 15.1


def test_extend_rejects_silly_values_and_unknown_sessions(client, auth):
    sid = _session(ends_in_minutes=10)
    assert client.post(f"/api/admin/sessions/{sid}/extend", headers=auth, json={"minutes": 0}).status_code == 422
    assert client.post(f"/api/admin/sessions/{sid}/extend", headers=auth, json={"minutes": 999}).status_code == 422
    assert client.post("/api/admin/sessions/4321/extend", headers=auth, json={"minutes": 15}).status_code == 404


def test_extend_needs_an_admin(client):
    sid = _session(ends_in_minutes=10)
    assert client.post(f"/api/admin/sessions/{sid}/extend", json={"minutes": 15}).status_code in (401, 403)
