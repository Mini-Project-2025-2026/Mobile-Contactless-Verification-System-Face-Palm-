"""Student profile (the gradient ID card + reference)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models import Student
from ..schemas import Profile
from ..security import current_student

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=Profile)
def me(student: Student = Depends(current_student)) -> Profile:
    return Profile(
        student_id=student.student_id,
        name=student.name,
        reference_no=student.reference_no,
        programme=student.programme,
        year_group=student.year_group,
        class_group=student.class_group,
        semester=student.semester,
        enrolled=student.enrolled_at is not None,
    )
