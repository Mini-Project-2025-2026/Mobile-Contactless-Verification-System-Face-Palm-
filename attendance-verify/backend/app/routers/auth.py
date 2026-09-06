"""Student login with one-device binding."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from .. import guard
from ..config import settings
from ..db import get_session
from ..models import Device, ProgrammeCredential, Student, norm_programme
from ..schemas import LoginRequest, TokenResponse
from ..security import create_token, verify_password
from ..timeutil import now

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _password_ok(db: Session, student: Student, raw: str) -> bool:
    """The programme's shared password, or a private one where the student has it."""
    if student.programme:
        cred = db.get(ProgrammeCredential, norm_programme(student.programme))
        if cred and verify_password(raw, cred.password_hash):
            return True
    return verify_password(raw, student.password_hash)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_session)) -> TokenResponse:
    # A programme password is shared by a cohort and read out in a lecture hall.
    # Guessing it is the cheapest attack on this system, so guessing is bounded.
    attempt = guard.before_student_login(request, req.student_id)

    student = db.exec(select(Student).where(Student.student_id == req.student_id)).first()
    if not student or not student.active or not _password_ok(db, student, req.password):
        guard.after_student_login(attempt, ok=False)
        # One message for "no such student" and for "wrong password": telling
        # them apart turns this endpoint into a roll of who is registered.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid student id or password")
    guard.after_student_login(attempt, ok=True)

    active = db.exec(
        select(Device).where(Device.student_id == student.student_id, Device.active == True)  # noqa: E712
    ).first()

    if settings.enforce_login_device and active and active.device_uid != req.device_uid:
        # Hard one-device policy (opt-in). Default is relaxed — biometric verify
        # prevents proxy at check-in, and enrolment is protected separately.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "device_conflict: another device is registered. Request a device change.",
        )

    device = db.exec(select(Device).where(Device.device_uid == req.device_uid)).first()
    if device is None:
        device = Device(
            student_id=student.student_id,
            device_uid=req.device_uid,
            platform=req.platform,
            name=req.device_name,
        )
    else:
        # A device is shared on purpose: a classroom phone gets handed around so
        # students without one can still mark. It belongs to whoever signed in
        # last, otherwise the registry keeps naming the first student forever.
        device.student_id = student.student_id
        device.active = True
        device.platform = req.platform
        device.name = req.device_name
    device.last_seen = now()
    db.add(device)
    db.commit()

    return TokenResponse(
        access_token=create_token(student.student_id, req.device_uid),
        student_id=student.student_id,
        name=student.name,
    )
