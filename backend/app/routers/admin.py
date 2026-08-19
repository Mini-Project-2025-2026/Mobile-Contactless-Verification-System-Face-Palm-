"""Admin console API: courses, students, geofenced sessions, attendance.

Auth: POST /api/admin/login with the configured admin credentials returns a
12-hour admin token; every other endpoint requires it (role=admin).
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import audit, biometric, enrolment, guard, policy, queries, reporting
from ..config import settings
from ..db import get_session
from ..middleware import client_ip
from ..models import (
    Attendance,
    AttendanceStatus,
    Course,
    EnrollGrant,
    Enrollment,
    ProgrammeCredential,
    Student,
    norm_programme,
)
from ..models import (
    Session as ClassSession,
)
from ..security import (
    create_admin_token,
    create_kiosk_token,
    current_admin,
    hash_password,
)
from ..timeutil import aware_or_now as _aware
from ..timeutil import now

log = logging.getLogger("attendance.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _note(db: Session, request: Request, actor: str, action: str, *,
          target: str = "", detail: str = "") -> None:
    """Append one audit entry for an action that changed something.

    Never raises. The action it describes has already succeeded and been
    committed; failing the response now would report "not extended" for a
    session that is, in fact, extended.
    """
    try:
        audit.record(db, actor, action, target=target, detail=detail, ip=client_ip(request))
    except Exception:
        log.exception("audit note failed for %s by %s", action, actor)


# ---------- auth ----------
class AdminLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def admin_login(body: AdminLogin, request: Request) -> dict:
    attempt = guard.before_admin_login(request)
    if not guard.check_credentials(body.username, body.password,
                                   settings.admin_username, settings.admin_password):
        guard.after_admin_login(attempt, ok=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin credentials")
    guard.after_admin_login(attempt, ok=True)
    return {"access_token": create_admin_token(), "token_type": "bearer"}


# ---------- overview ----------
@router.get("/overview")
def overview(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    moment = now()
    # Only "live" needs the rows themselves (it depends on the clock); the rest
    # are counts, and counting by loading every row is what this screen did on
    # every refresh, across five tables at once.
    live = sum(1 for s in db.exec(select(ClassSession).where(
        ClassSession.active == True)).all()  # noqa: E712
        if _aware(s.starts_at) <= moment <= _aware(s.ends_at))
    return {
        "students": queries.count(db, Student),
        "courses": queries.count(db, Course),
        "sessions": queries.count(db, ClassSession),
        "live_sessions": live,
        "attendance_records": queries.count(db, Attendance),
    }


# ---------- courses ----------
class CourseIn(BaseModel):
    code: str
    title: str
    semester: str = "2025/2026-1"
    lecturer_name: str = ""


@router.get("/courses")
def list_courses(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    """Courses with their class size.

    A session is only visible to students enrolled in its course, so the count
    is what tells you whether anyone will see the class you are about to open.
    """
    sizes = queries.count_by(db, Enrollment, Enrollment.course_id)
    out = []
    for c in db.exec(select(Course)).all():
        row = c.model_dump()
        row["students"] = sizes.get(c.id, 0)
        out.append(row)
    return out


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, request: Request, actor: str = Depends(current_admin),
                  db: Session = Depends(get_session)) -> dict:
    c = Course(**body.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    # Read the row out BEFORE anything else commits on this session. A commit
    # expires every instance attached to it, and `model_dump()` reads the
    # instance dictionary directly rather than through the attribute
    # instrumentation that would reload it — so it would come back empty.
    created = c.model_dump()
    _note(db, request, actor, "course.create", target=f"course:{c.id}",
          detail=f"{c.code} {c.title} ({c.semester})")
    return created


# ---------- students ----------
class StudentIn(BaseModel):
    student_id: str
    name: str
    # Empty means "no private password": the student signs in with their
    # programme's shared one. A default here would be a backdoor on every account.
    password: str = ""
    programme: str = ""
    year_group: str = ""
    class_group: str = ""
    reference_no: str = ""
    semester: str = "2025/2026-1"


@router.get("/students")
def list_students(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    by_student = queries.collect_by(db, Enrollment, Enrollment.student_id, Enrollment.course_id)
    return [
        {
            "student_id": s.student_id, "name": s.name, "programme": s.programme,
            "year_group": s.year_group, "class_group": s.class_group,
            "active": s.active,
            "biometric_enrolled": s.enrolled_at is not None,
            "course_ids": by_student.get(s.student_id, []),
        }
        for s in db.exec(select(Student)).all()
    ]


@router.post("/students", status_code=201)
def create_student(body: StudentIn, request: Request, actor: str = Depends(current_admin),
                   db: Session = Depends(get_session)) -> dict:
    if db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "student_id already exists")
    s = Student(
        student_id=body.student_id, name=body.name,
        password_hash=hash_password(body.password) if body.password else "",
        programme=body.programme, year_group=body.year_group, class_group=body.class_group,
        reference_no=body.reference_no, semester=body.semester,
    )
    db.add(s)
    db.commit()
    _note(db, request, actor, "student.create", target=f"student:{s.student_id}",
          detail=f"{s.name} / {s.programme or 'no programme'}")
    return {"student_id": s.student_id, "name": s.name}


class EnrollCourseIn(BaseModel):
    student_id: str
    course_id: int


@router.post("/enroll-course", status_code=201)
def enroll_course(body: EnrollCourseIn, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    if db.get(Course, body.course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    if not db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown student")
    existing = db.exec(select(Enrollment).where(
        Enrollment.student_id == body.student_id, Enrollment.course_id == body.course_id)).first()
    if existing is None:
        db.add(Enrollment(student_id=body.student_id, course_id=body.course_id))
        db.commit()
    return {"student_id": body.student_id, "course_id": body.course_id, "enrolled": True}


# ---------- sessions ----------
class SessionIn(BaseModel):
    course_id: int
    title: str = ""
    lat: float
    lng: float
    radius_m: float = Field(default=70.0, ge=10, le=1000)
    duration_minutes: int = Field(default=90, ge=5, le=1440)
    marks_required: int = Field(default=2, ge=1, le=5)


@router.get("/sessions")
def list_sessions(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    moment = now()
    sessions = db.exec(select(ClassSession)).all()
    courses = {c.id: c for c in db.exec(select(Course)).all()}
    class_sizes = queries.count_by(db, Enrollment, Enrollment.course_id)

    # One pass over this list's attendance rows, instead of a query per session.
    by_session: dict[int, list] = {}
    for row in queries.fetch_in(db, Attendance, Attendance.session_id, [s.id for s in sessions]):
        by_session.setdefault(row.session_id, []).append(row.status)

    out = []
    for s in sessions:
        c = courses.get(s.course_id)
        statuses = by_session.get(s.id, [])
        # Split the rows so the console can say who is still short of present:
        # a session that ends while stuck in START leaves every one of them partial.
        out.append({
            "id": s.id, "course_code": c.code if c else "?", "title": s.title,
            "lat": s.lat, "lng": s.lng, "radius_m": s.radius_m,
            "starts_at": _aware(s.starts_at).isoformat(), "ends_at": _aware(s.ends_at).isoformat(),
            "live": s.active and _aware(s.starts_at) <= moment <= _aware(s.ends_at), "active": s.active,
            "phase": s.phase, "marks_required": s.marks_required, "checked_in": len(statuses),
            "partial": sum(1 for st in statuses if st == AttendanceStatus.partial),
            "present": sum(1 for st in statuses if st == AttendanceStatus.present),
            # who can even see this session: nobody, if the course has no students
            "enrolled": class_sizes.get(s.course_id, 0),
        })
    return sorted(out, key=lambda x: x["starts_at"], reverse=True)


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn, request: Request, actor: str = Depends(current_admin),
                   db: Session = Depends(get_session)) -> dict:
    if db.get(Course, body.course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    moment = now()
    s = ClassSession(
        course_id=body.course_id, title=body.title, lat=body.lat, lng=body.lng,
        radius_m=body.radius_m, starts_at=moment - timedelta(minutes=1),
        ends_at=moment + timedelta(minutes=body.duration_minutes), marks_required=body.marks_required,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    _note(db, request, actor, "session.open", target=f"session:{s.id}",
          detail=f"course {body.course_id} for {body.duration_minutes}min "
                 f"at ({body.lat:.5f},{body.lng:.5f}) r={body.radius_m:.0f}m")
    return {"session_id": s.id}


@router.post("/sessions/{session_id}/close")
def close_session(session_id: int, request: Request, actor: str = Depends(current_admin),
                  db: Session = Depends(get_session)) -> dict:
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    s.active = False
    s.phase = "closed"
    db.add(s)
    db.commit()
    _note(db, request, actor, "session.close", target=f"session:{session_id}")
    return {"session_id": session_id, "active": False}


class PhaseIn(BaseModel):
    phase: str  # "start" | "end" | "closed"


@router.post("/sessions/{session_id}/phase")
def set_phase(session_id: int, body: PhaseIn, request: Request,
              actor: str = Depends(current_admin),
              db: Session = Depends(get_session)) -> dict:
    """Open the start/end check-in window (or pause it). Present needs both windows."""
    if body.phase not in ("start", "end", "closed"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "phase must be start|end|closed")
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    was, s.phase = s.phase, body.phase
    db.add(s)
    db.commit()
    _note(db, request, actor, "session.phase", target=f"session:{session_id}",
          detail=f"{was} -> {s.phase}")
    return {"session_id": session_id, "phase": s.phase}


class ExtendIn(BaseModel):
    minutes: int = Field(default=15, ge=1, le=180)


@router.post("/sessions/{session_id}/extend")
def extend_session(session_id: int, body: ExtendIn, request: Request,
                   actor: str = Depends(current_admin),
                   db: Session = Depends(get_session)) -> dict:
    """Push a session's end time back, so the END window still has room to run.

    Two-phase attendance needs both windows inside the session; a class that
    expires while still in START leaves everyone who marked stuck on partial.
    Extending from an already-expired session runs from now, not from the old
    end time, so the added minutes are minutes students can actually use.
    """
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    moment = now()
    base = max(_aware(s.ends_at), moment)
    s.ends_at = base + timedelta(minutes=body.minutes)
    if not s.active:  # bringing a closed class back needs it live again
        s.active = True
    db.add(s)
    db.commit()
    _note(db, request, actor, "session.extend", target=f"session:{session_id}",
          detail=f"+{body.minutes}min, now ends {_aware(s.ends_at).isoformat()}")
    return {"session_id": session_id, "ends_at": _aware(s.ends_at).isoformat(), "active": s.active}


class KioskIn(BaseModel):
    #: Minutes past the end of the class that the device keeps working. Short:
    #: a phone that is handed around should stop being able to mark soon after
    #: the lecture it belongs to is over.
    grace_minutes: int = Field(default=0, ge=0, le=240)


@router.post("/sessions/{session_id}/kiosk", status_code=201)
def open_kiosk(session_id: int, body: KioskIn, request: Request,
               actor: str = Depends(current_admin),
               db: Session = Depends(get_session)) -> dict:
    """Mint a token for a shared classroom device covering THIS class only.

    The device can then identify whoever stands in front of it and mark them,
    with no student typing an id or repeating the programme password aloud. It
    can do nothing else, and it stops working when the class does.
    """
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    grace = body.grace_minutes or settings.kiosk_token_grace_minutes
    remaining = (_aware(s.ends_at) - now()).total_seconds() / 60
    minutes = max(5, int(remaining) + grace)
    _note(db, request, actor, "kiosk.open", target=f"session:{session_id}",
          detail=f"device token valid {minutes}min")
    return {
        "session_id": session_id,
        "access_token": create_kiosk_token(session_id, minutes),
        "token_type": "bearer",
        "valid_minutes": minutes,
        "note": ("Load this on the classroom device. It can mark attendance for "
                 "this class only, and expires with it."),
    }


# ---------- end-of-semester course report ----------
@router.get("/courses/{course_id}/report")
def course_report(course_id: int, _: str = Depends(current_admin),
                  db: Session = Depends(get_session)) -> dict:
    """Everything recorded for one course: every class, every student, every mark."""
    report = reporting.build(db, course_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    return report


@router.get("/courses/{course_id}/report.csv")
def course_report_csv(course_id: int, shape: str = "roll",
                      _: str = Depends(current_admin),
                      db: Session = Depends(get_session)) -> Response:
    """The same record as a spreadsheet a lecturer can keep.

    `shape=roll` is the register: one row per student, one column per class.
    `shape=marks` is the audit trail: one row per student per class, with the
    times, scores and distances behind each figure in the register.
    """
    if shape not in ("roll", "marks"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "shape must be roll or marks")
    report = reporting.build(db, course_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    body = reporting.roll_csv(report) if shape == "roll" else reporting.marks_csv(report)
    name = reporting.filename(report, shape)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ---------- bulk enrolment ----------
class BulkPersonIn(BaseModel):
    student_id: str
    images: list[str] = Field(min_length=1)


class BulkEnrollIn(BaseModel):
    people: list[BulkPersonIn] = Field(min_length=1, max_length=200)
    dedupe: bool = True
    #: Hand the batch to the service's queue and return a job to poll, instead
    #: of holding this request open while it works. Anything past a handful of
    #: people should: a gateway will cut the socket long before the work is done,
    #: and the operator is left not knowing what landed.
    queue: bool = False


@router.post("/enroll/bulk")
def bulk_enroll(body: BulkEnrollIn, request: Request,
                actor: str = Depends(current_admin),
                db: Session = Depends(get_session)) -> dict:
    """Register a batch of students' biometrics in one pass.

    Enrolment is the in-person step, so doing it a student at a time does not
    scale to a department. This takes the same shape the biometric service's own
    bulk import takes (one person, their images) and records the outcome against
    each student here, so nobody is asked to enrol again on their phone.

    Students unknown to this database are reported, never sent onward: a typo in
    a folder name must not create a template nothing can match.
    """
    known = {
        st.student_id: st
        for st in db.exec(select(Student).where(
            Student.student_id.in_([p.student_id for p in body.people])  # type: ignore[attr-defined]
        )).all()
    }
    results: list[dict] = [
        {"student_id": p.student_id, "success": False, "enrolled": 0, "message": "no such student here"}
        for p in body.people if p.student_id not in known
    ]
    sendable = [(p.student_id, p.images) for p in body.people if p.student_id in known]
    if not sendable:
        return {"people": len(body.people), "enrolled": 0, "results": results}

    try:
        outcome = biometric.enroll_users_bulk(sendable, dedupe=body.dedupe, queue=body.queue)
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"biometric_unavailable: {exc}") from exc

    if outcome.queued:
        # Nothing is recorded here yet: the service has accepted the work, not
        # done it. `POST /api/admin/enroll/bulk/{job_id}` reads the outcome back
        # and writes it against each student, once there is an outcome to write.
        _note(db, request, actor, "enrol.bulk_queued",
              detail=f"{len(sendable)} people, job {outcome.job_id}")
        return {"queued": True, "job_id": outcome.job_id, "people": len(body.people),
                "submitted": len(sendable), "results": results,
                "poll": f"/api/admin/enroll/bulk/{outcome.job_id}",
                "message": ("Import accepted. Poll the job for progress; results are "
                            "recorded against each student when it finishes.")}

    adopted, _ = _adopt_bulk_results(db, outcome.results)
    results.extend(adopted)
    _note(db, request, actor, "enrol.bulk",
          detail=f"{outcome.enrolled} enrolled of {len(body.people)} submitted")
    return {"people": len(body.people), "enrolled": outcome.enrolled, "results": results}


def _adopt_bulk_results(db: Session, results: tuple) -> tuple[list[dict], int]:
    """Write a batch's per-person outcome against the students it names."""
    known = {
        st.student_id: st
        for st in queries.fetch_in(db, Student, Student.student_id,
                                   [r.user_id for r in results])
    }
    rows: list[dict] = []
    enrolled = 0
    moment = now()
    for res in results:
        student = known.get(res.user_id)
        if student is None:
            rows.append({"student_id": res.user_id, "success": False,
                         "message": "no such student here"})
            continue
        if res.success:
            enrolled += 1
            mods = {m for m in (student.enrolled_modality or "").split(",") if m}
            mods.update(res.modalities or ("face",))
            student.enrolled_modality = ",".join(sorted(mods))
            student.enrolled_at = student.enrolled_at or moment
            student.enrolled_samples = max(student.enrolled_samples, res.enrolled)
            db.add(student)
        message = res.message or ("enrolled" if res.success else "not enrolled")
        if res.duplicate:
            # Never silently skipped: an import that would tie one face to a second
            # student ID is the one outcome an operator has to see by name.
            whose = ", ".join(res.conflict_user_ids) or "another student"
            message = f"already registered to {whose}"
        rows.append({
            "student_id": res.user_id, "name": student.name, "success": res.success,
            "enrolled": res.enrolled, "modalities": list(res.modalities),
            "duplicate": res.duplicate, "conflicts": list(res.conflict_user_ids),
            "message": message,
        })
    db.commit()
    enrolment.reset_cache()
    return rows, enrolled


