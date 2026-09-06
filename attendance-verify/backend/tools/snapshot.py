"""Dump every row of a database to one JSON file, before something changes it.

`pg_dump` is the right tool and is not installed on every machine that ever
needs to deploy this. This is the small honest substitute: it reads every table
through SQLAlchemy and writes them to a single file that `tools/restore.py` can
put back. It is not a substitute for real backups at any size — it holds the
whole database in memory — but for an institution's first semesters it is the
difference between "we can undo that" and "we cannot".

    DATABASE_URL=... python -m tools.snapshot            # writes snapshot-<ts>.json
    DATABASE_URL=... python -m tools.snapshot out.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, Table, MetaData


def _plain(value):
    """JSON cannot hold a datetime, and str() of one round-trips through SQL."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(sys.argv[1] if len(sys.argv) > 1 else f"snapshot-{stamp}.json")

    engine = create_engine(url)
    payload: dict = {"taken_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    try:
        metadata = MetaData()
        for name in sorted(inspect(engine).get_table_names()):
            table = Table(name, metadata, autoload_with=engine)
            with engine.connect() as conn:
                rows = [
                    {k: _plain(v) for k, v in row._mapping.items()}
                    for row in conn.execute(select(table))
                ]
            payload["tables"][name] = rows
            print(f"  {name:<22} {len(rows)}")
    finally:
        engine.dispose()

    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    total = sum(len(r) for r in payload["tables"].values())
    print(f"\n{total} rows from {len(payload['tables'])} tables -> {out} "
          f"({out.stat().st_size / 1024:.1f} KB)")
    print("Keep this somewhere that is not the machine you are deploying from.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
