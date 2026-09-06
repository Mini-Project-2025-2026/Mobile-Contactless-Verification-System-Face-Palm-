"""Recording what the console did, so a disputed mark has an answer.

Every admin endpoint here alters an academic record: a class extended past its
end, a one-time enrolment code issued, a student's device binding cleared, a
whole cohort registered onto a course. At the end of a semester those changes
are the difference between a student passing and repeating, and until now none
of them left a trace.

Writing the entry must never be the reason an action fails — a full disk or a
locked table would otherwise turn "session extended" into "session not
extended". So a failure to record is logged loudly and swallowed.
"""
from __future__ import annotations

import contextlib
import logging

from sqlmodel import Session, select

from .models import AuditLog

log = logging.getLogger("attendance.audit")


def record(db: Session, actor: str, action: str, *, target: str = "",
           detail: str = "", ip: str = "") -> None:
    """Append one entry. Best-effort by design: never raises."""
    try:
        db.add(AuditLog(actor=actor, action=action, target=target, detail=detail, ip=ip))
        db.commit()
    except Exception as exc:  # never let bookkeeping fail the action itself
        log.error("audit write failed for %s by %s: %s", action, actor, exc)
        with contextlib.suppress(Exception):
            db.rollback()
    else:
        log.info("audit %s %s %s", actor, action, target, extra={"actor": actor})


def recent(db: Session, *, limit: int = 100, action: str = "", target: str = "") -> list[AuditLog]:
    """The newest entries, optionally narrowed to one action or one target."""
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if target:
        query = query.where(AuditLog.target == target)
    rows = db.exec(query.order_by(AuditLog.id.desc()).limit(limit)).all()  # type: ignore[attr-defined]
    return list(rows)