@router.get("/enroll/bulk/{job_id}")
def bulk_enroll_job(job_id: str, request: Request, actor: str = Depends(current_admin),
                    db: Session = Depends(get_session)) -> dict:
    """Progress of a queued import — and, once it is done, its results recorded.

    Reading a finished job is what writes the outcome against each student here,
    so nobody who was enrolled in the batch is asked to enrol again on their
    phone. Safe to poll: adopting the same finished job twice changes nothing.
    """
    try:
        state = biometric.job_status(job_id)
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc

    status_name = str(state.get("status") or "")
    out = {"job_id": job_id, "status": status_name,
           "done": int(state.get("done") or 0), "people": int(state.get("people") or 0)}
    if status_name not in ("done", "finished", "complete", "completed"):
        return out

    rows, enrolled = _adopt_bulk_results(db, biometric._bulk_results(state))
    _note(db, request, actor, "enrol.bulk_adopted", detail=f"job {job_id}: {enrolled} enrolled")
    return {**out, "enrolled": enrolled, "results": rows}


class BulkCourseEnrollIn(BaseModel):
    course_id: int
    programme: str = ""
    year_group: str = ""
    class_group: str = ""


@router.post("/enroll-course/bulk")
def bulk_enroll_course(body: BulkCourseEnrollIn, request: Request,
                       actor: str = Depends(current_admin),
                       db: Session = Depends(get_session)) -> dict:
    """Put a whole programme (optionally one year or class group) on a course.

    A session is only visible to students enrolled in its course, so registering
    a cohort one row at a time is the step most likely to be left half done.
    """
    if db.get(Course, body.course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown course")
    if not body.programme.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "programme is required")

    # Indexed on the normalised column rather than reading every student row and
    # normalising in Python: a department is thousands of rows, and this endpoint
    # is the one an admin runs while a class waits.
    wanted = norm_programme(body.programme)
    query = select(Student).where(Student.programme_key == wanted)
    if body.year_group:
        query = query.where(Student.year_group == body.year_group)
    if body.class_group:
        query = query.where(Student.class_group == body.class_group)
    students = list(db.exec(query).all())
    existing = set(db.exec(select(Enrollment.student_id).where(
        Enrollment.course_id == body.course_id)).all())
    added = [st.student_id for st in students if st.student_id not in existing]
    for student_id in added:
        db.add(Enrollment(student_id=student_id, course_id=body.course_id))
    db.commit()
    _note(db, request, actor, "course.enrol_cohort", target=f"course:{body.course_id}",
          detail=f"{body.programme} {body.year_group} {body.class_group}".strip()
                 + f" — {len(added)} added of {len(students)} matched")
    return {
        "course_id": body.course_id, "matched": len(students),
        "added": len(added), "already_enrolled": len(students) - len(added),
    }


