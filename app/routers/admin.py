"""Admin console API: courses, students, geofenced sessions, attendance.

Auth: POST /api/admin/login with the configured admin credentials returns a
12-hour admin token; every other endpoint requires it (role=admin).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import (
    Attendance,
    AttendanceStatus,
    Course,
    EnrollGrant,
    Enrollment,
    Session as ClassSession,
    Student,
)
from ..security import create_admin_token, current_admin, hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- auth ----------
class AdminLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def admin_login(body: AdminLogin) -> dict:
    if body.username != settings.admin_username or body.password != settings.admin_password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin credentials")
    return {"access_token": create_admin_token(), "token_type": "bearer"}


# ---------- overview ----------
@router.get("/overview")
def overview(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    now = datetime.now(timezone.utc)
    sessions = db.exec(select(ClassSession)).all()
    live = sum(1 for s in sessions if s.active and _aware(s.starts_at) <= now <= _aware(s.ends_at))
    return {
        "students": len(db.exec(select(Student)).all()),
        "courses": len(db.exec(select(Course)).all()),
        "sessions": len(sessions),
        "live_sessions": live,
        "attendance_records": len(db.exec(select(Attendance)).all()),
    }


# ---------- courses ----------
class CourseIn(BaseModel):
    code: str
    title: str
    semester: str = "2025/2026-1"
    lecturer_name: str = ""


@router.get("/courses")
def list_courses(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    return [c.model_dump() for c in db.exec(select(Course)).all()]


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    c = Course(**body.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.model_dump()


# ---------- students ----------
class StudentIn(BaseModel):
    student_id: str
    name: str
    password: str = "passw0rd"
    programme: str = ""
    year_group: str = ""
    class_group: str = ""
    reference_no: str = ""
    semester: str = "2025/2026-1"


@router.get("/students")
def list_students(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    out = []
    for s in db.exec(select(Student)).all():
        course_ids = db.exec(select(Enrollment.course_id).where(Enrollment.student_id == s.student_id)).all()
        out.append({
            "student_id": s.student_id, "name": s.name, "programme": s.programme,
            "year_group": s.year_group, "class_group": s.class_group,
            "biometric_enrolled": s.enrolled_at is not None, "course_ids": list(course_ids),
        })
    return out


@router.post("/students", status_code=201)
def create_student(body: StudentIn, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    if db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "student_id already exists")
    s = Student(
        student_id=body.student_id, name=body.name, password_hash=hash_password(body.password),
        programme=body.programme, year_group=body.year_group, class_group=body.class_group,
        reference_no=body.reference_no, semester=body.semester,
    )
    db.add(s)
    db.commit()
    return {"student_id": s.student_id, "name": s.name}


class EnrollCourseIn(BaseModel):
    student_id: str
    course_id: int


@router.post("/enroll-course", status_code=201)
def enroll_course(body: EnrollCourseIn, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    if db.get(Course, body.course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    if not db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown student")
    existing = db.exec(select(Enrollment).where(
        Enrollment.student_id == body.student_id, Enrollment.course_id == body.course_id)).first()
    if existing is None:
        db.add(Enrollment(student_id=body.student_id, course_id=body.course_id))
        db.commit()
    return {"student_id": body.student_id, "course_id": body.course_id, "enrolled": True}


# ---------- sessions ----------
class SessionIn(BaseModel):
    course_id: int
    title: str = ""
    lat: float
    lng: float
    radius_m: float = Field(default=70.0, ge=10, le=1000)
    duration_minutes: int = Field(default=90, ge=5, le=1440)
    marks_required: int = Field(default=2, ge=1, le=5)


@router.get("/sessions")
def list_sessions(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for s in db.exec(select(ClassSession)).all():
        c = db.get(Course, s.course_id)
        rows = db.exec(select(Attendance).where(Attendance.session_id == s.id)).all()
        # Split the rows so the console can say who is still short of present:
        # a session that ends while stuck in START leaves every one of them partial.
        partial = sum(1 for a in rows if a.status == AttendanceStatus.partial)
        present = sum(1 for a in rows if a.status == AttendanceStatus.present)
        out.append({
            "id": s.id, "course_code": c.code if c else "?", "title": s.title,
            "lat": s.lat, "lng": s.lng, "radius_m": s.radius_m,
            "starts_at": _aware(s.starts_at).isoformat(), "ends_at": _aware(s.ends_at).isoformat(),
            "live": s.active and _aware(s.starts_at) <= now <= _aware(s.ends_at), "active": s.active,
            "phase": s.phase, "marks_required": s.marks_required, "checked_in": len(rows),
            "partial": partial, "present": present,
        })
    return sorted(out, key=lambda x: x["starts_at"], reverse=True)


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    if db.get(Course, body.course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    now = datetime.now(timezone.utc)
    s = ClassSession(
        course_id=body.course_id, title=body.title, lat=body.lat, lng=body.lng,
        radius_m=body.radius_m, starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(minutes=body.duration_minutes), marks_required=body.marks_required,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"session_id": s.id}


@router.post("/sessions/{session_id}/close")
def close_session(session_id: int, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    s.active = False
    s.phase = "closed"
    db.add(s)
    db.commit()
    return {"session_id": session_id, "active": False}


class PhaseIn(BaseModel):
    phase: str  # "start" | "end" | "closed"


@router.post("/sessions/{session_id}/phase")
def set_phase(session_id: int, body: PhaseIn, _: str = Depends(current_admin),
              db: Session = Depends(get_session)) -> dict:
    """Open the start/end check-in window (or pause it). Present needs both windows."""
    if body.phase not in ("start", "end", "closed"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "phase must be start|end|closed")
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    s.phase = body.phase
    db.add(s)
    db.commit()
    return {"session_id": session_id, "phase": s.phase}


class ExtendIn(BaseModel):
    minutes: int = Field(default=15, ge=1, le=180)


@router.post("/sessions/{session_id}/extend")
def extend_session(session_id: int, body: ExtendIn, _: str = Depends(current_admin),
                   db: Session = Depends(get_session)) -> dict:
    """Push a session's end time back, so the END window still has room to run.

    Two-phase attendance needs both windows inside the session; a class that
    expires while still in START leaves everyone who marked stuck on partial.
    Extending from an already-expired session runs from now, not from the old
    end time, so the added minutes are minutes students can actually use.
    """
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    now = datetime.now(timezone.utc)
    base = max(_aware(s.ends_at), now)
    s.ends_at = base + timedelta(minutes=body.minutes)
    if not s.active:  # bringing a closed class back needs it live again
        s.active = True
    db.add(s)
    db.commit()
    return {"session_id": session_id, "ends_at": _aware(s.ends_at).isoformat(), "active": s.active}


# ---------- enrolment grants (one-time re-enrolment / new-device codes) ----------
class GrantIn(BaseModel):
    student_id: str
    ttl_hours: int = Field(default=24, ge=1, le=720)


@router.post("/enroll-grant", status_code=201)
def issue_grant(body: GrantIn, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    if not db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown student")
    token = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8].upper()
    expires = datetime.now(timezone.utc) + timedelta(hours=body.ttl_hours)
    db.add(EnrollGrant(token=token, student_id=body.student_id, expires_at=expires))
    db.commit()
    return {"student_id": body.student_id, "token": token, "expires_at": expires.isoformat(),
            "note": "Give this one-time code to the student to re-enrol or enrol on a new device."}


@router.get("/enroll-grants")
def list_grants(student_id: str, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.exec(select(EnrollGrant).where(EnrollGrant.student_id == student_id)).all()
    now = datetime.now(timezone.utc)
    out = []
    for g in rows:
        exp = g.expires_at if g.expires_at.tzinfo else g.expires_at.replace(tzinfo=timezone.utc)
        out.append({"token": g.token, "expires_at": exp.isoformat(),
                    "used": g.used_at is not None, "expired": exp < now})
    return sorted(out, key=lambda x: (x["used"] or x["expired"], x["expires_at"]))


@router.post("/enroll-grants/{token}/revoke")
def revoke_grant(token: str, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    g = db.exec(select(EnrollGrant).where(EnrollGrant.token == token)).first()
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    if g.used_at is None:  # marking it used = revoked (validation rejects used tokens)
        g.used_at = datetime.now(timezone.utc)
        db.add(g)
        db.commit()
    return {"token": token, "revoked": True}


# ---------- attendance ----------
@router.get("/attendance")
def session_attendance(session_id: int, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    course = db.get(Course, s.course_id)
    enrolled = db.exec(select(Enrollment.student_id).where(Enrollment.course_id == s.course_id)).all()
    rows = []
    for sid in enrolled:
        stu = db.exec(select(Student).where(Student.student_id == sid)).first()
        att = db.exec(select(Attendance).where(
            Attendance.session_id == session_id, Attendance.student_id == sid)).first()
        rows.append({
            "student_id": sid, "name": stu.name if stu else sid,
            "status": att.status.value if att else "absent",
            "marks": att.marks_count if att else 0,
            "score": round(att.best_score, 3) if att else 0.0,
            "last_marked_at": (att.last_marked_at.isoformat() if att and att.last_marked_at else None),
        })
    rows.sort(key=lambda r: (r["status"] != "present", r["name"]))
    return {"session_id": session_id, "course": course.code if course else "?", "title": s.title, "rows": rows}
