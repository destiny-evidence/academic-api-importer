from __future__ import annotations

import json
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

from filelock import AsyncReadWriteLock
from sqlalchemy import Engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

from academic_apis.utils.config import settings
from academic_apis.utils.types import DictLikeEncoder, Mode

from .schemas import AccessToken, Query, Task, User, UserCreate, UserPublic, Wrapper

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from sqlite3 import Connection

WRITE_LOCK_TIMEOUT = 5
settings.db_lock.parent.mkdir(parents=True, exist_ok=True)
_write_lock = AsyncReadWriteLock(settings.db_lock, timeout=WRITE_LOCK_TIMEOUT)


@lru_cache(maxsize=1)
def make_engine() -> Engine:
    settings.db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{settings.db_file}",
        connect_args={"check_same_thread": False, "timeout": WRITE_LOCK_TIMEOUT},
        poolclass=NullPool,  # each connection is opened/closed per checkout; no long-lived pooled state per process
        json_serializer=lambda obj: json.dumps(obj, cls=DictLikeEncoder),
        echo=settings.db_verbose,
    )

    SQLModel.metadata.create_all(engine)

    @event.listens_for(engine, "connect")
    def _set_pragmas(conn: Connection, _record: object) -> None:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={WRITE_LOCK_TIMEOUT * 1000}")
        cur.close()

    return engine


@asynccontextmanager
async def db_session(mode: Mode = Mode.READ) -> AsyncGenerator[Session]:
    engine = make_engine()
    session = Session(bind=engine, autocommit=False, autoflush=False)

    try:
        if mode is Mode.WRITE:
            await _write_lock.acquire_write(timeout=WRITE_LOCK_TIMEOUT)
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        await _write_lock.release(force=True)


__all__ = [
    "AccessToken",
    "Mode",
    "Query",
    "Task",
    "User",
    "UserCreate",
    "UserPublic",
    "Wrapper",
    "db_session",
]
