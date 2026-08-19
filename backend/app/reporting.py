"""The end-of-semester record for one course: every class, every student.

A lecturer closing a semester needs one artefact, not thirty session screens: who
was in each class, who was verified and when, who never enrolled, and the rate per
student. This module assembles that record once; the router serves it as JSON for
the console and as CSV for a spreadsheet, so both always agree.

Two shapes, because they answer different questions:
  * **roll** - one row per student, one column per class, plus totals. What a
    lecturer reads, and what goes in a gradebook.
  * **marks** - one row per individual verified mark, with time, score, distance
    and modality. The audit trail behind any single figure in the roll.
"""
from __future__ import annotations

import csv
import io

from sqlmodel import Session, select

from . import queries
from .models import (
    Attendance,
    AttendanceMark,
    Course,
    Enrollment,
    Session as ClassSession,
    Student,
)
from .timeutil import aware as _aware, iso as _iso, now as _now

#: Marks required for "present" are per session, but a course-level rate needs one
#: rule: a class counts for a student only if they completed every window it ran.
PRESENT = "present"




def build(db: Session, course_id: int) -> dict | None:
    """Assemble the whole record for one course. None when the course is unknown."""
    course = db.get(Course, course_id)
    if course is None:
        return None

    sessions = sorted(
        db.exec(select(ClassSession).where(ClassSession.course_id == course_id)).all(),
        key=lambda s: _aware(s.starts_at),
    )
    student_ids = db.exec(
        select(Enrollment.student_id).where(Enrollment.course_id == course_id)).all()
    students = sorted(
        queries.fetch_in(db, Student, Student.student_id, student_ids),
        key=lambda s: (s.name or "", s.student_id),
    )

    # Scoped to this course. Reading every attendance row in the database and
    # then discarding other courses' made one lecturer's report cost grow with
    # every other lecturer's attendance.
    session_ids = [s.id for s in sessions]
    attendance = {
        (a.session_id, a.student_id): a
        for a in queries.fetch_in(db, Attendance, Attendance.session_id, session_ids)
    }
    marks_by_attendance: dict[int, list[AttendanceMark]] = {}
    for mark in queries.fetch_in(db, AttendanceMark, AttendanceMark.attendance_id,
                                 [a.id for a in attendance.values()]):
        marks_by_attendance.setdefault(mark.attendance_id, []).append(mark)

    classes = [{
        "session_id": s.id,
        "title": s.title or course.title,
        "starts_at": _iso(s.starts_at),
        "ends_at": _iso(s.ends_at),
        "radius_m": s.radius_m,
        "marks_required": s.marks_required,
        "phase": s.phase,
        "closed": not s.active or _aware(s.ends_at) < _now(),
    } for s in sessions]

    rows = []
    for student in students:
        per_class, present_count, partial_count = [], 0, 0
        for s in sessions:
            record = attendance.get((s.id, student.student_id))
            marks = marks_by_attendance.get(record.id, []) if record else []
            phases = {m.phase for m in marks}
            status = record.status.value if record else "absent"
            if status == PRESENT:
                present_count += 1
            elif status == "partial":
                partial_count += 1
            per_class.append({
                "session_id": s.id,
                "status": status,
                "marked_start": "start" in phases,
                "marked_end": "end" in phases,
                "marks": len(phases),
                "best_score": round(record.best_score, 4) if record else 0.0,
                "first_marked_at": _iso(record.first_marked_at) if record else "",
                "last_marked_at": _iso(record.last_marked_at) if record else "",
                "modalities": sorted({m.modality.value for m in marks}),
                "closest_m": round(min((m.distance_m for m in marks), default=0.0), 1),
            })

        held = len(sessions)
        modalities = [m for m in (student.enrolled_modality or "").split(",") if m]
        rows.append({
            "student_id": student.student_id,
            "name": student.name,
            "reference_no": student.reference_no,
            "programme": student.programme,
            "year_group": student.year_group,
            "class_group": student.class_group,
            # Enrolment is reported per student because "absent" for someone who
            # never enrolled is a different problem from "absent" for someone who did.
            "biometrics_enrolled": bool(modalities),
            "enrolled_modalities": modalities,
            "enrolled_at": _iso(student.enrolled_at),
            "classes": per_class,
            "present": present_count,
            "partial": partial_count,
            "absent": held - present_count - partial_count,
            "held": held,
            "rate": round(present_count / held, 4) if held else 0.0,
        })

    return {
        "course": {
            "id": course.id, "code": course.code, "title": course.title,
            "semester": course.semester, "lecturer_name": course.lecturer_name,
        },
        "generated_at": _iso(_now()),
        "classes": classes,
        "students": rows,
        "totals": {
            "classes_held": len(sessions),
            "students": len(rows),
            "never_enrolled": sum(1 for r in rows if not r["biometrics_enrolled"]),
            "full_attendance": sum(1 for r in rows if r["held"] and r["present"] == r["held"]),
        },
    }