# ---------- programme sign-in passwords ----------
class ProgrammePasswordIn(BaseModel):
    programme: str
    # Short by password standards, and deliberately so: this is the shared half of a
    # credential whose identifying half is the student's own ID, and it is read out in
    # a lecture hall. What protects attendance is the face at check-in, not this.
    password: str = Field(min_length=6)


@router.get("/programmes")
def list_programmes(_: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    """Every programme in use, its class size, and whether it can sign in yet."""
    creds = {c.programme for c in db.exec(select(ProgrammeCredential)).all()}
    sizes = queries.count_by(db, Student, Student.programme_key,
                             Student.programme_key != "")
    # One readable spelling per programme, taken from the students themselves.
    labels = dict(db.exec(select(Student.programme_key, Student.programme).where(
        Student.programme_key != "")).all())
    return sorted(
        ({"programme": labels.get(key, key), "key": key, "students": total,
          "password_set": key in creds}
         for key, total in sizes.items()),
        key=lambda r: r["programme"].lower(),
    )


@router.post("/programmes/password")
def set_programme_password(body: ProgrammePasswordIn, request: Request,
                           actor: str = Depends(current_admin),
                           db: Session = Depends(get_session)) -> dict:
    """Set or rotate the password everyone on a programme signs in with."""
    key = norm_programme(body.programme)
    if not key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "programme is required")
    cred = db.get(ProgrammeCredential, key)
    if cred is None:
        cred = ProgrammeCredential(programme=key, password_hash=hash_password(body.password))
    else:
        cred.password_hash = hash_password(body.password)
        cred.updated_at = now()
    db.add(cred)
    db.commit()
    # The password itself is never recorded — only that it was rotated, by whom.
    _note(db, request, actor, "programme.password", target=f"programme:{key}",
          detail="set or rotated")
    students = queries.count(db, Student, Student.programme != "")
    return {"programme": key, "updated": True, "students_on_programme": students}


