"""Database engine and session helpers."""
from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _sqlite else {}
# Several lecturers open classes at the same moment, and the pooled Postgres on
# the other side drops idle connections overnight. pre_ping trades one cheap
# round trip for never handing a dead connection to the first request of the day.
_pool = {} if _sqlite else {
    "pool_pre_ping": True,
    "pool_size": 10,
    "max_overflow": 20,
    "pool_recycle": 1800,
}
engine = create_engine(settings.database_url, echo=False, connect_args=_connect_args, **_pool)


def init_db() -> None:
    """Create tables. Import models first so they register on SQLModel.metadata."""
    from . import models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Lightweight additive migration: add columns introduced after first deploy.
    create_all() creates missing TABLES but never adds columns to existing ones."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    def add_col(table: str, col: str, ddl: str) -> None:
        if table not in tables or col in {c["name"] for c in insp.get_columns(table)}:
            return
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        except Exception:
            # Another worker added it between inspect and ALTER (or it already
            # exists). The column is present either way — safe to ignore.
            pass

    add_col("student", "enroll_device_uid", "enroll_device_uid VARCHAR DEFAULT ''")
    add_col("session", "phase", "phase VARCHAR DEFAULT 'start'")
    add_col("attendancemark", "phase", "phase VARCHAR DEFAULT 'start'")


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
