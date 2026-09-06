"""Sign-in guards: constant-time credential checks and brute-force lockout.

Two of this system's credentials are weak by design and cannot be made strong.
A programme password is shared by a whole cohort and read out in a lecture hall.
The console has one username. Neither survives an attacker allowed to guess as
fast as the network permits — and guessing a student's password is enough to
sign in as them and wait for an enrolment grant, so the biometric downstream
does not save us.

Nothing here makes those credentials stronger. It makes guessing slow, and it
stops the check itself from leaking how close a guess was.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request, status

from .config import settings
from .middleware import client_ip
from .ratelimit import SlidingWindow, client_key

log = logging.getLogger("attendance.guard")

#: Per (student id, client address). Keying on both means one attacker cannot
#: lock a whole cohort out by guessing at every id from one machine, and one
#: student's own repeated mistakes do not lock the address a hall of phones share.
student_logins = SlidingWindow(
    settings.login_attempts, settings.login_window_s, settings.login_lockout_s)

#: Per client address only — the console has a single account, so there is no
#: identity dimension to spread an attack across.
admin_logins = SlidingWindow(
    settings.admin_login_attempts, settings.admin_login_window_s,
    settings.admin_login_lockout_s)


def constant_time_equals(supplied: str, expected: str) -> bool:
    """Compare two secrets without leaking where they first differ.

    `a == b` on strings returns as soon as a character differs, so the time it
    takes is a measure of how much of the password was right. That is a usable
    signal against a fixed console password.
    """
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def check_credentials(supplied_user: str, supplied_password: str,
                      expected_user: str, expected_password: str) -> bool:
    """Both halves, both compared in constant time, with no early exit."""
    user_ok = constant_time_equals(supplied_user, expected_user)
    password_ok = constant_time_equals(supplied_password, expected_password)
    return user_ok and password_ok


def _refuse(retry_after_s: int, who: str) -> HTTPException:
    minutes = max(1, retry_after_s // 60)
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"too_many_attempts: Too many failed sign-in attempts. "
        f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(retry_after_s)},
    )


def before_student_login(request: Request, student_id: str) -> str:
    """Raise 429 if this identity/address pair is locked out. Returns the key."""
    key = client_key(student_id.strip().lower(), client_ip(request))
    decision = student_logins.check(key)
    if not decision.allowed:
        log.warning("login locked out for %s", key)
        raise _refuse(decision.retry_after_s, "student")
    return key


def after_student_login(key: str, *, ok: bool) -> None:
    """Record the outcome. A correct password clears the history entirely."""
    if ok:
        student_logins.clear(key)
    else:
        student_logins.record(key)


def before_admin_login(request: Request) -> str:
    key = client_key("admin", client_ip(request))
    decision = admin_logins.check(key)
    if not decision.allowed:
        log.warning("admin login locked out for %s", key)
        raise _refuse(decision.retry_after_s, "admin")
    return key


def after_admin_login(key: str, *, ok: bool) -> None:
    if ok:
        admin_logins.clear(key)
    else:
        admin_logins.record(key)


def reset_all() -> None:
    """Clear every window (tests, and an operator unlocking everyone)."""
    student_logins.reset()
    admin_logins.reset()


def sweep() -> int:
    """Discard windows nothing is using. Called periodically so an idle process
    does not keep one entry per student id it has ever seen."""
    return student_logins.sweep() + admin_logins.sweep()
