"""Course discovery: which enrolled courses have a live session near me."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..geo import within_geofence
from ..models import Attendance, AttendanceMark, Course, Enrollment, Session as ClassSession, Student
from ..schemas import AvailableCourse, RecentSession
from ..security import current_student

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
    now = datetime.now(timezone.utc)
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
        ends = s.ends_at if s.ends_at.tzinfo else s.ends_at.replace(tzinfo=timezone.utc)
        starts = s.starts_at if s.starts_at.tzinfo else s.starts_at.replace(tzinfo=timezone.utc)
        if not (starts <= now <= ends):
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


@router.get("/recent", response_model=list[RecentSession])
def recent(
    hours: int = 6,
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> list[RecentSession]:
    """Classes that finished in the last `hours`, newest first.

    A session drops out of /available the instant its window closes, which left
    Home blank and made a just-completed check-in look like it never happened.
    This keeps the outcome on screen for the rest of the day.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=max(1, min(hours, 48)))
    course_ids = _enrolled_course_ids(db, student.student_id)
    if not course_ids:
        return []

    sessions = db.exec(
        select(ClassSession).where(ClassSession.course_id.in_(course_ids))  # type: ignore[attr-defined]
    ).all()

    out: list[RecentSession] = []
    for s in sessions:
        ends = s.ends_at if s.ends_at.tzinfo else s.ends_at.replace(tzinfo=timezone.utc)
        if not (since <= ends < now):
            continue
        course = db.get(Course, s.course_id)
        if course is None:
            continue
        phases = _phases_marked(db, s.id, student.student_id)
        out.append(
            RecentSession(
                session_id=s.id,
                course_code=course.code,
                course_title=course.title,
                session_title=s.title or course.title,
                ended_at=ends,
                marks_count=len(phases),
                marks_required=s.marks_required,
                marked_start="start" in phases,
                marked_end="end" in phases,
                status=_status_for(phases),
            )
        )
    out.sort(key=lambda r: r.ended_at, reverse=True)
    return out