# ---------- the verification tenant: capacity, template health, erasure ----------
@router.get("/verification")
def verification_state(_: str = Depends(current_admin)) -> dict:
    """What the verification service says about itself, for this tenant.

    Three questions an administrator could not previously answer from anywhere:
    are we near a usage limit, is the service reachable, and what are the
    thresholds our check-ins are actually being judged against. The last of
    these was set independently here and there, with no way to see both.
    """
    health = biometric.service_health()
    out: dict = {
        "service": {"ok": health.ok, "version": health.version,
                    "url": settings.biometric_base_url,
                    "active_liveness": health.active_liveness},
        "policy": policy.describe(),
    }
    try:
        out["usage"] = biometric.usage_summary()
    except biometric.BiometricError as exc:
        # Usage is informational; a tenant on a plan that does not meter it, or
        # a brief outage, must not take the whole screen down.
        out["usage"] = {"available": False, "reason": str(exc)[:200]}
    return out


@router.get("/templates")
def template_health(student_id: str = "", _: str = Depends(current_admin)) -> dict:
    """Which protection domain stored templates live in, tenant-wide or for one
    person. Reported before exam week, not discovered during it."""
    try:
        return biometric.templates_status(student_id)
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc


class EraseIn(BaseModel):
    student_id: str
    #: Typing the student id again. Erasure cannot be undone and the person has
    #: to enrol in person to come back, so a mis-click should not be enough.
    confirm_student_id: str


