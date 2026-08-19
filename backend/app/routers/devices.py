"""Devices tab: list the student's registered devices (one-device policy view)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..db import get_session
from ..models import Device, Student
from ..schemas import DeviceItem
from ..security import current_student
from ..timeutil import aware_or_now, now

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=list[DeviceItem])
def list_devices(
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> list[DeviceItem]:
    rows = db.exec(select(Device).where(Device.student_id == student.student_id)).all()
    rows.sort(key=lambda d: d.last_seen, reverse=True)
    return [
        DeviceItem(
            device_uid=d.device_uid,
            platform=d.platform,
            name=d.name,
            active=d.active,
            last_seen=aware_or_now(d.last_seen),
        )
        for d in rows
    ]


@router.post("/deregister")
def deregister(
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> dict:
    """Release the student's active device binding so a new phone can be paired
    on next login (the app's 'device change request', simplified for phase 1)."""
    active = db.exec(
        select(Device).where(Device.student_id == student.student_id, Device.active == True)  # noqa: E712
    ).all()
    if not active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active device")
    for d in active:
        d.active = False
        d.last_seen = now()
        db.add(d)
    db.commit()
    return {"released": len(active)}
