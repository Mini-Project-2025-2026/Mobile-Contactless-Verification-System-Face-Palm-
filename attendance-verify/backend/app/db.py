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
_pool: dict = {} if _sqlite else {
    "pool_pre_ping": True,
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_max_overflow,
    "pool_recycle": 1800,
}
engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    connect_args=_connect_args,
    **_pool,
)


def init_db() -> None:
    """Create tables, then bring an older database up to the current shape."""
    from . import migrate, models  # noqa: F401  (importing models registers tables)

    SQLModel.metadata.create_all(engine)
    migrate.run(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