@router.post("/students/erase-biometrics")
def erase_biometrics(body: EraseIn, request: Request, actor: str = Depends(current_admin),
                     db: Session = Depends(get_session)) -> dict:
    """Erase one student's biometric record, here and at the service.

    The attendance they already earned stays: it is an academic record of
    classes attended, not biometric data, and deleting it would punish the
    student for exercising a right. What goes is the template, the credentials
    issued from it, and our cached belief that they are enrolled — which would
    otherwise keep waving them past the face-required gate to a verify that can
    only fail.
    """
    if body.student_id != body.confirm_student_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "confirm_mismatch: the confirmation must repeat the student id")
    student = db.exec(select(Student).where(Student.student_id == body.student_id)).first()
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown student")

    try:
        outcome = biometric.delete_users([body.student_id])
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc

    student.enrolled_modality = ""
    student.enrolled_at = None
    student.enrolled_samples = 0
    student.enroll_device_uid = ""
    db.add(student)
    db.commit()
    enrolment.reset_cache()
    _note(db, request, actor, "student.erase_biometrics",
          target=f"student:{body.student_id}",
          detail=f"{outcome.get('deleted', 0)} deleted, "
                 f"{outcome.get('credentials_revoked', 0)} credentials revoked")
    return {
        "student_id": body.student_id,
        "deleted": bool(outcome.get("deleted")),
        "credentials_revoked": outcome.get("credentials_revoked", 0),
        "message": ("Biometric record erased. Attendance already recorded is unchanged. "
                    "The student must enrol in person to mark attendance again."),
    }


