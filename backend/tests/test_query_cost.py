"""What these endpoints cost, counted in queries rather than guessed at.

Every one of these used to issue a query per row of whatever was on screen — per
course, per student, per live session — so the cost grew with the size of the
institution while every test with ten rows in it stayed fast and green. Counting
the statements is the only way that regression shows up before a department
does.
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
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
from app.timeutil import now
from datetime import timedelta

LAT, LNG = 6.6745, -1.5716


@contextlib.contextmanager
def counted():
    """Count SQL statements issued while the block runs."""
    statements: list[str] = []

    def before(_conn, _cursor, statement, *_):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


@pytest.fixture
def campus():
    """Twelve students, three courses, three live sessions, marks recorded."""
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    moment = now()
    with Session(engine) as db:
        courses = []
        for n in range(3):
            course = Course(code=f"CS10{n}", title=f"Course {n}", semester="2025/2026-1")
            db.add(course)
            courses.append(course)
        db.commit()
        for course in courses:
            db.refresh(course)

        for i in range(12):
            sid = f"205000{i:02d}"
            db.add(Student(student_id=sid, name=f"Student {i}",
                           password_hash=hash_password("pw"), programme="Computer Science",
                           enrolled_modality="face"))
            for course in courses:
                db.add(Enrollment(student_id=sid, course_id=course.id))
        db.commit()

        for course in courses:
            session = ClassSession(
                course_id=course.id, title=f"{course.code} L1", lat=LAT, lng=LNG,
                radius_m=70.0, starts_at=moment - timedelta(minutes=5),
                ends_at=moment + timedelta(hours=2), marks_required=2)
            db.add(session)
            db.commit()
            db.refresh(session)
            for i in range(12):
                record = Attendance(session_id=session.id, student_id=f"205000{i:02d}",
                                    status=AttendanceStatus.partial, marks_count=1)
                db.add(record)
                db.commit()
                db.refresh(record)
                db.add(AttendanceMark(attendance_id=record.id, phase="start",
                                      sig_nonce=f"n-{session.id}-{i}"))
            db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_headers(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "pw")
    token = client.post("/api/admin/login",
                        json={"username": "admin", "password": "pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _student_headers(client):
    token = client.post("/api/auth/login", json={
        "student_id": "20500000", "password": "pw", "device_uid": "dev-1"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_the_overview_does_not_load_five_tables(campus, client, admin_headers):
    with counted() as sql:
        assert client.get("/api/admin/overview", headers=admin_headers).status_code == 200
    # four counts and one narrow select for the live sessions
    assert len(sql) <= 6, sql
    assert not any("FROM student" in s and "count" not in s.lower() for s in sql)


def test_listing_courses_is_two_queries_not_one_per_course(campus, client, admin_headers):
    with counted() as sql:
        rows = client.get("/api/admin/courses", headers=admin_headers).json()
    assert [r["students"] for r in rows] == [12, 12, 12]
    assert len(sql) <= 3, sql


def test_listing_students_is_two_queries_not_one_per_student(campus, client, admin_headers):
    with counted() as sql:
        rows = client.get("/api/admin/students", headers=admin_headers).json()
    assert len(rows) == 12
    assert all(len(r["course_ids"]) == 3 for r in rows)
    assert len(sql) <= 3, sql


def test_listing_sessions_is_flat_in_the_number_of_sessions(campus, client, admin_headers):
    with counted() as sql:
        rows = client.get("/api/admin/sessions", headers=admin_headers).json()
    assert len(rows) == 3
    assert all(r["checked_in"] == 12 and r["enrolled"] == 12 for r in rows)
    assert len(sql) <= 5, sql


def test_the_session_roll_is_flat_in_the_number_of_students(campus, client, admin_headers):
    session_id = client.get("/api/admin/sessions", headers=admin_headers).json()[0]["id"]
    with counted() as sql:
        body = client.get(f"/api/admin/attendance?session_id={session_id}",
                          headers=admin_headers).json()
    assert len(body["rows"]) == 12
    assert len(sql) <= 6, sql


def test_the_polled_course_list_is_flat_in_the_number_of_sessions(campus, client):
    """The app polls this every few seconds, for every student in the hall."""
    headers = _student_headers(client)
    with counted() as sql:
        rows = client.get(f"/api/courses/available?lat={LAT}&lng={LNG}", headers=headers).json()
    assert len(rows) == 3
    assert all(r["marked_start"] and not r["marked_end"] for r in rows)
    assert len(sql) <= 9, sql


def test_the_semester_report_reads_only_its_own_course(campus, client, admin_headers):
    """Its cost must not grow with every other lecturer's attendance."""
    course_id = client.get("/api/admin/courses", headers=admin_headers).json()[0]["id"]
    with counted() as sql:
        report = client.get(f"/api/admin/courses/{course_id}/report",
                            headers=admin_headers).json()
    assert report["totals"]["students"] == 12
    # Nothing may read a whole table unfiltered.
    unscoped = [s for s in sql
                if "FROM attendance" in s and "WHERE" not in s.upper()]
    assert not unscoped, unscoped
    assert len(sql) <= 8, sql
