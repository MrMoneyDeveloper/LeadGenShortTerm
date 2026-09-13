"""Phase-2-only tests. Importing tests cannot read the operator's private .env."""

import os

import pytest

from app.config import Settings, settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    cfg = Settings(
        _env_file=None,
        database_url="",
        processor_trigger_token="",
        dashboard_api_token="",
        acquisition_enabled=False,
        processing_enabled=False,
        bluesky_enabled=False,
        youtube_enabled=False,
        grok_enabled=False,
        scheduler_enabled=False,
        local_model_path="",
        xai_api_key="",
        youtube_api_key="",
    )
    monkeypatch.setattr("app.config.settings", lambda: cfg)
    # Modules intentionally import settings directly; replace each already-loaded binding.
    import sys

    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "settings", None) is settings:
            monkeypatch.setattr(module, "settings", lambda: cfg)
    yield cfg


@pytest.fixture
def postgres(isolated_settings, monkeypatch):
    """Explicit disposable database only; each test owns an isolated schema."""
    import uuid
    from contextlib import contextmanager

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app import models  # noqa: F401
    from app.database import Base

    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database in Phase 2")
    if url == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must be separate from DATABASE_URL")
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    eng = create_engine(url, hide_parameters=True, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001_phase1')"))

    @contextmanager
    def local_session():
        with Session(eng, expire_on_commit=False) as db, db.begin():
            yield db

    @contextmanager
    def local_advisory_lock(key):
        with eng.connect() as conn:
            acquired = conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            conn.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    conn.commit()

    import sys

    from app.database import advisory_lock, session

    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "session", None) is session:
            monkeypatch.setattr(module, "session", local_session)
        if name.startswith("app.") and getattr(module, "advisory_lock", None) is advisory_lock:
            monkeypatch.setattr(module, "advisory_lock", local_advisory_lock)
    try:
        yield local_session
    finally:
        eng.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