# ---------- consent (the lawful basis for holding a face) ----------
@router.get("/consent")
def consent_position(_: str = Depends(current_admin)) -> dict:
    """How many students have consented, how many have withdrawn, and to what."""
    try:
        return biometric.consent_summary()
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc


class ConsentRecordIn(BaseModel):
    student_id: str
    #: "operator" is consent gathered on paper and entered here. A student who
    #: agrees in the app records "self", which is the stronger form — this
    #: endpoint deliberately cannot claim that on their behalf.
    note: str = ""


@router.post("/consent/record", status_code=201)
def record_paper_consent(body: ConsentRecordIn, request: Request,
                         actor: str = Depends(current_admin),
                         db: Session = Depends(get_session)) -> dict:
    """Record consent gathered offline, e.g. on a signed form at registration."""
    if not db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown student")
    try:
        receipt = biometric.record_consent(body.student_id, method="operator")
    except biometric.BiometricError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"biometric_unavailable: {exc}") from exc
    _note(db, request, actor, "consent.record", target=f"student:{body.student_id}",
          detail=f"operator-entered, v{receipt.version}: {body.note}".strip())
    return {"student_id": body.student_id, "status": "granted",
            "method": receipt.method, "version": receipt.version,
            "note": body.note}


# ---------- enrolment grants (one-time re-enrolment / new-device codes) ----------
class GrantIn(BaseModel):
    student_id: str
    ttl_hours: int = Field(default=24, ge=1, le=720)


