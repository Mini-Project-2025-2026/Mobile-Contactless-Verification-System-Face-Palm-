"""Auth: password hashing, JWT issue/verify, and FastAPI dependencies."""
from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import Session, select

from .config import settings
from .db import get_session
from .models import Device, Student
from .timeutil import now

# pbkdf2_sha256 is pure-Python (hashlib) — avoids native-bcrypt version pitfalls.
_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    if not hashed:  # student with no individual password set
        return False
    return _pwd.verify(raw, hashed)


def create_token(student_id: str, device_uid: str) -> str:
    expire = now() + timedelta(minutes=settings.jwt_expire_minutes)
    claims = {"sub": student_id, "dev": device_uid, "exp": expire}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc


def create_admin_token() -> str:
    expire = now() + timedelta(hours=settings.admin_token_hours)
    return jwt.encode({"sub": "admin", "role": "admin", "exp": expire},
                      settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_kiosk_token(session_id: int, minutes: int) -> str:
    """A token for a shared classroom device, tied to ONE class.

    Deliberately not a student token and not an admin token. A phone passed
    around a lecture hall is the least trustworthy device in the system: it can
    mark attendance for the class it was issued for, for as long as that class
    runs, and it can do nothing else. It cannot read a roster, cannot enrol
    anybody, and stops working when the class does.
    """
    expire = now() + timedelta(minutes=minutes)
    return jwt.encode({"sub": f"kiosk:{session_id}", "role": "kiosk", "ses": session_id,
                       "exp": expire}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def current_kiosk(authorization: str = Header(default="")) -> int:
    """The session id a kiosk token is good for. Rejects anything else."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    claims = _decode(authorization.split(" ", 1)[1].strip())
    if claims.get("role") != "kiosk":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "kiosk_token_required")
    session_id = claims.get("ses")
    if not isinstance(session_id, int):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "kiosk_token_required")
    return session_id


def current_admin(authorization: str = Header(default="")) -> str:
    """Guard admin-only endpoints. Returns the admin subject on success."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    claims = _decode(authorization.split(" ", 1)[1].strip())
    if claims.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return claims.get("sub", "admin")


def current_device_uid(authorization: str = Header(default="")) -> str:
    """The device_uid carried in the caller's token (used for enrolment binding)."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return _decode(authorization.split(" ", 1)[1].strip()).get("dev", "")


def current_student(
    authorization: str = Header(default=""),
    db: Session = Depends(get_session),
) -> Student:
    """Resolve the caller from a bearer token. When `enforce_login_device` is on,
    also require the request to come from the active registered device."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    claims = _decode(authorization.split(" ", 1)[1].strip())
    student_id, device_uid = claims.get("sub", ""), claims.get("dev", "")

    student = db.exec(select(Student).where(Student.student_id == student_id)).first()
    if not student:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown student")

    device = db.exec(select(Device).where(Device.device_uid == device_uid)).first()
    if settings.enforce_login_device and (not device or device.student_id != student_id or not device.active):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device_not_registered")

    if device is not None:
        device.last_seen = now()
        db.add(device)
        db.commit()
    return student
