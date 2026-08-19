"""Schema catch-up for a database that predates the current models.

`SQLModel.metadata.create_all()` creates missing *tables* and nothing else: it
will not add a column to a table that already exists, and it will not add the
unique indexes that enforce this application's core rules. A deployment running
since before those rules existed therefore keeps its old shape forever, in
silence, which is the worst way to find out.

Deliberately a small idempotent catch-up rather than a migration framework:
every step checks the live schema first and is safe to run on every boot, on
SQLite and on Postgres alike. A step that cannot apply (because existing rows
violate a rule about to be enforced) repairs the rows first where the repair is
unambiguous, and is reported — never skipped in silence.
"""
from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

log = logging.getLogger("attendance.migrate")

#: (table, column, DDL) added after the first deploy.
_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("student", "enroll_device_uid", "enroll_device_uid VARCHAR DEFAULT ''"),
    ("student", "programme_key", "programme_key VARCHAR DEFAULT ''"),
    ("student", "active", "active BOOLEAN DEFAULT 1"),
    ("session", "phase", "phase VARCHAR DEFAULT 'start'"),
    ("attendancemark", "phase", "phase VARCHAR DEFAULT 'start'"),
    ("course", "archived", "archived BOOLEAN DEFAULT 0"),
)

#: (index name, table, columns) — the rules the database itself should keep.
_UNIQUE_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("uq_attendance_session_student", "attendance", ("session_id", "student_id")),
    ("uq_enrollment_student_course", "enrollment", ("student_id", "course_id")),
    ("uq_attendancemark_sig_nonce", "attendancemark", ("sig_nonce",)),
)

#: (index name, table, columns) — plain indexes for the lookups that got slow.
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_attendance_session", "attendance", ("session_id",)),
    ("ix_mark_attendance", "attendancemark", ("attendance_id",)),
    ("ix_enrollment_course", "enrollment", ("course_id",)),
    ("ix_session_course_active", "session", ("course_id", "active")),
    ("ix_student_programme_key", "student", ("programme_key",)),
)


def run(engine: Engine) -> None:
    """Bring an existing database up to the current shape. Safe to call always."""
    tables = set(inspect(engine).get_table_names())
    for table, column, ddl in _COLUMNS:
        _add_column(engine, tables, table, column, ddl)
    _backfill_programme_key(engine, tables)
    _dedupe(engine, tables, "attendance", ("session_id", "student_id"))
    _dedupe(engine, tables, "enrollment", ("student_id", "course_id"))
    _dedupe(engine, tables, "attendancemark", ("sig_nonce",))
    for name, table, cols in _UNIQUE_INDEXES:
        _create_index(engine, tables, name, table, cols, unique=True)
    for name, table, cols in _INDEXES:
        _create_index(engine, tables, name, table, cols, unique=False)


def _add_column(engine: Engine, tables: set[str], table: str, column: str, ddl: str) -> None:
    if table not in tables:
        return  # create_all() just made it, with the column already on it
    if column in {c["name"] for c in inspect(engine).get_columns(table)}:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        log.info("migrate: added %s.%s", table, column)
    except Exception as exc:  # another worker won the race, or it already exists
        log.debug("migrate: could not add %s.%s (%s)", table, column, exc)


def _backfill_programme_key(engine: Engine, tables: set[str]) -> None:
    """Fill the normalised programme column for rows written before it existed."""
    if "student" not in tables:
        return
    from .models import norm_programme

    try:
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, programme FROM student "
                "WHERE (programme_key IS NULL OR programme_key = '') AND programme <> ''"
            )).all()
            for row_id, programme in rows:
                conn.execute(
                    text("UPDATE student SET programme_key = :key WHERE id = :id"),
                    {"key": norm_programme(programme or ""), "id": row_id},
                )
        if rows:
            log.info("migrate: backfilled programme_key for %d students", len(rows))
    except Exception as exc:
        log.warning("migrate: programme_key backfill skipped (%s)", exc)


def _dedupe(engine: Engine, tables: set[str], table: str, cols: tuple[str, ...]) -> None:
    """Delete rows that would block a unique index, keeping the lowest id.

    Only ever runs against tables whose duplicates are meaningless by
    construction: a second attendance row for the same student in the same
    class, a repeated course registration, a replayed signature. The surviving
    row is the original, so nothing a student earned is thrown away.
    """
    if table not in tables:
        return
    key = ", ".join(cols)
    try:
        with engine.begin() as conn:
            removed = conn.execute(text(
                f"DELETE FROM {table} WHERE id NOT IN "
                f"(SELECT MIN(id) FROM {table} GROUP BY {key})"
            )).rowcount
        if removed:
            log.warning("migrate: removed %d duplicate row(s) from %s (%s)", removed, table, key)
    except Exception as exc:
        log.warning("migrate: dedupe of %s skipped (%s)", table, exc)


def _create_index(engine: Engine, tables: set[str], name: str, table: str,
                  cols: tuple[str, ...], *, unique: bool) -> None:
    if table not in tables:
        return
    if name in {ix["name"] for ix in inspect(engine).get_indexes(table)}:
        return
    kind = "UNIQUE INDEX" if unique else "INDEX"
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE {kind} IF NOT EXISTS {name} ON {table} ({', '.join(cols)})"))
        log.info("migrate: created %s %s", kind.lower(), name)
    except Exception as exc:
        # A unique index that will not build means the data still violates the
        # rule. Say so: the alternative is a constraint everyone assumes is on.
        (log.error if unique else log.debug)(
            "migrate: could not create %s on %s (%s)", name, table, exc)
