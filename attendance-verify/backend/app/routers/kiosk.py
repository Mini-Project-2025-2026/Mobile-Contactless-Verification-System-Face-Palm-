"""Kiosk check-in: one shared device, and nobody types anything.

The problem this solves is already in this repository. `test_shared_device.py`
exists because a classroom phone gets handed around so students without one can
still mark — and the way that worked was: sign out, sign in as the next student
with the programme password everybody knows, mark, hand it on. Every step of
that is a credential being read aloud in a lecture hall, and the device registry
ends up naming whoever touched it last.

The verification service has answered this since before we integrated with it.
`POST /v1/identify` is 1:N: hand it a capture, it says who. It applies its own
`identify_margin` first — the winner must beat the runner-up by that much — which
is the guard that stops a lookalike coming back as a confident match, and it
signs the verdict exactly as a 1:1 verify is signed.

So: a lecturer opens a class and mints a kiosk token for it. The phone at the
door holds that token, and nothing else. A student walks up, looks at it, and is
marked. No student id typed, no shared password spoken, no session for anyone to
forget to sign out of.

What the token can do is deliberately tiny: mark attendance, for one class, for
as long as that class runs. It cannot read a roster, enrol anyone, or outlive
the lecture.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from .. import biometric, policy
from ..db import get_session
from ..geo import within_geofence
from ..models import Enrollment, Student
from ..models import Session as ClassSession
from ..schemas import KioskChallengeOut, KioskVerifyIn, KioskVerifyOut
from ..security import current_kiosk
from . import checkin

log = logging.getLogger("attendance.kiosk")

router = APIRouter(prefix="/api/kiosk", tags=["kiosk"])


def _open_session(db: Session, session_id: int) -> ClassSession:
    return checkin._load_open_session(db, session_id)


@router.post("/challenge", response_model=KioskChallengeOut)
def challenge(
    session_id: int = Depends(current_kiosk),
    db: Session = Depends(get_session),
) -> KioskChallengeOut:
    """A liveness challenge for whoever is standing in front of the device."""
    session = _open_session(db, session_id)
    try:
        issued = biometric.get_challenge()
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc
    return KioskChallengeOut(token=issued.token, instruction=issued.instruction,
                             active=issued.active, phase=session.phase)


@router.post("/verify", response_model=KioskVerifyOut)
def verify(
    body: KioskVerifyIn,
    session_id: int = Depends(current_kiosk),
    db: Session = Depends(get_session),
) -> KioskVerifyOut:
    """Identify whoever is at the device, and mark them present for this class."""
    session = _open_session(db, session_id)

    if session.phase not in ("start", "end"):
        return KioskVerifyOut(ok=False, code="checkin_closed",
                              message="Check-in is not open for this class right now.")

    # The kiosk is the thing standing in the room, so its position is what the
    # geofence is checked against — not a position the student's phone reports.
    # That is the whole security advantage of a fixed device: the location claim
    # comes from equipment the lecturer controls.
    in_range, distance = within_geofence(body.gps.lat, body.gps.lng,
                                         session.lat, session.lng, session.radius_m)
    distance = round(distance, 1)
    if not in_range:
        log.warning("kiosk for session %s is %.0fm outside its own geofence",
                    session_id, distance)
        return KioskVerifyOut(
            ok=False, code="kiosk_out_of_place", distance_m=distance,
            message=("This device is not in the classroom it was issued for. "
                     "Ask your lecturer to set it up again."))

    try:
        verdict = biometric.identify_person(frames=body.frames, token=body.token,
                                            image=body.image, modality=body.modality.value)
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc

    if not verdict.signature_valid:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "bad_signature")
    if not verdict.success or not verdict.user_id:
        return KioskVerifyOut(ok=False, code="not_recognised", distance_m=distance,
                              message="Not recognised. Step closer, face the camera, and try again.")

    floor = policy.effective_score_floor()
    if verdict.score < floor.min_score:
        return KioskVerifyOut(ok=False, code="low_confidence", distance_m=distance,
                              message="Capture quality too low. Try again in better light.")

    student = db.exec(select(Student).where(Student.student_id == verdict.user_id)).first()
    if student is None or not student.active:
        # The service knows this face; this campus does not. Say so plainly
        # rather than pretending the capture failed.
        log.warning("kiosk identified %s, who is not a student here", verdict.user_id)
        return KioskVerifyOut(ok=False, code="unknown_student", distance_m=distance,
                              message="You were recognised, but you are not registered here.")

    enrolled = db.exec(select(Enrollment).where(
        Enrollment.student_id == student.student_id,
        Enrollment.course_id == session.course_id)).first()
    if enrolled is None:
        return KioskVerifyOut(
            ok=False, code="not_enrolled", student_id=student.student_id,
            name=student.name, distance_m=distance,
            message=f"{student.name}, you are not registered for this course.")

    outcome = checkin.record_mark(db, session, student, distance=distance,
                                  score=verdict.score, nonce=verdict.nonce,
                                  modality=body.modality)
    log.info("kiosk marked %s for session %s (%s)",
             student.student_id, session_id, outcome.code,
             extra={"student_id": student.student_id})
    return KioskVerifyOut(
        ok=outcome.ok, code=outcome.code, message=f"{student.name}: {outcome.message}",
        student_id=student.student_id, name=student.name,
        status=outcome.status, marks_count=outcome.marks_count,
        distance_m=distance, score=round(verdict.score, 4),
    )
