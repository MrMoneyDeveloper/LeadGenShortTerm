from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings


class Base(DeclarativeBase):
    pass


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
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                conn.commit()
