"""A finished class, and the marks earned in it, must stay visible.

A session leaves /available the moment its window closes. On its own that left
Home blank right after a successful check-in, which reads as "the app lost my
attendance" — so /recent reports what just happened.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.db import engine
from app.main import app
from app.models import (
    Attendance,
    AttendanceMark,
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
        db.add(Student(student_id=SID, name="Ama", password_hash=hash_password("pw"),
                       enrolled_modality="face"))
        course = Course(code="MATH151", title="Algebra & Trig", semester="2025/2026-1")
        db.add(course)
        db.commit()
        db.refresh(course)
        db.add(Enrollment(student_id=SID, course_id=course.id))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/login", json={"student_id": SID, "password": "pw", "device_uid": "dev-1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _session(*, ended_minutes_ago: float, title="lecture 2") -> int:
    """A session whose window closed `ended_minutes_ago` minutes back."""
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        s = ClassSession(
            course_id=1, title=title, lat=LAT, lng=LNG, radius_m=70.0,
            starts_at=now - timedelta(minutes=ended_minutes_ago + 31),
            ends_at=now - timedelta(minutes=ended_minutes_ago),
            marks_required=2,
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id


def _mark(session_id: int, *phases: str) -> None:
    with Session(engine) as db:
        a = Attendance(session_id=session_id, student_id=SID, marks_count=len(phases),
                       status=AttendanceStatus.present if len(phases) == 2 else AttendanceStatus.partial)
        db.add(a)
        db.commit()
        db.refresh(a)
        for p in phases:
            db.add(AttendanceMark(attendance_id=a.id, marked_at=datetime.now(timezone.utc),
                                  distance_m=5.0, score=0.8, modality="face", phase=p,
                                  sig_nonce=f"n-{session_id}-{p}"))
        db.commit()


def test_just_ended_session_reports_the_start_mark(client, auth):
    """Exactly this morning's case: start marked, window closed 5 min later."""
    sid = _session(ended_minutes_ago=5)
    _mark(sid, "start")

    r = client.get("/api/courses/recent", headers=auth)
    assert r.status_code == 200
    (item,) = r.json()
    assert item["course_code"] == "MATH151"
    assert item["status"] == "partial"
    assert item["marked_start"] is True and item["marked_end"] is False
    assert item["marks_count"] == 1 and item["marks_required"] == 2


def test_completed_session_reads_present(client, auth):
    sid = _session(ended_minutes_ago=30)
    _mark(sid, "start", "end")
    (item,) = client.get("/api/courses/recent", headers=auth).json()
    assert item["status"] == "present" and item["marks_count"] == 2


def test_unmarked_session_reads_absent(client, auth):
    _session(ended_minutes_ago=10)
    (item,) = client.get("/api/courses/recent", headers=auth).json()
    assert item["status"] == "absent" and item["marks_count"] == 0


def test_live_and_long_finished_sessions_are_excluded(client, auth):
    _session(ended_minutes_ago=60 * 9)          # yesterday's, outside the window
    live = ClassSession(
        course_id=1, title="live", lat=LAT, lng=LNG, radius_m=70.0,
        starts_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ends_at=datetime.now(timezone.utc) + timedelta(hours=1), marks_required=2,
    )
    with Session(engine) as db:
        db.add(live)
        db.commit()

    assert client.get("/api/courses/recent", headers=auth).json() == []


def test_newest_first_and_window_is_configurable(client, auth):
    _session(ended_minutes_ago=200, title="early")
    _session(ended_minutes_ago=20, title="late")

    titles = [i["session_title"] for i in client.get("/api/courses/recent", headers=auth).json()]
    assert titles == ["late", "early"]

    narrow = client.get("/api/courses/recent?hours=1", headers=auth).json()
    assert [i["session_title"] for i in narrow] == ["late"]


def test_other_students_courses_are_not_reported(client, auth):
    with Session(engine) as db:
        other = Course(code="CS101", title="Intro", semester="2025/2026-1")
        db.add(other)
        db.commit()
        db.refresh(other)
        now = datetime.now(timezone.utc)
        db.add(ClassSession(course_id=other.id, title="not mine", lat=LAT, lng=LNG, radius_m=70.0,
                            starts_at=now - timedelta(hours=2), ends_at=now - timedelta(minutes=10),
                            marks_required=2))
        db.commit()

    assert client.get("/api/courses/recent", headers=auth).json() == []
