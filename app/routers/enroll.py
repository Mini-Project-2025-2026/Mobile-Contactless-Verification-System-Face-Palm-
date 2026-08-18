"""In-app biometric enrolment: a student registers their own face/palm.

The mobile app captures a few samples and POSTs them here; the backend enrols
them into the Biometric Verify tenant under user_id = studentID, then records
that the student is enrolled so check-in can require it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from .. import biometric
from ..db import get_session
from ..models import Student
from ..schemas import EnrollRequest, EnrollResponse, EnrollStatus
from ..security import current_student

router = APIRouter(prefix="/api/enroll", tags=["enroll"])


@router.get("/status", response_model=EnrollStatus)
def enroll_status(student: Student = Depends(current_student)) -> EnrollStatus:
    return EnrollStatus(
        enrolled=student.enrolled_at is not None,
        samples=student.enrolled_samples,
        modality=student.enrolled_modality,
    )


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
        # Nothing usable in the frames (no face/palm detected, poor quality, etc.)
        return EnrollResponse(
            ok=False, enrolled=0, of=result.of, samples=result.samples, modality=req.modality,
            message="No usable face/palm detected — retake in good lighting, filling the frame.",
        )

    student.enrolled_at = datetime.now(timezone.utc)
    student.enrolled_modality = req.modality.value
    student.enrolled_samples = max(student.enrolled_samples, result.samples)
    db.add(student)
    db.commit()

    return EnrollResponse(
        ok=True, enrolled=result.enrolled, of=result.of, samples=result.samples, modality=req.modality,
        message=f"Enrolled {result.enrolled} of {result.of} samples. You can now mark attendance.",
    )
