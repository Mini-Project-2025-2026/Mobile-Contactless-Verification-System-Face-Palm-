"""Read-only inspection of a live database before a migration runs against it.

`app.migrate` is idempotent and mostly additive, but one step is not: it deletes
rows that would block the unique indexes (a second attendance row for the same
student in the same class, a repeated course registration, a replayed
signature), keeping the lowest id. That is the right repair — the duplicates are
meaningless by construction — but "the right repair" and "nothing to repair" are
very different things to deploy on a Friday, and only the database can say which
one this is.

So: run this first. It writes nothing.

    DATABASE_URL=... python -m tools.preflight

It reports what exists, what the migration will add, and — the number that
actually matters — how many rows the deduplication step would remove.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, inspect, text

#: (table, columns) that are about to become unique.
_UNIQUE = (
    ("attendance", ("session_id", "student_id")),
    ("enrollment", ("student_id", "course_id")),
    ("attendancemark", ("sig_nonce",)),
)

#: (table, column) added after the first deploy.
_COLUMNS = (
    ("student", "enroll_device_uid"),
    ("student", "programme_key"),
    ("student", "active"),
    ("session", "phase"),
    ("attendancemark", "phase"),
    ("course", "archived"),
)


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    engine = create_engine(url)
    problems = 0
    try:
        with engine.connect() as conn:
            tables = set(inspect(engine).get_table_names())
            print(f"tables present: {len(tables)}")

            print("\nrow counts")
            for table in sorted(tables):
                total = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"  {table:<22} {total}")

            print("\ncolumns the migration will add")
            for table, column in _COLUMNS:
                if table not in tables:
                    print(f"  {table}.{column:<22} (table is new)")
                    continue
                have = {c["name"] for c in inspect(engine).get_columns(table)}
                print(f"  {table}.{column:<22} {'already there' if column in have else 'WILL BE ADDED'}")

            print("\nrows the deduplication step would DELETE")
            for table, cols in _UNIQUE:
                if table not in tables:
                    print(f"  {table:<22} (table is new)")
                    continue
                key = ", ".join(cols)
                extra = conn.execute(text(
                    f"SELECT COALESCE(SUM(n - 1), 0) FROM "
                    f"(SELECT COUNT(*) AS n FROM {table} GROUP BY {key}) AS grouped "
                    f"WHERE n > 1"
                )).scalar() or 0
                problems += int(extra)
                flag = "" if not extra else "   <-- these rows will be removed"
                print(f"  {table:<22} {extra}{flag}")

            print("\nwhat a student would notice")
            if not problems:
                print("  nothing. The destructive step is a no-op on this data.")
            else:
                print(f"  {problems} duplicate row(s) removed, oldest kept in each group.")
                print("  No attendance is lost: a duplicate is a second row for a mark")
                print("  the surviving row already records.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
