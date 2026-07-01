"""FastAPI dependencies."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from astor.db.base import session_scope


def get_session() -> Iterator[Session]:
    """Yield a transactional session per request (reuses db.base.session_scope)."""
    with session_scope() as session:
        yield session
