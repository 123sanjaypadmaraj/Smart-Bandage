"""SQLAlchemy engine/session setup (Phase 4)."""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Phase 4 uses create_all for a from-scratch dev DB. A real deployment
    # swaps this for Alembic migrations before Phase 8/9 touch real data.
    from backend.app import models  # noqa: F401 - ensure models are registered

    Base.metadata.create_all(bind=engine)
