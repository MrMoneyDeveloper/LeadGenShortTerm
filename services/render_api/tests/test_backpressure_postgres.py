from datetime import timedelta

import pytest
from sqlalchemy import delete

from app import jobs
from app.models import Candidate, PipelineState, SourceRecord, now
from app.services import backpressure

pytestmark = pytest.mark.integration


def test_queue_hysteresis_and_database_pressure(postgres, isolated_settings):
    jobs.initialize()
    isolated_settings.queue_high_water = 3
    isolated_settings.queue_low_water = 1
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        for i in range(3):
            db.add(Candidate(source="fixture", source_url="https://example.org", identity_hash=str(i),
                             excerpt="fixture", contacts=[], product_type="MOTOR", score=80))
    with postgres() as db:
        assert backpressure.inspect(db, database_bytes=100)["mode"] == "drain"
        db.execute(delete(Candidate).where(Candidate.identity_hash == "2"))
    with postgres() as db:
        assert backpressure.inspect(db, database_bytes=100)["mode"] == "drain"
        db.execute(delete(Candidate).where(Candidate.identity_hash == "1"))
    with postgres() as db:
        assert backpressure.inspect(db, database_bytes=100)["mode"] == "collect"
        assert backpressure.inspect(db, database_bytes=800_000_000)["mode"] == "storage_pressure"
    with postgres() as db:
        assert backpressure.inspect(db, database_bytes=650_000_000)["mode"] == "storage_pressure"
        assert backpressure.inspect(db, database_bytes=500_000_000)["mode"] == "collect"
    with postgres() as db:
        state = db.get(PipelineState, 1)
        state.campaign_status = "RUNNING"
        state.campaign_deadline = now() - timedelta(seconds=1)
        assert backpressure.inspect(db, database_bytes=100)["mode"] == "drain"
        db.execute(delete(Candidate))
    with postgres() as db:
        assert backpressure.inspect(db, database_bytes=100)["campaign"]["status"] == "FINALIZING"
    with postgres() as db:
        assert backpressure.inspect(db, database_bytes=100)["mode"] == "campaign_complete"


def test_tick_does_not_starve_processing_with_empty_stages_or_cleanup(postgres, isolated_settings):
    jobs.initialize()
    isolated_settings.processing_enabled = True
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        db.add(SourceRecord(source="fixture", source_record_id="one", source_url="https://example.org",
                            identity_hash="i", content_hash="c", text="irrelevant", published_at=now()))
    jobs.schedule_tick()
    assert jobs.run_next()["result"] == {"normalized": 1}
    jobs.schedule_tick()
    assert "raw_deleted" in jobs.run_next()["result"]
    jobs.schedule_tick()
    assert jobs.run_next()["status"] == "idle"
