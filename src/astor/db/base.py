"""Engine, session factory, and declarative base.

Stateless seam: callers obtain a short-lived Session per unit of work; no
application state lives in process memory, so the app tier scales horizontally
by simply running more instances.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from astor.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional unit of work."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