@router.post("/enroll-grant", status_code=201)
def issue_grant(body: GrantIn, request: Request, actor: str = Depends(current_admin),
                db: Session = Depends(get_session)) -> dict:
    if not db.exec(select(Student).where(Student.student_id == body.student_id)).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown student")
    token = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8].upper()
    expires = now() + timedelta(hours=body.ttl_hours)
    db.add(EnrollGrant(token=token, student_id=body.student_id, expires_at=expires))
    db.commit()
    # A grant is what lets a face be re-bound to a student id. If any single
    # action here needs a name against it afterwards, it is this one.
    _note(db, request, actor, "grant.issue", target=f"student:{body.student_id}",
          detail=f"one-time code, valid {body.ttl_hours}h")
    return {"student_id": body.student_id, "token": token, "expires_at": expires.isoformat(),
            "note": "Give this one-time code to the student to re-enrol or enrol on a new device."}


@router.get("/enroll-grants")
def list_grants(student_id: str, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.exec(select(EnrollGrant).where(EnrollGrant.student_id == student_id)).all()
    moment = now()
    out = []
    for g in rows:
        exp = _aware(g.expires_at)
        out.append({"token": g.token, "expires_at": exp.isoformat(),
                    "used": g.used_at is not None, "expired": exp < moment})
    return sorted(out, key=lambda x: (x["used"] or x["expired"], x["expires_at"]))


@router.post("/enroll-grants/{token}/revoke")
def revoke_grant(token: str, request: Request, actor: str = Depends(current_admin),
                 db: Session = Depends(get_session)) -> dict:
    g = db.exec(select(EnrollGrant).where(EnrollGrant.token == token)).first()
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    if g.used_at is None:  # marking it used = revoked (validation rejects used tokens)
        g.used_at = now()
        db.add(g)
        db.commit()
        _note(db, request, actor, "grant.revoke", target=f"student:{g.student_id}")
    return {"token": token, "revoked": True}


# ---------- the trail ----------
@router.get("/audit")
def audit_trail(limit: int = 100, action: str = "", target: str = "",
                _: str = Depends(current_admin),
                db: Session = Depends(get_session)) -> list[dict]:
    """Who changed what, most recent first.

    Every entry here is an action that altered an academic record. When a mark
    is disputed at the end of a semester, "the console did it" is not an answer.
    """
    return [
        {"at": _aware(row.at).isoformat(), "actor": row.actor, "action": row.action,
         "target": row.target, "detail": row.detail, "ip": row.ip}
        for row in audit.recent(db, limit=min(max(limit, 1), 500),
                                action=action, target=target)
    ]


# ---------- attendance ----------
@router.get("/attendance")
def session_attendance(session_id: int, _: str = Depends(current_admin), db: Session = Depends(get_session)) -> dict:
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown session")
    course = db.get(Course, s.course_id)
    enrolled = db.exec(select(Enrollment.student_id).where(Enrollment.course_id == s.course_id)).all()
    # A lecture hall is hundreds of students, and this was two queries for each.
    names = {st.student_id: st.name
             for st in queries.fetch_in(db, Student, Student.student_id, enrolled)}
    marked = {a.student_id: a for a in db.exec(select(Attendance).where(
        Attendance.session_id == session_id)).all()}
    rows = []
    for sid in enrolled:
        att = marked.get(sid)
        rows.append({
            "student_id": sid, "name": names.get(sid, sid),
            "status": att.status.value if att else "absent",
            "marks": att.marks_count if att else 0,
            "score": round(att.best_score, 3) if att else 0.0,
            "last_marked_at": (_aware(att.last_marked_at).isoformat()
                               if att and att.last_marked_at else None),
        })
    rows.sort(key=lambda r: (r["status"] != "present", r["name"]))
    return {"session_id": session_id, "course": course.code if course else "?", "title": s.title, "rows": rows}
