"""The record a lecturer takes away at the end of a semester.

One course, every class, every enrolled student: who was verified in which window,
who never enrolled at all, and the rate per student. It has to be downloadable and
it has to be complete, because it is the artefact an attendance dispute is settled
from months later.
"""
import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.config import settings
from app.db import engine
from app.main import app
from app.models import (
    Attendance,
    AttendanceMark,
    AttendanceStatus,
    Course,
    Enrollment,
    Modality,
    Student,
)
from app.models import (
    Session as ClassSession,
)
from app.security import hash_password

LAT, LNG = 6.6745, -1.5716
NOW = datetime.now(UTC)

AMA, KOFI, YAA = "20512345", "20512399", "20512400"


@pytest.fixture(autouse=True)
def fresh_db():
    """One course, three weeks, three students with three different stories."""
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Course(code="MATH151", title="Algebra & Trig", semester="2025/2026-1",
                      lecturer_name="Prof. Adjei"))
        db.add(Student(student_id=AMA, name="Ama Mensah", password_hash=hash_password("pw"),
                       programme="Computer Science", year_group="2023/2024", class_group="1",
                       reference_no="20997359", enrolled_modality="face,palm",
                       enrolled_at=NOW - timedelta(days=30)))
        db.add(Student(student_id=KOFI, name="Kofi Owusu", password_hash=hash_password("pw"),
                       programme="Computer Science", year_group="2023/2024", class_group="1",
                       enrolled_modality="face", enrolled_at=NOW - timedelta(days=29)))
        # never enrolled: absent for a different reason, and the report must say so
        db.add(Student(student_id=YAA, name="Yaa Asante", password_hash=hash_password("pw"),
                       programme="Computer Science", year_group="2023/2024", class_group="1"))
        db.commit()
        for sid in (AMA, KOFI, YAA):
            db.add(Enrollment(student_id=sid, course_id=1))
        for week in range(3):
            start = NOW - timedelta(days=21 - week * 7)
            db.add(ClassSession(course_id=1, title=f"Week {week + 1}", lat=LAT, lng=LNG,
                                radius_m=70.0, starts_at=start,
                                ends_at=start + timedelta(hours=2), marks_required=2,
                                phase="closed", active=False))
        db.commit()

        # Ama: present in weeks 1 and 2, missed week 3 entirely
        _mark(db, 1, AMA, ["start", "end"], AttendanceStatus.present, score=0.81)
        _mark(db, 2, AMA, ["start", "end"], AttendanceStatus.present, score=0.77)
        # Kofi: present week 1, only the start mark in week 2, nothing in week 3
        _mark(db, 1, KOFI, ["start", "end"], AttendanceStatus.present, score=0.69)
        _mark(db, 2, KOFI, ["start"], AttendanceStatus.partial, score=0.72)
    yield


def _mark(db, session_id, student_id, phases, status, score):
    attendance = Attendance(session_id=session_id, student_id=student_id, status=status,
                            marks_count=len(phases), best_score=score,
                            first_marked_at=NOW - timedelta(days=10),
                            last_marked_at=NOW - timedelta(days=10))
    db.add(attendance)
    db.commit()
    db.refresh(attendance)
    for phase in phases:
        db.add(AttendanceMark(attendance_id=attendance.id, marked_at=NOW - timedelta(days=10),
                              distance_m=12.5, score=score, modality=Modality.face,
                              phase=phase, sig_nonce=f"n{session_id}{student_id}{phase}"))
    db.commit()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/api/admin/login", json={
        "username": settings.admin_username, "password": settings.admin_password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _rows(csv_text, skip_preamble=0):
    return list(csv.reader(io.StringIO(csv_text)))[skip_preamble:]


