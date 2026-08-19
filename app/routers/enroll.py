"""In-app biometric enrolment: a student registers their own face/palm.

Policy (enforced here, not in the biometric service):
  * **Face is compulsory, palm optional** — `can_mark` is true only once a face
    template exists. If both exist, either can be presented at check-in.
  * **First enrolment binds the device.** Adding a not-yet-enrolled modality
    (e.g. optional palm) from that same device is free.
  * **Re-enrolment (redoing an existing modality) or enrolling from a different
    device requires an admin-issued one-time grant token** (single-use, expiring),
    which then re-binds the enrolment device.

Enrolled modalities live in `enrolled_modality` as a comma-joined set.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from .. import biometric, enrolment
from ..db import get_session
from ..models import EnrollGrant, Modality, Student
from ..schemas import EnrollRequest, EnrollResponse, EnrollStatus
from ..security import current_device_uid, current_student

router = APIRouter(prefix="/api/enroll", tags=["enroll"])


def _modalities(student: Student) -> set[str]:
    return enrolment.modalities(student)


def _valid_grant(db: Session, student_id: str, token: str) -> EnrollGrant | None:
    if not token:
        return None
    g = db.exec(select(EnrollGrant).where(EnrollGrant.token == token)).first()
    if not g or g.student_id != student_id or g.used_at is not None:
        return None
    exp = g.expires_at if g.expires_at.tzinfo else g.expires_at.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        return None
    return g


@router.get("/status", response_model=EnrollStatus)
def enroll_status(
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> EnrollStatus:
    # A student whose template still lives in the biometric service is enrolled,
    # whatever this database (or the phone's storage) remembers.
    student = enrolment.sync(db, student)
    mods = _modalities(student)
    face = "face" in mods
    return EnrollStatus(face_enrolled=face, palm_enrolled="palm" in mods, can_mark=face)


@router.post("", response_model=EnrollResponse)
def enroll(
    req: EnrollRequest,
    student: Student = Depends(current_student),
    device_uid: str = Depends(current_device_uid),
    db: Session = Depends(get_session),
) -> EnrollResponse:
    if not req.images:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no images provided")

    # Sync first: an existing template must count as "already enrolled" here too,
    # or a forgotten cache would hand out a grant-free re-enrolment.
    student = enrolment.sync(db, student)
    mods = _modalities(student)
    first_ever = not mods
    has_mod = req.modality.value in mods
    same_device = bool(student.enroll_device_uid) and device_uid == student.enroll_device_uid

    # Decide whether an admin grant is needed.
    if first_ever or (same_device and not has_mod):
        need_grant, grant = False, None
    else:
        grant = _valid_grant(db, student.student_id, req.grant_token)
        need_grant = True
        if grant is None:
            reason = ("Re-enrolling your " + req.modality.value) if has_mod else "Enrolling from a new device"
            return EnrollResponse(
                ok=False, enrolled=0, of=len(req.images), samples=0, modality=req.modality,
                code="grant_required",
                message=f"{reason} needs a one-time code from your admin.",
            )

    try:
        result = biometric.enroll_user(student.student_id, req.images, source=req.source)
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}") from exc

    if result.enrolled <= 0:
        return EnrollResponse(
            ok=False, enrolled=0, of=result.of, samples=result.samples, modality=req.modality,
            code="no_biometric",
            message=f"No usable {req.modality.value} detected — retake in good lighting, filling the frame.",
        )

    # Persist: modalities, first-enrolment timestamp, device binding, grant use.
    mods.add(req.modality.value)
    student.enrolled_modality = ",".join(sorted(mods))
    student.enrolled_at = student.enrolled_at or datetime.now(timezone.utc)
    student.enrolled_samples = max(student.enrolled_samples, result.samples)
    if first_ever or need_grant:
        student.enroll_device_uid = device_uid  # bind / re-bind
    if grant is not None:
        grant.used_at = datetime.now(timezone.utc)
        db.add(grant)
    db.add(student)
    db.commit()
    enrolment.reset_cache()  # the roster just changed

    face_done = "face" in mods
    if req.modality == Modality.face:
        msg = "Face enrolled — you can now mark attendance. Adding your palm is optional."
    else:
        msg = ("Palm enrolled — you can now use face or palm to check in." if face_done
               else "Palm enrolled. Face is still required before you can mark attendance.")
    return EnrollResponse(
        ok=True, enrolled=result.enrolled, of=result.of, samples=result.samples,
        modality=req.modality, message=msg, code="ok",
    )
