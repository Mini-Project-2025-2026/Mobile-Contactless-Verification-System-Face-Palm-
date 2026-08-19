"""Request/response models for the API surface."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import AttendanceStatus, Modality


# --- auth ---
class LoginRequest(BaseModel):
    student_id: str
    password: str
    device_uid: str
    platform: str = "unknown"
    device_name: str = "device"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    student_id: str
    name: str


# --- courses / sessions ---
class Gps(BaseModel):
    lat: float
    lng: float
    accuracy_m: float | None = None


class AvailableCourse(BaseModel):
    session_id: int
    course_code: str
    course_title: str
    session_title: str
    lecturer_name: str
    distance_m: float
    radius_m: float
    in_range: bool
    ends_at: datetime
    center_lat: float
    center_lng: float
    phase: str  # "start" | "end" | "closed" — which window is open now
    marked_start: bool
    marked_end: bool
    status: str  # this student's status for the session: absent | partial | present


# --- check-in ---
class ChallengeRequest(BaseModel):
    session_id: int


class ChallengeResponse(BaseModel):
    token: str
    instruction: str
    modality: Modality = Modality.face
    active: bool = True


class VerifyRequest(BaseModel):
    session_id: int
    token: str = ""
    frames: list[str] = Field(default_factory=list, description="base64 JPEG/PNG frames")
    image: str | None = None  # single-shot fallback (no active liveness)
    modality: Modality = Modality.face
    gps: Gps


class VerifyResponse(BaseModel):
    ok: bool
    status: AttendanceStatus
    marks_count: int
    marks_required: int
    distance_m: float
    score: float = 0.0
    code: str = "ok"
    message: str = ""


# --- history ---
class AttendanceItem(BaseModel):
    course_code: str
    course_title: str
    session_title: str
    date: datetime
    status: AttendanceStatus


class SemesterHistory(BaseModel):
    semester: str
    items: list[AttendanceItem]


class DeviceItem(BaseModel):
    device_uid: str
    platform: str
    name: str
    active: bool
    last_seen: datetime


class Profile(BaseModel):
    student_id: str
    name: str
    reference_no: str
    programme: str
    year_group: str
    class_group: str
    semester: str
    enrolled: bool = False


class EnrollRequest(BaseModel):
    modality: Modality = Modality.face
    images: list[str] = Field(default_factory=list, description="base64 JPEG/PNG samples")
    source: str = "auto"
    grant_token: str = ""  # admin one-time token, required for re-enrolment / new device


class EnrollResponse(BaseModel):
    ok: bool
    enrolled: int
    of: int
    samples: int
    modality: Modality
    message: str
    code: str = "ok"  # ok | grant_required | no_biometric


class EnrollStatus(BaseModel):
    face_enrolled: bool
    palm_enrolled: bool
    can_mark: bool  # true once the compulsory face enrolment exists
