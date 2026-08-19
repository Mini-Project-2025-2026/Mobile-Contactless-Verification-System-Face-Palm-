"""Seed demo data: one student, two courses, one live geofenced session.

Run:  python -m app.seed
Login:  student_id=20512345  password=passw0rd  device_uid=<anything>

The student_id (20512345) must exist as an enrolled user in the Biometric
Verify tenant for check-in to succeed against a real service.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .db import engine, init_db
from .models import Course, Enrollment, Session as ClassSession, Student
from .security import hash_password

# KNUST main campus-ish coordinates (used as the session centre for the demo).
DEMO_LAT, DEMO_LNG = 6.6745, -1.5716
DEMO_STUDENT_ID = "20512345"


def run() -> None:
    init_db()
    with Session(engine) as db:
        if db.exec(select(Student).where(Student.student_id == DEMO_STUDENT_ID)).first():
            print("Seed already present.")
            return

        student = Student(
            student_id=DEMO_STUDENT_ID,
            name="Ama Mensah",
            password_hash=hash_password("passw0rd"),
            semester="2025/2026-1",
            reference_no="20997359",
            programme="Computer Science",
            year_group="2023/2024",
            class_group="1",
        )
        db.add(student)

        cs101 = Course(code="CS101", title="Intro to Computing", semester="2025/2026-1", lecturer_name="Dr. Osei")
        math151 = Course(code="MATH151", title="Algebra & Trig", semester="2025/2026-1", lecturer_name="Prof. Adjei")
        db.add(cs101)
        db.add(math151)
        db.commit()
        db.refresh(cs101)
        db.refresh(math151)

        db.add(Enrollment(student_id=DEMO_STUDENT_ID, course_id=cs101.id))
        db.add(Enrollment(student_id=DEMO_STUDENT_ID, course_id=math151.id))

        now = datetime.now(timezone.utc)
        db.add(ClassSession(
            course_id=cs101.id,
            title="CS101 Lecture 5",
            lat=DEMO_LAT, lng=DEMO_LNG, radius_m=70.0,
            starts_at=now - timedelta(minutes=10),
            # long window so the hosted demo always has a live session to check into
            ends_at=now + timedelta(days=30),
            marks_required=2,
        ))
        db.commit()
        print(f"Seeded student {DEMO_STUDENT_ID} (password 'passw0rd'), 2 courses, 1 live CS101 session "
              f"at ({DEMO_LAT}, {DEMO_LNG}).")


if __name__ == "__main__":
    run()
