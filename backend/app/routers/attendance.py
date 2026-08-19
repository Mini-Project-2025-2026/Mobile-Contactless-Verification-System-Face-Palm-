"""Attendance history, grouped by semester (mirrors the original app's view)."""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..models import Attendance, Course, Session as ClassSession, Student
from ..schemas import AttendanceItem, SemesterHistory
from ..security import current_student

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.get("/history", response_model=list[SemesterHistory])
def history(
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> list[SemesterHistory]:
    rows = db.exec(
        select(Attendance).where(Attendance.student_id == student.student_id)
    ).all()

    by_semester: dict[str, list[AttendanceItem]] = defaultdict(list)
    for a in rows:
        s = db.get(ClassSession, a.session_id)
        if s is None:
            continue
        course = db.get(Course, s.course_id)
        if course is None:
            continue
        when = a.last_marked_at or a.first_marked_at or s.starts_at
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        by_semester[course.semester].append(
            AttendanceItem(
                course_code=course.code,
                course_title=course.title,
                session_title=s.title or course.title,
                date=when,
                status=a.status,
            )
        )

    out = [
        SemesterHistory(semester=sem, items=sorted(items, key=lambda i: i.date, reverse=True))
        for sem, items in by_semester.items()
    ]
    out.sort(key=lambda h: h.semester, reverse=True)
    return out
