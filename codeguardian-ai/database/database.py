"""Database engine, session factory, and table management.

Provides:

- :data:`engine` — SQLAlchemy engine created from ``settings.database_url``.
- :class:`SessionLocal` — session factory for dependency injection.
- :func:`get_db` — FastAPI dependency yielding a session.
- :func:`init_db` — create all tables (idempotent).
- :func:`get_session` — context manager for ad-hoc sessions.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from database.models import Base

# ── Engine ──────────────────────────────────────────────────────────────
# SQLite needs ``check_same_thread=False`` so FastAPI's threadpool can
# share the connection.  For Postgres / other backends the arg is ignored.
_connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine: Engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    echo=False,
    future=True,
)

# ── Session factory ─────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def init_db() -> None:
    """Create all tables if they don't exist (idempotent).

    Call once at application startup.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a database session.

    Usage in a route::

        @app.get("/reviews/{review_id}")
        def get_review(review_id: int, db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager for non-FastAPI code needing a session.

    Usage::

        with get_session() as db:
            repo = get_or_create_repository(db, owner="x", name="y")
            db.commit()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
