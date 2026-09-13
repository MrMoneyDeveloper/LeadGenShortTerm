from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import jobs
from app.models import Failure, Job, PipelineState, now

pytestmark = pytest.mark.integration


def enable_processing(postgres, isolated_settings):
    isolated_settings.processing_enabled = True
    with postgres() as db:
        db.add(PipelineState(id=1, paused=False))


def test_abandoned_running_job_is_reclaimed(postgres, isolated_settings, monkeypatch):
    enable_processing(postgres, isolated_settings)
    with postgres() as db:
        job = Job(idempotency_key="abandoned", kind="normalize", source=None, batch_size=2, status="RUNNING")
        db.add(job)
        db.flush()
        job_id = job.id
    monkeypatch.setattr(jobs.pipeline, "normalize_batch", lambda limit: {"normalized": limit})
    assert jobs.run_next()["status"] == "complete"
    with postgres() as db:
        row = db.get(Job, job_id)
        assert row.status == "COMPLETE" and row.attempts == 1 and row.result == {"normalized": 2}


def test_failures_backoff_stop_and_manual_retry(postgres, isolated_settings, monkeypatch):
    enable_processing(postgres, isolated_settings)
    isolated_settings.job_max_attempts = 2
    with postgres() as db:
        job = Job(idempotency_key="fails", kind="normalize", source=None, batch_size=1)
        db.add(job)
        db.flush()
        job_id = job.id

    def fail(_limit):
        raise RuntimeError("sensitive detail must not persist")

    monkeypatch.setattr(jobs.pipeline, "normalize_batch", fail)
    assert jobs.run_next()["status"] == "retry_or_failed"
    with postgres() as db:
        row = db.get(Job, job_id)
        assert row.status == "QUEUED" and row.result == {"error_code": "RuntimeError"}
        row.available_at = now() - timedelta(seconds=1)
    assert jobs.run_next()["status"] == "retry_or_failed"
    with postgres() as db:
        row = db.get(Job, job_id)
        assert row.status == "FAILED" and row.attempts == 2
        codes = list(db.scalars(select(Failure.code)))
        assert codes == ["RuntimeError", "RuntimeError"]
        assert all("sensitive" not in code for code in codes)
        assert db.scalar(select(func.count()).select_from(Job)) == 1
