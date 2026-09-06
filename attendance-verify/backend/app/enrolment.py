"""Enrolment state that survives a client (or database) losing its memory.

The biometric service is the real store: it holds the templates. The attendance
DB only *caches* which modalities a student has, so that check-in can enforce
"face is compulsory" without a round trip.

Those two can drift apart — a schema change that adds the column blank, a
restored/rebuilt database, a row written by an older release. When they drift,
a student who is genuinely enrolled is told to enrol again, which is exactly
the failure we cannot afford: enrolment is the expensive, in-person step.

So: whenever the cache says "nothing enrolled", ask the service before
believing it, and write the answer back. The roster is cached for a minute, and
now carries each person's modalities — `/v1/users` returns them alongside the
ids, so the old follow-up call per student was asking for something already on
the wire.
"""
from __future__ import annotations

import threading
import time

from sqlmodel import Session

from . import biometric
from .models import Student
from .timeutil import now

#: Used only when the service can name a template but not its modality (an older
#: service, answering from a roster without the modality map). Face is compulsory
#: and every enrolment flow starts with it, so that is the safe reading of
#: "enrolled, unspecified".
ADOPTED_MODALITY = "face"

_ROSTER_TTL_S = 60.0
_roster: tuple[float, dict[str, tuple[str, ...]]] | None = None
#: The cache is read by every request thread. Without this, two requests arriving
#: on an expired cache both call the service and one overwrites the other's answer.
_lock = threading.Lock()


def modalities(student: Student) -> set[str]:
    """Modalities cached on the student row."""
    return {m for m in (student.enrolled_modality or "").split(",") if m}


def _cached_roster(*, moment: float | None = None) -> dict[str, tuple[str, ...]]:
    """The service roster, cached for `_ROSTER_TTL_S`.

    Raises `biometric.BiometricError` when the service can't be reached and no
    fresh copy is held.
    """
    global _roster
    moment = time.monotonic() if moment is None else moment
    with _lock:
        if _roster is not None and moment - _roster[0] < _ROSTER_TTL_S:
            return _roster[1]
    fetched = biometric.list_roster()
    with _lock:
        _roster = (moment, fetched)
    return fetched


def reset_cache() -> None:
    """Drop the roster cache (called after an enrolment, and by tests)."""
    global _roster
    with _lock:
        _roster = None


def _service_modalities(student_id: str) -> set[str] | None:
    """Which modalities the service holds for this student. None = it can't say.

    The cached roster answers first: it already carries the modality map, so the
    common case costs nothing. The per-user endpoint is the fallback for someone
    the roster does not list — a student enrolled in the last minute, whose
    absence from a cached copy must not read as "not enrolled".
    """
    try:
        roster = _cached_roster()
    except biometric.BiometricError:
        roster = None

    if roster is not None and student_id in roster:
        return set(roster[student_id]) or {ADOPTED_MODALITY}

    try:
        status = biometric.user_status(student_id)
    except biometric.BiometricError:
        return None
    if status is not None:
        return set(status.modalities) or ({ADOPTED_MODALITY} if status.enrolled else set())
    # The service answered, and it does not hold this person.
    return set() if roster is not None else None


def sync(db: Session, student: Student) -> Student:
    """Re-adopt a service-side enrolment the DB has forgotten.

    A no-op when the row already lists a modality, and a no-op (never an error)
    when the biometric service is unreachable — a temporary outage must not
    push a student back into enrolment.
    """
    if modalities(student):
        return student
    found = _service_modalities(student.student_id)
    if not found:
        return student

    student.enrolled_modality = ",".join(sorted(found))
    student.enrolled_at = student.enrolled_at or now()
    db.add(student)
    db.commit()
    db.refresh(student)
    return student
