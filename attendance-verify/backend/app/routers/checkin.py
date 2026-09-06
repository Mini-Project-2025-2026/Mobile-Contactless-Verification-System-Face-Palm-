"""Check-in: geofence + biometric verify -> attendance mark.

This is the substituted mechanism. Where the original app accepts an
admin-generated PIN, this endpoint requires a live biometric verify whose
HMAC-signed verdict the backend independently validates before recording a mark.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .. import biometric, enrolment, policy
from ..config import settings
from ..db import get_session
from ..geo import within_geofence
from ..models import (
    REQUIRED_PHASES,
    Attendance,
    AttendanceMark,
    AttendanceStatus,
    Enrollment,
    Modality,
    Student,
)
from ..models import (
    Session as ClassSession,
)
from ..schemas import ChallengeRequest, ChallengeResponse, VerifyRequest, VerifyResponse
from ..security import current_student
from ..timeutil import aware_or_now as _aware
from ..timeutil import now

log = logging.getLogger("attendance.checkin")

router = APIRouter(prefix="/api/checkin", tags=["checkin"])


def _load_open_session(db: Session, session_id: int) -> ClassSession:
    s = db.get(ClassSession, session_id)
    moment = now()
    if s is None or not s.active or not (_aware(s.starts_at) <= moment <= _aware(s.ends_at)):
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

    # Check-in must be open for a phase (start/end). "closed" = between/after windows.
    if s.phase not in ("start", "end"):
        return VerifyResponse(
            ok=False, status=_status_now(db, s.id, student.student_id),
            marks_count=_marks_now(db, s.id, student.student_id),
            marks_required=s.marks_required, distance_m=0.0, code="checkin_closed",
            message="Check-in isn't open right now. Wait for your lecturer to open it.",
        )

    # Face is compulsory: no attendance can be marked (by any modality) until a
    # face template exists. Palm alone is not enough. Enforced server-side so it
    # holds even outside the app. Sync first so a forgotten cache never sends an
    # already-enrolled student back to enrolment.
    student = enrolment.sync(db, student)
    if "face" not in (student.enrolled_modality or "").split(","):
        return VerifyResponse(
            ok=False, status=_status_now(db, s.id, student.student_id),
            marks_count=_marks_now(db, s.id, student.student_id),
            marks_required=s.marks_required, distance_m=0.0, code="face_required",
            message="Enrol your face first (required) before you can mark attendance.",
        )

    # 0) GPS quality floor — a fix less accurate than the limit can't be trusted
    #    against a tight geofence (indoor hardening; 0 disables).
    acc = req.gps.accuracy_m
    if settings.max_gps_accuracy_m and acc is not None and acc > settings.max_gps_accuracy_m:
        return VerifyResponse(
            ok=False, status=_status_now(db, s.id, student.student_id),
            marks_count=_marks_now(db, s.id, student.student_id),
            marks_required=s.marks_required, distance_m=0.0, code="low_gps_accuracy",
            message=f"Weak GPS signal (±{acc:.0f} m). Move to an open spot and try again.",
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
            student.student_id, frames=req.frames, token=req.token, image=req.image, modality=req.modality.value
        )
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}") from exc

    # Visible on purpose: every verdict, pass or fail, is one log line naming the
    # actual score and claimed identity the service returned. Without this the
    # only symptom anyone can see is "Not counted" — there was no way, short of
    # reading the biometric service's own logs, to tell a genuine low-confidence
    # capture apart from a wrong-identity template or a signature problem.
    log.info(
        "verify %s modality=%s success=%s claimed_user=%s returned_user=%s score=%.4f sig_valid=%s",
        student.student_id, req.modality.value, result.success, student.student_id,
        result.user_id, result.score, result.signature_valid,
    )

    # 3) Trust gates: verdict must be granted, signature valid, identity + score right.
    if not result.signature_valid:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "bad_signature")
    if not result.success or result.user_id != student.student_id:
        return _fail(db, s, student.student_id, distance, "biometric_mismatch",
                     "Face/palm did not match your enrolled record.", score=result.score)
    floor = policy.effective_score_floor()
    if result.score < floor.min_score:
        return _fail(db, s, student.student_id, distance, "low_confidence",
                     "Capture quality too low. Try again in better light.", score=result.score)

    # 4) Record the mark (idempotent on the signature nonce → blocks replay).
    outcome = record_mark(db, s, student, distance=distance, score=result.score,
                          nonce=result.nonce, modality=req.modality)
    return VerifyResponse(
        ok=outcome.ok, status=outcome.status, marks_count=outcome.marks_count,
        marks_required=s.marks_required, distance_m=distance,
        score=round(result.score, 4), code=outcome.code, message=outcome.message,
    )


@dataclass(frozen=True)
class MarkOutcome:
    """What happened when a verified capture was written down."""
    ok: bool
    code: str          # ok | duplicate | already_marked
    message: str
    status: AttendanceStatus
    marks_count: int


def record_mark(db: Session, session: ClassSession, student: Student, *,
                distance: float, score: float, nonce: str,
                modality: Modality) -> MarkOutcome:
    """Write down one verified capture, and say what it changed.

    Shared by the two ways a mark can be made — a student on their own phone,
    and a shared kiosk that identified them — because the rules must not differ
    by route. One mark per window, a signed verdict counted once, present only
    when both windows are complete.

    Everything above this point is about establishing WHO and WHERE; by the time
    a caller reaches here, that is settled and this is only bookkeeping.
    """
    attendance = _get_or_create_attendance(db, session.id, student.student_id)

    if nonce and db.exec(
        select(AttendanceMark).where(AttendanceMark.sig_nonce == nonce)
    ).first():
        # Same signed verdict submitted twice — report state, don't double-count.
        return _outcome(attendance, "duplicate", "This capture was already counted.")

    # One mark per phase: marking the same window twice does not complete attendance.
    existing_phases = {m.phase for m in db.exec(
        select(AttendanceMark).where(AttendanceMark.attendance_id == attendance.id)).all()}
    if session.phase in existing_phases:
        note = ("Start already marked. Come back when the END check-in opens."
                if session.phase == "start"
                else "The end check-in for this class is already complete.")
        return _outcome(attendance, "already_marked", note)

    moment = now()
    db.add(AttendanceMark(
        attendance_id=attendance.id, marked_at=moment, distance_m=distance, score=score,
        modality=modality, phase=session.phase,
        sig_nonce=nonce or f"noref-{moment.timestamp()}",
    ))
    phases = existing_phases | {session.phase}
    attendance.marks_count = len(phases)
    attendance.best_score = max(attendance.best_score, score)
    attendance.first_marked_at = attendance.first_marked_at or moment
    attendance.last_marked_at = moment
    attendance.status = (
        AttendanceStatus.present if phases >= REQUIRED_PHASES else AttendanceStatus.partial
    )
    db.add(attendance)
    try:
        db.commit()
    except IntegrityError:
        # The nonce is unique in the database, so a verdict submitted twice fast
        # enough to clear the check above lands here instead of being counted
        # twice. The mark is already recorded by the request that won.
        db.rollback()
        log.info("duplicate verdict for %s on session %s (nonce %s)",
                 student.student_id, session.id, nonce)
        current = _get_or_create_attendance(db, session.id, student.student_id)
        return _outcome(current, "duplicate", "This capture was already counted.")
    db.refresh(attendance)

    if attendance.status == AttendanceStatus.present:
        message = "Attendance complete. Present (marked at both start and end)."
    elif session.phase == "start":
        message = "Start check-in recorded ✓. Come back for the END check-in."
    else:
        message = "End check-in recorded ✓, but no start mark was found. Attendance is partial."
    return _outcome(attendance, "ok", message)


def _outcome(attendance: Attendance, code: str, message: str) -> MarkOutcome:
    return MarkOutcome(ok=True, code=code, message=message,
                       status=attendance.status, marks_count=attendance.marks_count)


# --- small helpers ---
def _find_attendance(db: Session, session_id: int, student_id: str) -> Attendance | None:
    return db.exec(select(Attendance).where(
        Attendance.session_id == session_id, Attendance.student_id == student_id)).first()


def _get_or_create_attendance(db: Session, session_id: int, student_id: str) -> Attendance:
    """The student's row for this class, created if this is their first mark.

    Two check-ins arriving together both find nothing and both insert. The
    database refuses the second (one row per session and student), which is the
    point of the constraint — so the loser reads the winner's row instead of
    turning a successful verification into a 500.
    """
    existing = _find_attendance(db, session_id, student_id)
    if existing is not None:
        return existing
    record = Attendance(session_id=session_id, student_id=student_id)
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        won = _find_attendance(db, session_id, student_id)
        if won is None:  # refused for some other reason; let it surface
            raise
        return won
    db.refresh(record)
    return record


def _marks_now(db: Session, session_id: int, student_id: str) -> int:
    a = _find_attendance(db, session_id, student_id)
    return a.marks_count if a else 0


def _status_now(db: Session, session_id: int, student_id: str) -> AttendanceStatus:
    a = _find_attendance(db, session_id, student_id)
    return a.status if a else AttendanceStatus.absent


def _fail(db: Session, s: ClassSession, student_id: str, distance: float, code: str, message: str,
         score: float = 0.0) -> VerifyResponse:
    return VerifyResponse(
        ok=False, status=_status_now(db, s.id, student_id), marks_count=_marks_now(db, s.id, student_id),
        marks_required=s.marks_required, distance_m=distance, score=round(score, 4), code=code, message=message,
    )


def _state_response(a: Attendance, s: ClassSession, distance: float, score: float, code: str, message: str) -> VerifyResponse:
    return VerifyResponse(
        ok=code in ("ok", "duplicate", "already_marked"), status=a.status, marks_count=a.marks_count,
        marks_required=s.marks_required, distance_m=distance, score=round(score, 4),
        code=code, message=message,
    )