def _label(cls: dict) -> str:
    """A column header a lecturer recognises: the date, then the class title."""
    date = (cls["starts_at"] or "")[:10]
    title = cls["title"] or ""
    return f"{date} {title}".strip() or f"session {cls['session_id']}"


def roll_csv(report: dict) -> str:
    """One row per student, one column per class, totals on the right."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    course = report["course"]

    writer.writerow([f"{course['code']} - {course['title']}"])
    writer.writerow([f"Semester: {course['semester']}",
                     f"Lecturer: {course['lecturer_name'] or '-'}",
                     f"Classes held: {report['totals']['classes_held']}",
                     f"Generated: {report['generated_at']}"])
    writer.writerow([])

    header = ["Student ID", "Name", "Reference", "Programme", "Year", "Class",
              "Biometrics enrolled"]
    header += [_label(c) for c in report["classes"]]
    header += ["Present", "Partial", "Absent", "Classes held", "Attendance %"]
    writer.writerow(header)

    for row in report["students"]:
        line = [row["student_id"], row["name"], row["reference_no"], row["programme"],
                row["year_group"], row["class_group"],
                ",".join(row["enrolled_modalities"]) if row["biometrics_enrolled"] else "NOT ENROLLED"]
        for cls in row["classes"]:
            # start/end shown separately: "present" hides that one window was missed
            line.append({"present": "present", "partial": "partial", "absent": "-"}[cls["status"]]
                        + (f" ({'S' if cls['marked_start'] else '-'}{'E' if cls['marked_end'] else '-'})"
                           if cls["status"] != "absent" else ""))
        line += [row["present"], row["partial"], row["absent"], row["held"],
                 f"{row['rate'] * 100:.1f}"]
        writer.writerow(line)

    return out.getvalue()


def marks_csv(report: dict) -> str:
    """One row per class per student: the detail behind every figure in the roll."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    by_id = {c["session_id"]: c for c in report["classes"]}

    writer.writerow(["Student ID", "Name", "Class", "Date", "Session ID", "Status",
                     "Start marked", "End marked", "Marks", "Best score",
                     "First mark", "Last mark", "Modality", "Closest distance (m)",
                     "Biometrics enrolled"])
    for row in report["students"]:
        for cls in row["classes"]:
            meta = by_id.get(cls["session_id"], {})
            writer.writerow([
                row["student_id"], row["name"], meta.get("title", ""),
                (meta.get("starts_at") or "")[:10], cls["session_id"], cls["status"],
                "yes" if cls["marked_start"] else "no",
                "yes" if cls["marked_end"] else "no",
                cls["marks"], f"{cls['best_score']:.4f}" if cls["best_score"] else "",
                cls["first_marked_at"], cls["last_marked_at"],
                ",".join(cls["modalities"]), cls["closest_m"] or "",
                ",".join(row["enrolled_modalities"]) if row["biometrics_enrolled"] else "no",
            ])
    return out.getvalue()


def filename(report: dict, shape: str) -> str:
    course = report["course"]
    stamp = report["generated_at"][:10]
    semester = (course["semester"] or "").replace("/", "-")
    return f"{course['code']}_{semester}_{shape}_{stamp}.csv"
