"""Enrolment state that survives a client (or database) losing its memory.

The biometric service is the real store: it holds the templates. The attendance
DB only *caches* which modalities a student has, so that check-in can enforce
"face is compulsory" without a round trip.

Those two can drift apart — a schema change that adds the column blank, a
restored/rebuilt database, a row written by an older release. When they drift,
a student who is genuinely enrolled is told to enrol again, which is exactly
the failure we cannot afford: enrolment is the expensive, in-person step.

So: whenever the cache says "nothing enrolled", ask the service before
believing it, and write the answer back. The service roster is cached for a
minute because it has no per-user lookup.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlmodel import Session

from . import biometric
from .models import Student

#: The service reports *that* a user has templates, not which modality they
#: came from. Face is the compulsory one and every enrolment flow starts with
#: it, so an un-cached template is adopted as a face enrolment.
ADOPTED_MODALITY = "face"

_ROSTER_TTL_S = 60.0
_roster: tuple[float, frozenset[str]] | None = None


def modalities(student: Student) -> set[str]:
    """Modalities cached on the student row."""
    return {m for m in (student.enrolled_modality or "").split(",") if m}


def _enrolled_ids(*, now: float | None = None) -> frozenset[str]:
    """User ids with a template, cached for `_ROSTER_TTL_S`.

    Raises `biometric.BiometricError` when the service can't be reached and no
    fresh copy is held.
    """
    global _roster
    now = time.monotonic() if now is None else now
    if _roster is not None and now - _roster[0] < _ROSTER_TTL_S:
        return _roster[1]
    ids = frozenset(biometric.list_enrolled_user_ids())
    _roster = (now, ids)
    return ids


def reset_cache() -> None:
    """Drop the roster cache (called after an enrolment, and by tests)."""
    global _roster
    _roster = None


def sync(db: Session, student: Student) -> Student:
    """Re-adopt a service-side enrolment the DB has forgotten.

    A no-op when the row already lists a modality, and a no-op (never an error)
    when the biometric service is unreachable — a temporary outage must not
    push a student back into enrolment.
    """
    if modalities(student):
        return student
    try:
        known = _enrolled_ids()
    except biometric.BiometricError:
        return student
    if student.student_id not in known:
        return student

    student.enrolled_modality = ADOPTED_MODALITY
    student.enrolled_at = student.enrolled_at or datetime.now(timezone.utc)
    db.add(student)
    db.commit()
    db.refresh(student)
    return student
