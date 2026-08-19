"""Consent: the lawful basis for holding a student's face.

Biometric data is special-category data almost everywhere. This app was
capturing faces and storing templates with no record that the student ever
agreed, nothing to show anyone who asked, and no way for the student to change
their mind. The verification service has carried all three since before we
integrated with it — a versioned consent statement, a receipt pinned to the
exact text agreed, immediate enforcement of a withdrawal at verify time — and
this app simply never used any of it.

These endpoints put that in front of the student:

  * read the statement before enrolling,
  * agree to it in the app (which the service records as `self`, the strongest
    form, rather than an administrator ticking a box on their behalf),
  * see their own receipt, and
  * withdraw, after which verification stops immediately and their data is due
    for erasure.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from .. import biometric
from ..db import get_session
from ..models import Student
from ..schemas import ConsentReceiptOut, ConsentStatementOut
from ..security import current_student

log = logging.getLogger("attendance.consent")

router = APIRouter(prefix="/api/consent", tags=["consent"])


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}")


@router.get("/statement", response_model=ConsentStatementOut)
def statement(_: Student = Depends(current_student)) -> ConsentStatementOut:
    """The exact wording this campus asks students to agree to."""
    try:
        policy = biometric.consent_policy()
    except biometric.BiometricError as exc:
        raise _unavailable(exc) from exc
    return ConsentStatementOut(
        text=policy.text, version=policy.version, text_sha256=policy.text_sha256,
        required_before_enrolment=policy.require_consent,
        withdrawal_stops_verification=policy.enforce_withdrawal,
    )


@router.get("", response_model=ConsentReceiptOut)
def my_consent(student: Student = Depends(current_student)) -> ConsentReceiptOut:
    """This student's own consent standing."""
    try:
        receipt = biometric.consent_receipt(student.student_id)
    except biometric.BiometricError as exc:
        raise _unavailable(exc) from exc
    if receipt is None:
        return ConsentReceiptOut(recorded=False, status="none")
    return ConsentReceiptOut(
        recorded=True, status=receipt.status, granted_at=receipt.granted_at,
        method=receipt.method, version=receipt.version,
        text_sha256=receipt.text_sha256, withdrawn_at=receipt.withdrawn_at,
    )


@router.post("", response_model=ConsentReceiptOut, status_code=201)
def give_consent(student: Student = Depends(current_student)) -> ConsentReceiptOut:
    """Record that this student agreed, in the app, themselves."""
    try:
        receipt = biometric.record_consent(student.student_id, method="self")
    except biometric.BiometricError as exc:
        raise _unavailable(exc) from exc
    log.info("consent recorded for %s", student.student_id,
             extra={"student_id": student.student_id})
    return ConsentReceiptOut(
        recorded=True, status="granted", granted_at=receipt.granted_at,
        method=receipt.method, version=receipt.version, text_sha256=receipt.text_sha256,
    )


@router.post("/withdraw")
def withdraw(
    student: Student = Depends(current_student),
    db: Session = Depends(get_session),
) -> dict:
    """Withdraw consent. Verification stops immediately.

    The attendance already recorded stays: it is an academic record of classes
    the student attended, not biometric data. What stops is any further use of
    their face — which also means they cannot mark attendance again until they
    enrol afresh.
    """
    try:
        withdrawn = biometric.withdraw_consent(student.student_id)
    except biometric.BiometricError as exc:
        raise _unavailable(exc) from exc
    if not withdrawn:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "no_consent_record: There is nothing on record to withdraw.")

    # Our cached copy of "this student is enrolled" would otherwise keep letting
    # them past the face-required gate, to a verify the service now refuses.
    student.enrolled_modality = ""
    db.add(student)
    db.commit()
    log.warning("consent withdrawn by %s", student.student_id,
                extra={"student_id": student.student_id})
    return {
        "student_id": student.student_id,
        "status": "withdrawn",
        "message": ("Your consent is withdrawn and your biometric data is due for erasure. "
                    "Attendance already recorded is unaffected. You will need to enrol "
                    "again before you can mark attendance."),
    }


@router.get("/my-data")
def my_data(student: Student = Depends(current_student)) -> dict:
    """Everything held about this student, for them to read or keep.

    The biometric side is metadata only — how many samples, which modalities,
    when — never the template, which stays encrypted at rest and is not
    meaningful outside the service anyway.
    """
    try:
        held = biometric.export_user_record(student.student_id)
    except biometric.BiometricError as exc:
        raise _unavailable(exc) from exc
    return {
        "student": {
            "student_id": student.student_id, "name": student.name,
            "reference_no": student.reference_no, "programme": student.programme,
            "year_group": student.year_group, "class_group": student.class_group,
            "semester": student.semester,
            "enrolled_modalities": [m for m in (student.enrolled_modality or "").split(",") if m],
        },
        "biometric": held or {"enrolled": False},
    }
