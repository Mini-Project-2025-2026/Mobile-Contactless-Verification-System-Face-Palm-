"""Who changed what, from where.

Every console action here alters an academic record — a class extended past its
end, a one-time code issued that lets a face be re-bound to a student id, a
whole cohort put onto a course. At the end of a semester those changes decide
whether a student passes, and until now none of them left a trace.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.config import settings
from app.db import engine
from app.main import app
from app.models import AuditLog, Student
from app.security import hash_password


@pytest.fixture(autouse=True)
def fresh():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id="20512345", name="Ama", password_hash=hash_password("pw"),
                       programme="Computer Science"))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "pw")
    token = client.post("/api/admin/login",
                        json={"username": "admin", "password": "pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_course(client, admin, code: str = "CS101") -> int:
    created = client.post("/api/admin/courses", headers=admin,
                          json={"code": code, "title": "Intro"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _entries(action: str = "") -> list[AuditLog]:
    with Session(engine) as db:
        rows = db.exec(select(AuditLog)).all()
    return [r for r in rows if not action or r.action == action]


def test_opening_a_class_is_recorded_with_where_and_for_how_long(client, admin):
    course_id = _make_course(client, admin)
    client.post("/api/admin/sessions", headers=admin, json={
        "course_id": course_id, "lat": 6.6745, "lng": -1.5716,
        "radius_m": 70, "duration_minutes": 90})

    opened = _entries("session.open")
    assert len(opened) == 1
    assert "90min" in opened[0].detail
    assert "6.67450" in opened[0].detail
    assert opened[0].actor == "admin"


def test_extending_a_class_records_the_new_end_time(client, admin):
    course_id = _make_course(client, admin)
    session_id = client.post("/api/admin/sessions", headers=admin, json={
        "course_id": course_id, "lat": 6.6745, "lng": -1.5716}).json()["session_id"]
    client.post(f"/api/admin/sessions/{session_id}/extend", headers=admin, json={"minutes": 20})

    extended = _entries("session.extend")
    assert len(extended) == 1
    assert "+20min" in extended[0].detail


def test_opening_and_closing_a_window_records_the_transition(client, admin):
    course_id = _make_course(client, admin)
    session_id = client.post("/api/admin/sessions", headers=admin, json={
        "course_id": course_id, "lat": 6.6745, "lng": -1.5716}).json()["session_id"]
    client.post(f"/api/admin/sessions/{session_id}/phase", headers=admin, json={"phase": "end"})

    assert _entries("session.phase")[0].detail == "start -> end"


def test_issuing_a_grant_is_recorded_against_the_student(client, admin):
    """This is the action that lets a face be re-bound to a student id."""
    client.post("/api/admin/enroll-grant", headers=admin,
                json={"student_id": "20512345", "ttl_hours": 24})

    issued = _entries("grant.issue")
    assert len(issued) == 1
    assert issued[0].target == "student:20512345"
    assert "24h" in issued[0].detail


def test_a_rotated_programme_password_is_noted_but_never_recorded(client, admin):
    client.post("/api/admin/programmes/password", headers=admin,
                json={"programme": "Computer Science", "password": "CS@2024"})

    rotated = _entries("programme.password")
    assert len(rotated) == 1
    assert "CS@2024" not in rotated[0].detail
    assert "computer science" in rotated[0].target


def test_the_trail_reads_back_newest_first(client, admin):
    client.post("/api/admin/courses", headers=admin, json={"code": "CS101", "title": "Intro"})
    client.post("/api/admin/courses", headers=admin, json={"code": "CS102", "title": "Data"})

    rows = client.get("/api/admin/audit", headers=admin).json()
    assert [r["action"] for r in rows[:2]] == ["course.create", "course.create"]
    assert "CS102" in rows[0]["detail"]  # the later one first


def test_the_trail_can_be_narrowed_to_one_action(client, admin):
    client.post("/api/admin/courses", headers=admin, json={"code": "CS101", "title": "Intro"})
    client.post("/api/admin/enroll-grant", headers=admin, json={"student_id": "20512345"})

    rows = client.get("/api/admin/audit?action=grant.issue", headers=admin).json()
    assert [r["action"] for r in rows] == ["grant.issue"]


def test_the_trail_is_admin_only(client):
    assert client.get("/api/admin/audit").status_code == 401


def test_a_failed_audit_write_never_fails_the_action(client, admin, monkeypatch):
    """Bookkeeping must not turn "session extended" into "session not extended"."""
    from app import audit

    def broken(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(audit, "record", broken)
    response = client.post("/api/admin/courses", headers=admin,
                           json={"code": "CS999", "title": "Still Created"})
    assert response.status_code == 201


def test_bulk_biometric_enrolment_is_recorded(client, admin, monkeypatch):
    monkeypatch.setattr(biometric, "enroll_users_bulk",
                        lambda people, **kw: biometric.BulkEnrollResult(
                            people=1, enrolled=1, raw={},
                            results=(biometric.BulkPersonResult(
                                user_id="20512345", success=True, enrolled=1,
                                modalities=("face",), message="ok"),)))
    client.post("/api/admin/enroll/bulk", headers=admin,
                json={"people": [{"student_id": "20512345", "images": ["img"]}]})

    assert "1 enrolled of 1 submitted" in _entries("enrol.bulk")[0].detail
