"""Student login with one-device binding."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import Device, Student
from ..schemas import LoginRequest, TokenResponse
from ..security import create_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_session)) -> TokenResponse:
    student = db.exec(select(Student).where(Student.student_id == req.student_id)).first()
    if not student or not verify_password(req.password, student.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid student id or password")

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
        device.active = True
        device.platform = req.platform
        device.name = req.device_name
    device.last_seen = datetime.now(timezone.utc)
    db.add(device)
    db.commit()

    return TokenResponse(
        access_token=create_token(student.student_id, req.device_uid),
        student_id=student.student_id,
        name=student.name,
    )
