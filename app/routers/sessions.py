"""Lecturer-side: create a geofenced attendance session for a course.

Phase 1 keeps this open to any authenticated student for demo/testing; Phase 2
gates it behind a lecturer role.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import Course, Session as ClassSession, Student
from ..schemas import CreateSessionRequest
from ..security import current_student

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_session(
    req: CreateSessionRequest,
    _: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> dict:
    if db.get(Course, req.course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    s = ClassSession(
        course_id=req.course_id,
        title=req.title,
        lat=req.lat,
        lng=req.lng,
        radius_m=req.radius_m or settings.geofence_default_radius_m,
        starts_at=req.starts_at,
        ends_at=req.ends_at,
        marks_required=req.marks_required,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"session_id": s.id, "radius_m": s.radius_m}


@router.post("/{session_id}/close")
def close_session(
    session_id: int,
    _: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> dict:
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    s.active = False
    db.add(s)
    db.commit()
    return {"session_id": session_id, "active": False}
