"""The rules the database itself keeps, and the catch-up that installs them.

These are not tests of application logic. They assert that a second attendance
row for the same student in the same class, a repeated course registration and a
replayed signature are impossible at the storage layer — so that a race between
two requests cannot produce what the code alone was trusted to prevent.
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app import migrate
from app.db import engine
from app.models import Attendance, AttendanceMark, Enrollment, Student, norm_programme


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield


def test_one_attendance_row_per_student_per_session():
    with Session(engine) as db:
        db.add(Attendance(session_id=1, student_id="S1"))
        db.commit()
        db.add(Attendance(session_id=1, student_id="S1"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_signature_can_only_be_counted_once():
    with Session(engine) as db:
        db.add(AttendanceMark(attendance_id=1, sig_nonce="abc"))
        db.commit()
        # A different attendance row, same signed verdict: still refused. This is
        # the replay guard, and it must not depend on which row it lands on.
        db.add(AttendanceMark(attendance_id=2, sig_nonce="abc"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_student_is_on_a_course_once():
    with Session(engine) as db:
        db.add(Enrollment(student_id="S1", course_id=7))
        db.commit()
        db.add(Enrollment(student_id="S1", course_id=7))
        with pytest.raises(IntegrityError):
            db.commit()


def _legacy_db() -> tuple[object, pathlib.Path]:
    """A database shaped like the release before any of this existed."""
    path = pathlib.Path(tempfile.gettempdir()) / f"legacy_{uuid.uuid4().hex}.db"
    old = create_engine(f"sqlite:///{path}")
    with old.begin() as conn:
        conn.execute(text(
            "CREATE TABLE student (id INTEGER PRIMARY KEY, student_id VARCHAR, "
            "name VARCHAR, password_hash VARCHAR, programme VARCHAR)"))
        conn.execute(text(
            "CREATE TABLE attendance (id INTEGER PRIMARY KEY, session_id INTEGER, "
            "student_id VARCHAR, status VARCHAR, marks_count INTEGER, best_score FLOAT)"))
        conn.execute(text(
            "CREATE TABLE enrollment (id INTEGER PRIMARY KEY, student_id VARCHAR, course_id INTEGER)"))
        conn.execute(text(
            "CREATE TABLE attendancemark (id INTEGER PRIMARY KEY, attendance_id INTEGER, "
            "sig_nonce VARCHAR)"))
        conn.execute(text(
            "INSERT INTO student (student_id, name, password_hash, programme) "
            "VALUES ('S1', 'Ama', '', 'Computer  Science')"))
        # the duplicates a check-then-insert could leave behind
        conn.execute(text("INSERT INTO attendance (session_id, student_id) VALUES (1, 'S1'), (1, 'S1')"))
        conn.execute(text("INSERT INTO enrollment (student_id, course_id) VALUES ('S1', 2), ('S1', 2)"))
        conn.execute(text("INSERT INTO attendancemark (attendance_id, sig_nonce) VALUES (1, 'n'), (2, 'n')"))
    return old, path


def test_migration_adds_columns_indexes_and_repairs_duplicates():
    old, path = _legacy_db()
    try:
        migrate.run(old)

        columns = {c["name"] for c in inspect(old).get_columns("student")}
        assert {"programme_key", "active", "enroll_device_uid"} <= columns

        indexes = {ix["name"] for ix in inspect(old).get_indexes("attendance")}
        assert "uq_attendance_session_student" in indexes

        with old.begin() as conn:
            # one row survived of each duplicate pair, and it is the original
            assert conn.execute(text("SELECT COUNT(*) FROM attendance")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM enrollment")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM attendancemark")).scalar() == 1
            # and the normalised programme was backfilled, not left blank
            assert conn.execute(text("SELECT programme_key FROM student")).scalar() == \
                norm_programme("Computer  Science")
    finally:
        old.dispose()
        path.unlink(missing_ok=True)


def test_migration_is_safe_to_run_twice():
    old, path = _legacy_db()
    try:
        migrate.run(old)
        migrate.run(old)  # a second boot must be a no-op, not an error
        with old.begin() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM attendance")).scalar() == 1
    finally:
        old.dispose()
        path.unlink(missing_ok=True)


def test_programme_key_is_derived_on_write_not_by_the_caller():
    """Nobody has to remember it: any write of a Student keeps the key in step."""
    with Session(engine) as db:
        db.add(Student(student_id="S1", name="Ama", password_hash="",
                       programme="Computer Science"))  # key deliberately not set
        db.commit()
        found = db.exec(select(Student).where(
            Student.programme_key == norm_programme("computer   SCIENCE"))).all()
        assert [s.student_id for s in found] == ["S1"]

        # and a rename follows through, rather than leaving the old key behind
        found[0].programme = "Information Technology"
        db.add(found[0])
        db.commit()
        db.refresh(found[0])
        assert found[0].programme_key == norm_programme("Information Technology")
