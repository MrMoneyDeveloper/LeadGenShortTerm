from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings

# Executor fencing: the epoch this execution context owns (None outside run_next).
FENCE_EPOCH: ContextVar[int | None] = ContextVar("fence_epoch", default=None)


class StaleExecutor(Exception):
    """A newer executor took over; this (stale) execution must not commit anything."""


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "after_begin")
def _check_fence(_session, _transaction, connection):
    epoch = FENCE_EPOCH.get()
    if epoch is None:
        return
    # FOR SHARE: a takeover's epoch increment waits for this transaction; after takeover, the
    # stale executor's next transaction sees a different epoch and is rejected before any write.
    current = connection.execute(text("SELECT epoch FROM executor_fence WHERE id = 1 FOR SHARE")).scalar()
    if current != epoch:
        raise StaleExecutor("executor_fenced")


@lru_cache
def engine():
    url = settings().database_url.get_secret_value()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("PostgreSQL with psycopg is required")
    return create_engine(
        url, pool_pre_ping=True, pool_size=5, max_overflow=3, hide_parameters=True, connect_args={"connect_timeout": 10}
    )


@contextmanager
def session():
    with Session(engine(), expire_on_commit=False) as db:
        with db.begin():
            yield db


@contextmanager
def advisory_lock(key):
    # Dedicated connection: locks release automatically on connection/process loss.
    with engine().connect() as conn:
        acquired = conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    conn.commit()
                except Exception:  # noqa: BLE001 - lost connection already released the lock
                    pass
