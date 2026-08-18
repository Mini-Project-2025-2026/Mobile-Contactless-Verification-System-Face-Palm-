"""Check-in: geofence + biometric verify -> attendance mark.

This is the substituted mechanism. Where the original app accepts an
admin-generated PIN, this endpoint requires a live biometric verify whose
HMAC-signed verdict the backend independently validates before recording a mark.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from .. import biometric
from ..config import settings
from ..db import get_session
from ..geo import within_geofence
from ..models import (
    Attendance,
    AttendanceMark,
    AttendanceStatus,
    Enrollment,
    Session as ClassSession,
    Student,
)
from ..schemas import ChallengeRequest, ChallengeResponse, VerifyRequest, VerifyResponse
from ..security import current_student

router = APIRouter(prefix="/api/checkin", tags=["checkin"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_open_session(db: Session, session_id: int) -> ClassSession:
    s = db.get(ClassSession, session_id)
    now = datetime.now(timezone.utc)
    if s is None or not s.active or not (_aware(s.starts_at) <= now <= _aware(s.ends_at)):
        raise HTTPException(status.HTTP_409_CONFLICT, "session_closed")
    return s


def _require_enrolled(db: Session, student_id: str, course_id: int) -> None:
    row = db.exec(
        select(Enrollment).where(Enrollment.student_id == student_id, Enrollment.course_id == course_id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_enrolled")


@router.post("/challenge", response_model=ChallengeResponse)
def challenge(
    req: ChallengeRequest,
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> ChallengeResponse:
    """Fetch a liveness head-turn token for a session the student may check into."""
    s = _load_open_session(db, req.session_id)
    _require_enrolled(db, student.student_id, s.course_id)
    try:
        ch = biometric.get_challenge()
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}") from exc
    return ChallengeResponse(token=ch.token, instruction=ch.instruction, active=ch.active)


@router.post("/verify", response_model=VerifyResponse)
def verify(
    req: VerifyRequest,
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> VerifyResponse:
    s = _load_open_session(db, req.session_id)
    _require_enrolled(db, student.student_id, s.course_id)

    # Face is compulsory: no attendance can be marked (by any modality) until a
    # face template exists. Palm alone is not enough. Enforced server-side so it
    # holds even outside the app.
    if "face" not in (student.enrolled_modality or "").split(","):
        return VerifyResponse(
            ok=False, status=_status_now(db, s.id, student.student_id),
            marks_count=_marks_now(db, s.id, student.student_id),
            marks_required=s.marks_required, distance_m=0.0, code="face_required",
            message="Enrol your face first (required) before you can mark attendance.",
        )

    # 1) Geofence — server-authoritative.
    in_range, distance = within_geofence(req.gps.lat, req.gps.lng, s.lat, s.lng, s.radius_m)
    distance = round(distance, 1)
    if not in_range:
        return VerifyResponse(
            ok=False, status=_status_now(db, s.id, student.student_id), marks_count=_marks_now(db, s.id, student.student_id),
            marks_required=s.marks_required, distance_m=distance, code="not_in_geofence",
            message=f"You are {distance:.0f} m away; move within {s.radius_m:.0f} m of the class.",
        )

    # 2) Biometric verify (1:1 against this student's enrolled template).
    try:
        result = biometric.verify_student(
            student.student_id, frames=req.frames, token=req.token, image=req.image
        )
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}") from exc

    # 3) Trust gates: verdict must be granted, signature valid, identity + score right.
    if not result.signature_valid:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "bad_signature")
    if not result.success or result.user_id != student.student_id:
        return _fail(db, s, student.student_id, distance, "biometric_mismatch",
                     "Face/palm did not match your enrolled record.")
    if result.score < settings.min_verify_score:
        return _fail(db, s, student.student_id, distance, "low_confidence",
                     "Capture quality too low — try again in better light.")

    # 4) Record the mark (idempotent on the signature nonce → blocks replay).
    attendance = _get_or_create_attendance(db, s.id, student.student_id)
    if result.nonce and db.exec(
        select(AttendanceMark).where(AttendanceMark.sig_nonce == result.nonce)
    ).first():
        # Same signed verdict submitted twice — return current state, don't double-count.
        return _state_response(attendance, s, distance, result.score, code="duplicate",
                               message="This capture was already counted.")

    now = datetime.now(timezone.utc)
    db.add(AttendanceMark(
        attendance_id=attendance.id, marked_at=now, distance_m=distance,
        score=result.score, modality=req.modality, sig_nonce=result.nonce or f"noref-{now.timestamp()}",
    ))
    attendance.marks_count += 1
    attendance.best_score = max(attendance.best_score, result.score)
    attendance.first_marked_at = attendance.first_marked_at or now
    attendance.last_marked_at = now
    attendance.status = (
        AttendanceStatus.present if attendance.marks_count >= s.marks_required
        else AttendanceStatus.partial
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    remaining = max(0, s.marks_required - attendance.marks_count)
    msg = "Attendance complete — you're marked present." if remaining == 0 \
        else f"Mark {attendance.marks_count}/{s.marks_required} recorded. Check in once more to complete."
    return _state_response(attendance, s, distance, result.score, code="ok", message=msg)


# --- small helpers ---
def _get_or_create_attendance(db: Session, session_id: int, student_id: str) -> Attendance:
    a = db.exec(
        select(Attendance).where(Attendance.session_id == session_id, Attendance.student_id == student_id)
    ).first()
    if a is None:
        a = Attendance(session_id=session_id, student_id=student_id)
        db.add(a)
        db.commit()
        db.refresh(a)
    return a


def _marks_now(db: Session, session_id: int, student_id: str) -> int:
    a = db.exec(
        select(Attendance).where(Attendance.session_id == session_id, Attendance.student_id == student_id)
    ).first()
    return a.marks_count if a else 0


def _status_now(db: Session, session_id: int, student_id: str) -> AttendanceStatus:
    a = db.exec(
        select(Attendance).where(Attendance.session_id == session_id, Attendance.student_id == student_id)
    ).first()
    return a.status if a else AttendanceStatus.absent


def _fail(db: Session, s: ClassSession, student_id: str, distance: float, code: str, message: str) -> VerifyResponse:
    return VerifyResponse(
        ok=False, status=_status_now(db, s.id, student_id), marks_count=_marks_now(db, s.id, student_id),
        marks_required=s.marks_required, distance_m=distance, code=code, message=message,
    )


def _state_response(a: Attendance, s: ClassSession, distance: float, score: float, code: str, message: str) -> VerifyResponse:
    return VerifyResponse(
        ok=code in ("ok", "duplicate"), status=a.status, marks_count=a.marks_count,
        marks_required=s.marks_required, distance_m=distance, score=round(score, 4),
        code=code, message=message,
    )
