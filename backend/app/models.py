"""SQLModel tables for the attendance domain.

Kept deliberately small and explicit. `Student.student_id` doubles as the
`user_id` presented to the Biometric Verify API, so a verify verdict for a
student maps 1:1 onto their enrolled template.

Where a rule matters it is a database constraint, not a convention: one
attendance row per (session, student), one mark per signed verdict, one
enrolment row per (student, course). Application code can lose a race with
itself; a unique index cannot.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import UniqueConstraint, event
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class AttendanceStatus(str, Enum):
    absent = "absent"
    partial = "partial"
    present = "present"


class Modality(str, Enum):
    face = "face"
    palm = "palm"


class Phase(str, Enum):
    """Which check-in window a session is currently accepting."""
    start = "start"
    end = "end"
    closed = "closed"


#: The windows a student must complete to be counted present.
REQUIRED_PHASES = frozenset({Phase.start.value, Phase.end.value})


def norm_programme(name: str) -> str:
    """Programmes are typed by hand in the console, so match them loosely."""
    return " ".join(name.split()).casefold()


class Student(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    student_id: str = Field(index=True, unique=True)  # == biometric user_id
    name: str
    password_hash: str
    semester: str = "2025/2026-1"
    reference_no: str = ""
    programme: str = ""
    #: `programme` as `norm_programme` sees it. Stored so a cohort can be found
    #: with an indexed lookup instead of normalising every row in Python.
    programme_key: str = Field(default="", index=True)
    year_group: str = Field(default="", index=True)
    class_group: str = ""
    enrolled_at: datetime | None = None
    enrolled_modality: str = ""
    enrolled_samples: int = 0
    enroll_device_uid: str = ""  # device bound at first enrolment
    active: bool = True  # a withdrawn student keeps their record but cannot sign in
    created_at: datetime = Field(default_factory=_now)


@event.listens_for(Student, "before_insert")
@event.listens_for(Student, "before_update")
def _keep_programme_key_in_step(_mapper, _connection, student: Student) -> None:
    """Derive `programme_key` from `programme` on every write.

    A normalised copy that call sites are trusted to maintain is a normalised
    copy that goes stale the first time someone writes a Student row from a
    script, a seed, or a test — and a stale key means a cohort lookup silently
    returns nobody. Deriving it here makes forgetting impossible.
    """
    student.programme_key = norm_programme(student.programme or "")


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
    semester: str = Field(default="2025/2026-1", index=True)
    lecturer_name: str = ""
    archived: bool = False  # a finished course stays readable but stops appearing


class Enrollment(SQLModel, table=True):
    """A student is on a course. One row, whatever the console is clicked twice."""
    __table_args__ = (UniqueConstraint("student_id", "course_id", name="uq_enrollment_student_course"),)

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
    starts_at: datetime = Field(index=True)
    ends_at: datetime = Field(index=True)
    marks_required: int = 2
    # Two-phase attendance: "start" window open at class start, admin opens "end"
    # near class end; "closed" = no check-in accepted. Present needs both phases.
    phase: str = "start"
    active: bool = True
    created_at: datetime = Field(default_factory=_now)


class Attendance(SQLModel, table=True):
    """One row per (session, student). Updated as marks accumulate."""
    __table_args__ = (UniqueConstraint("session_id", "student_id", name="uq_attendance_session_student"),)

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
    """Append-only log of individual verified marks (audit + replay guard).

    `sig_nonce` is unique in the database, not merely checked before insert: the
    replay guard is the whole reason this table exists, and a check-then-insert
    loses to a second request that arrives in the gap between the two.
    """
    id: int | None = Field(default=None, primary_key=True)
    attendance_id: int = Field(index=True)
    marked_at: datetime = Field(default_factory=_now)
    distance_m: float = 0.0
    score: float = 0.0
    modality: Modality = Modality.face
    phase: str = "start"  # which window this mark belongs to: "start" | "end"
    sig_nonce: str = Field(index=True, unique=True)  # from the verify signature; blocks replay


class AuditLog(SQLModel, table=True):
    """Who changed what, from where.

    Every admin action here alters an academic record — a session extended, a
    grant issued, a student's enrolment cleared. When a mark is disputed at the
    end of a semester, "the console did it" is not an answer.
    """
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=_now, index=True)
    actor: str = Field(default="admin", index=True)
    action: str = Field(index=True)     # e.g. "session.extend"
    target: str = Field(default="")     # the thing acted on, e.g. "session:12"
    detail: str = Field(default="")     # short human-readable summary
    ip: str = Field(default="")