def test_the_report_covers_every_class_and_every_enrolled_student(client, auth):
    r = client.get("/api/admin/courses/1/report", headers=auth)
    assert r.status_code == 200
    report = r.json()

    assert report["course"]["code"] == "MATH151"
    assert [c["title"] for c in report["classes"]] == ["Week 1", "Week 2", "Week 3"]
    assert [s["student_id"] for s in report["students"]] == [AMA, KOFI, YAA]
    assert report["totals"] == {"classes_held": 3, "students": 3,
                                "never_enrolled": 1, "full_attendance": 0}


def test_each_student_carries_their_own_week_by_week_story(client, auth):
    report = client.get("/api/admin/courses/1/report", headers=auth).json()
    ama, kofi, yaa = report["students"]

    assert [c["status"] for c in ama["classes"]] == ["present", "present", "absent"]
    assert ama["present"] == 2 and ama["absent"] == 1 and ama["rate"] == round(2 / 3, 4)

    # the distinction that matters: Kofi was in the room in week 2, and still not present
    assert [c["status"] for c in kofi["classes"]] == ["present", "partial", "absent"]
    week2 = kofi["classes"][1]
    assert week2["marked_start"] is True and week2["marked_end"] is False

    # and Yaa is absent for a different reason entirely
    assert yaa["biometrics_enrolled"] is False and yaa["present"] == 0


def test_the_register_downloads_as_a_named_file(client, auth):
    r = client.get("/api/admin/courses/1/report.csv", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "MATH151_2025-2026-1_roll_" in r.headers["content-disposition"]


def test_the_register_reads_like_a_register(client, auth):
    text = client.get("/api/admin/courses/1/report.csv", headers=auth).text
    rows = _rows(text)

    assert rows[0][0] == "MATH151 - Algebra & Trig"
    header = rows[3]
    assert header[0] == "Student ID" and header[6] == "Biometrics enrolled"
    assert header[7].endswith("Week 1") and header[9].endswith("Week 3")
    assert header[-1] == "Attendance %"

    ama = next(r for r in rows if r and r[0] == AMA)
    assert ama[1] == "Ama Mensah" and ama[6] == "face,palm"
    assert ama[7] == "present (SE)" and ama[9] == "-"
    assert ama[-1] == "66.7"

    kofi = next(r for r in rows if r and r[0] == KOFI)
    assert kofi[8] == "partial (S-)", "a missed end window must be visible, not rounded away"

    yaa = next(r for r in rows if r and r[0] == YAA)
    assert yaa[6] == "NOT ENROLLED"


def test_the_audit_shape_carries_the_detail_behind_each_figure(client, auth):
    text = client.get("/api/admin/courses/1/report.csv?shape=marks", headers=auth).text
    rows = _rows(text)

    assert rows[0][:6] == ["Student ID", "Name", "Class", "Date", "Session ID", "Status"]
    assert len(rows) == 1 + 9, "three students times three classes"

    week1_ama = next(r for r in rows if r[0] == AMA and r[2] == "Week 1")
    assert week1_ama[5] == "present"
    assert week1_ama[6] == "yes" and week1_ama[7] == "yes"
    assert week1_ama[9] == "0.8100"
    assert week1_ama[12] == "face" and week1_ama[13] == "12.5"


def test_a_course_with_no_classes_yet_still_reports_its_students(client, auth):
    with Session(engine) as db:
        db.add(Course(code="CS101", title="Intro", semester="2025/2026-1"))
        db.commit()
        db.add(Enrollment(student_id=AMA, course_id=2))
        db.commit()

    report = client.get("/api/admin/courses/2/report", headers=auth).json()
    assert report["totals"]["classes_held"] == 0
    assert report["students"][0]["rate"] == 0.0, "no classes must not divide by zero"


def test_the_report_is_admin_only_and_checks_the_course(client, auth):
    assert client.get("/api/admin/courses/1/report").status_code in (401, 403)
    assert client.get("/api/admin/courses/1/report.csv").status_code in (401, 403)
    assert client.get("/api/admin/courses/99/report", headers=auth).status_code == 404
    assert client.get("/api/admin/courses/1/report.csv?shape=nonsense",
                      headers=auth).status_code == 400
