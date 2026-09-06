"""Course discovery: which enrolled courses have a live session near me."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import queries
from ..db import get_session
from ..geo import within_geofence
from ..models import Attendance, AttendanceMark, Course, Enrollment, Student
from ..models import Session as ClassSession
from ..schemas import AvailableCourse
from ..security import current_student
from ..timeutil import aware_or_now, now

router = APIRouter(prefix="/api/courses", tags=["courses"])


def _enrolled_course_ids(db: Session, student_id: str) -> set[int]:
    rows = db.exec(select(Enrollment.course_id).where(Enrollment.student_id == student_id)).all()
    return set(rows)


def _status_for(phases: set[str]) -> str:
    return "present" if {"start", "end"} <= phases else ("partial" if phases else "absent")


@router.get("/available", response_model=list[AvailableCourse])
def available(
    lat: float,
    lng: float,
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> list[AvailableCourse]:
    """Live sessions for the student's enrolled courses, sorted by distance.

    This is polled: the app calls it every few seconds while a student stands in
    a doorway waiting to see their class appear. It therefore does a fixed
    number of queries regardless of how many classes are running — it used to do
    three per live session, on every poll, for every student in the hall.
    """
    moment = now()
    course_ids = _enrolled_course_ids(db, student.student_id)
    if not course_ids:
        return []

    sessions = [
        s for s in db.exec(
            select(ClassSession).where(
                ClassSession.active == True,  # noqa: E712
                ClassSession.course_id.in_(course_ids),  # type: ignore[attr-defined]
            )
        ).all()
        # naive-datetime safety for SQLite-stored timestamps
        if aware_or_now(s.starts_at) <= moment <= aware_or_now(s.ends_at)
    ]
    if not sessions:
        return []

    courses = {c.id: c for c in queries.fetch_in(db, Course, Course.id, course_ids)}

    # This student's progress across every one of those sessions, in two queries.
    records = {
        a.session_id: a
        for a in db.exec(select(Attendance).where(
            Attendance.student_id == student.student_id,
            Attendance.session_id.in_([s.id for s in sessions]),  # type: ignore[attr-defined]
        )).all()
    }
    phases_by_session: dict[int, set[str]] = {}
    marks = queries.fetch_in(db, AttendanceMark, AttendanceMark.attendance_id,
                             [a.id for a in records.values()])
    attendance_to_session = {a.id: a.session_id for a in records.values()}
    for mark in marks:
        session_id = attendance_to_session.get(mark.attendance_id)
        if session_id is not None:
            phases_by_session.setdefault(session_id, set()).add(mark.phase)

    out: list[AvailableCourse] = []
    for s in sessions:
        course = courses.get(s.course_id)
        if course is None:
            continue
        in_range, dist = within_geofence(lat, lng, s.lat, s.lng, s.radius_m)
        phases = phases_by_session.get(s.id, set())
        out.append(
            AvailableCourse(
                session_id=s.id,
                course_code=course.code,
                course_title=course.title,
                session_title=s.title or course.title,
                lecturer_name=course.lecturer_name,
                distance_m=round(dist, 1),
                radius_m=s.radius_m,
                in_range=in_range,
                ends_at=aware_or_now(s.ends_at),
                center_lat=s.lat,
                center_lng=s.lng,
                phase=s.phase,
                marked_start="start" in phases,
                marked_end="end" in phases,
                status=_status_for(phases),
            )
        )
    out.sort(key=lambda c: c.distance_m)
    return out
