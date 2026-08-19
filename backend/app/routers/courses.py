"""Course discovery: which enrolled courses have a live session near me."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..geo import within_geofence
from ..models import Attendance, AttendanceMark, Course, Enrollment, Session as ClassSession, Student
from ..schemas import AvailableCourse
from ..security import current_student
from ..timeutil import aware_or_now, now

router = APIRouter(prefix="/api/courses", tags=["courses"])


def _enrolled_course_ids(db: Session, student_id: str) -> set[int]:
    rows = db.exec(select(Enrollment.course_id).where(Enrollment.student_id == student_id)).all()
    return set(rows)


def _phases_marked(db: Session, session_id: int, student_id: str) -> set[str]:
    """Which windows (start/end) this student has already been marked in."""
    att = db.exec(select(Attendance).where(
        Attendance.session_id == session_id, Attendance.student_id == student_id)).first()
    if att is None:
        return set()
    return {m.phase for m in db.exec(
        select(AttendanceMark).where(AttendanceMark.attendance_id == att.id)).all()}


def _status_for(phases: set[str]) -> str:
    return "present" if {"start", "end"} <= phases else ("partial" if phases else "absent")


@router.get("/available", response_model=list[AvailableCourse])
def available(
    lat: float,
    lng: float,
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> list[AvailableCourse]:
    """Live sessions for the student's enrolled courses, sorted by distance."""
    moment = now()
    course_ids = _enrolled_course_ids(db, student.student_id)
    if not course_ids:
        return []

    sessions = db.exec(
        select(ClassSession).where(
            ClassSession.active == True,  # noqa: E712
            ClassSession.course_id.in_(course_ids),  # type: ignore[attr-defined]
        )
    ).all()

    out: list[AvailableCourse] = []
    for s in sessions:
        # naive-datetime safety for SQLite-stored timestamps
        ends = aware_or_now(s.ends_at)
        starts = aware_or_now(s.starts_at)
        if not (starts <= moment <= ends):
            continue
        course = db.get(Course, s.course_id)
        if course is None:
            continue
        in_range, dist = within_geofence(lat, lng, s.lat, s.lng, s.radius_m)

        # this student's per-phase progress for the session
        phases = _phases_marked(db, s.id, student.student_id)
        status = _status_for(phases)

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
                ends_at=ends,
                center_lat=s.lat,
                center_lng=s.lng,
                phase=s.phase,
                marked_start="start" in phases,
                marked_end="end" in phases,
                status=status,
            )
        )
    out.sort(key=lambda c: c.distance_m)
    return out
