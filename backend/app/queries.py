"""Query helpers for the two shapes this app kept getting wrong.

**Counting by loading.** `len(db.exec(select(Student)).all())` builds every row
of a table in Python to learn how many there are. On the overview screen that
was five tables at once, on every refresh.

**Filtering by loading.** The end-of-semester report read every Attendance row
and every AttendanceMark row in the database and then discarded the ones
belonging to other courses. That works on a laptop with one course seeded and
gets slower every week the institution uses it — the cost of one lecturer's
report grows with everybody else's attendance.

Both are fixed by asking the database the question instead. `fetch_in` exists
because the fix has a trap: SQLite refuses a statement with more than 999 bound
parameters, so an `IN` list built from a large cohort fails in production and
not in any test with ten students in it.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, TypeVar

from sqlalchemy import func
from sqlmodel import Session, select

T = TypeVar("T")

#: SQLite's compiled default is 999 bound parameters per statement. Postgres
#: allows far more, but one conservative number keeps both correct.
_MAX_BOUND_PARAMS = 900


def count(db: Session, model: type, *where) -> int:
    """How many rows match, asked as a count rather than measured by loading."""
    statement = select(func.count()).select_from(model)
    for condition in where:
        statement = statement.where(condition)
    return int(db.exec(statement).one())


def fetch_in(db: Session, model: type[T], column: Any, values: Iterable[Any]) -> list[T]:
    """`SELECT ... WHERE column IN values`, split into safe-sized batches."""
    unique = list(dict.fromkeys(values))
    if not unique:
        return []
    rows: list[T] = []
    for batch in chunks(unique, _MAX_BOUND_PARAMS):
        rows.extend(db.exec(select(model).where(column.in_(batch))).all())
    return rows


def chunks(values: Sequence[Any], size: int = _MAX_BOUND_PARAMS) -> Iterable[Sequence[Any]]:
    """Split a sequence into batches no bigger than `size`."""
    for start in range(0, len(values), size):
        yield values[start:start + size]


def count_by(db: Session, model: type, group_column: Any, *where) -> dict[Any, int]:
    """One grouped count instead of a query per group.

    The console listed courses and asked each one "how many students?", and
    listed students and asked each one "how many courses?" — a query per row of
    whatever was on screen. Both are a single GROUP BY.
    """
    statement = select(group_column, func.count()).select_from(model)
    for condition in where:
        statement = statement.where(condition)
    statement = statement.group_by(group_column)
    return {key: int(total) for key, total in db.exec(statement).all()}


def collect_by(db: Session, model: type, key_column: Any, value_column: Any,
               *where) -> dict[Any, list[Any]]:
    """Two columns, gathered into {key: [values]} in one pass."""
    statement = select(key_column, value_column).select_from(model)
    for condition in where:
        statement = statement.where(condition)
    out: dict[Any, list[Any]] = {}
    for key, value in db.exec(statement).all():
        out.setdefault(key, []).append(value)
    return out
