import pytest

from app import jobs
from app.models import PipelineState
from app.services import campaign


def test_campaign_start_persists_clock_and_drains_to_complete(postgres, isolated_settings):
    isolated_settings.campaign_id = "phase2-campaign-test"
    jobs.initialize()

    started = campaign.start(raw_target=3, duration_days=7)
    assert started["status"] == "RUNNING"
    assert started["campaign_id"] == "phase2-campaign-test"
    assert started["raw_target"] == 3
    assert started["raw_scanned"] == 0
    assert (started["acquisition_deadline"] - started["started_at"]).days == 7
    assert not started["paused"]

    with postgres() as db:
        state = db.get(PipelineState, 1)
        state.campaign_raw_scanned = 3
        assert campaign.observe(db, total_work_rows=1, claimed_batches=0).campaign_status == "DRAINING"

    with postgres() as db:
        assert campaign.observe(db, total_work_rows=0, claimed_batches=0).campaign_status == "FINALIZING"

    with postgres() as db:
        state = campaign.observe(db, total_work_rows=0, claimed_batches=0)
        assert state.campaign_status == "COMPLETE"
        assert state.campaign_completed_at is not None
        assert state.paused


def test_active_campaign_cannot_be_started_twice(postgres, isolated_settings):
    isolated_settings.campaign_id = "phase2-campaign-test"
    jobs.initialize()
    campaign.start(raw_target=10, duration_days=7)
    with pytest.raises(campaign.CampaignError, match="campaign_already_active"):
        campaign.start(raw_target=10, duration_days=7)
