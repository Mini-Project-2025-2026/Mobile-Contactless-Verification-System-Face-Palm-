"""SQLModel tables for the attendance domain.

Kept deliberately small and explicit. `Student.student_id` doubles as the
`user_id` presented to the Biometric Verify API, so a verify verdict for a
student maps 1:1 onto their enrolled template.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AttendanceStatus(str, Enum):
    absent = "absent"
    partial = "partial"
    present = "present"


class Modality(str, Enum):
    face = "face"
    palm = "palm"


class Student(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    student_id: str = Field(index=True, unique=True)  # == biometric user_id
    name: str
    password_hash: str
    semester: str = "2025/2026-1"
    reference_no: str = ""
    programme: str = ""
    year_group: str = ""
    class_group: str = ""
    enrolled_at: datetime | None = None
    enrolled_modality: str = ""
    enrolled_samples: int = 0
    enroll_device_uid: str = ""  # device bound at first enrolment
    created_at: datetime = Field(default_factory=_now)


def norm_programme(name: str) -> str:
    """Programmes are typed by hand in the console, so match them loosely."""
    return " ".join(name.split()).casefold()


class ProgrammeCredential(SQLModel, table=True):
    """One sign-in password shared by everyone on a programme.

    Students identify themselves with their own (unique) student ID; this is the
    shared half of the credential, so a stranger needs both. It is deliberately
    NOT what protects attendance: a shared password is known to every classmate,
    so enrolment is gated separately (see `settings.enroll_requires_grant`).
    """
    programme: str = Field(primary_key=True)  # normalised via norm_programme
    password_hash: str
    updated_at: datetime = Field(default_factory=_now)


class Device(SQLModel, table=True):
    """One-device binding. A student's active device is the only one allowed to check in."""
    id: int | None = Field(default=None, primary_key=True)
    student_id: str = Field(index=True)
    device_uid: str = Field(index=True, unique=True)
    platform: str = "unknown"
    name: str = "device"
    active: bool = True
    last_seen: datetime = Field(default_factory=_now)
    created_at: datetime = Field(default_factory=_now)


class Course(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    title: str
    semester: str = "2025/2026-1"
    lecturer_name: str = ""


class Enrollment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    student_id: str = Field(index=True)
    course_id: int = Field(index=True)


class Session(SQLModel, table=True):
    """A geofenced attendance window for a course."""
    id: int | None = Field(default=None, primary_key=True)
    course_id: int = Field(index=True)
    title: str = ""
    lat: float
    lng: float
    radius_m: float = 70.0
    starts_at: datetime
    ends_at: datetime
    marks_required: int = 2
    # Two-phase attendance: "start" window open at class start, admin opens "end"
    # near class end; "closed" = no check-in accepted. Present needs both phases.
    phase: str = "start"
    active: bool = True
    created_at: datetime = Field(default_factory=_now)


class Attendance(SQLModel, table=True):
    """One row per (session, student). Updated as marks accumulate."""
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    student_id: str = Field(index=True)
    status: AttendanceStatus = AttendanceStatus.absent
    marks_count: int = 0
    best_score: float = 0.0
    first_marked_at: datetime | None = None
    last_marked_at: datetime | None = None


class EnrollGrant(SQLModel, table=True):
    """Admin-issued one-time token that permits a re-enrolment or an enrolment
    from a new device. Single-use, expiring."""
    id: int | None = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True)
    student_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime
    used_at: datetime | None = None


class AttendanceMark(SQLModel, table=True):
    """Append-only log of individual verified marks (audit + replay guard)."""
    id: int | None = Field(default=None, primary_key=True)
    attendance_id: int = Field(index=True)
    marked_at: datetime = Field(default_factory=_now)
    distance_m: float = 0.0
    score: float = 0.0
    modality: Modality = Modality.face
    phase: str = "start"  # which window this mark belongs to: "start" | "end"
    sig_nonce: str = Field(index=True)  # from the verify signature; blocks replay
