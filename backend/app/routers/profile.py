"""Student profile (the gradient ID card + reference)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from .. import enrolment
from ..db import get_session
from ..models import Student
from ..schemas import Profile
from ..security import current_student

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=Profile)
def me(
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> Profile:
    student = enrolment.sync(db, student)
    return Profile(
        student_id=student.student_id,
        name=student.name,
        reference_no=student.reference_no,
        programme=student.programme,
        year_group=student.year_group,
        class_group=student.class_group,
        semester=student.semester,
        enrolled="face" in (student.enrolled_modality or "").split(","),
    )
