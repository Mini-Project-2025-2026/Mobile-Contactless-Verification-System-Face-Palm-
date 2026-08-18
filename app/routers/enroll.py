"""In-app biometric enrolment: a student registers their own face/palm.

Policy (enforced here, not in the biometric service): **face is compulsory,
palm is optional**. `can_mark` becomes true only once a face template exists.
If both are enrolled, either can be presented at check-in — the biometric
service auto-detects the modality and matches the right template.

Enrolled modalities are tracked in the existing `enrolled_modality` column as a
comma-joined set (e.g. "face" or "face,palm") — no schema change required.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from .. import biometric
from ..db import get_session
from ..models import Modality, Student
from ..schemas import EnrollRequest, EnrollResponse, EnrollStatus
from ..security import current_student

router = APIRouter(prefix="/api/enroll", tags=["enroll"])


def _modalities(student: Student) -> set[str]:
    return {m for m in (student.enrolled_modality or "").split(",") if m}


@router.get("/status", response_model=EnrollStatus)
def enroll_status(student: Student = Depends(current_student)) -> EnrollStatus:
    mods = _modalities(student)
    face = "face" in mods
    return EnrollStatus(face_enrolled=face, palm_enrolled="palm" in mods, can_mark=face)


@router.post("", response_model=EnrollResponse)
def enroll(
    req: EnrollRequest,
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> EnrollResponse:
    if not req.images:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no images provided")

    try:
        result = biometric.enroll_user(student.student_id, req.images, source=req.source)
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}") from exc

    if result.enrolled <= 0:
        return EnrollResponse(
            ok=False, enrolled=0, of=result.of, samples=result.samples, modality=req.modality,
            message=f"No usable {req.modality.value} detected — retake in good lighting, filling the frame.",
        )

    mods = _modalities(student)
    mods.add(req.modality.value)
    student.enrolled_modality = ",".join(sorted(mods))
    student.enrolled_at = student.enrolled_at or datetime.now(timezone.utc)
    student.enrolled_samples = max(student.enrolled_samples, result.samples)
    db.add(student)
    db.commit()

    face_done = "face" in mods
    if req.modality == Modality.face:
        msg = "Face enrolled — you can now mark attendance. Adding your palm is optional."
    else:
        msg = ("Palm enrolled — you can now use face or palm to check in." if face_done
               else "Palm enrolled. Face is still required before you can mark attendance.")
    return EnrollResponse(
        ok=True, enrolled=result.enrolled, of=result.of, samples=result.samples,
        modality=req.modality, message=msg,
    )
