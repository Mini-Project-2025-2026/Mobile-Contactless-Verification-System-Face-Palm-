"""Database engine and session helpers."""
from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=_connect_args)


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
        if table in tables and col not in {c["name"] for c in insp.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

    add_col("student", "enroll_device_uid", "enroll_device_uid VARCHAR DEFAULT ''")
    add_col("session", "phase", "phase VARCHAR DEFAULT 'start'")
    add_col("attendancemark", "phase", "phase VARCHAR DEFAULT 'start'")


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
