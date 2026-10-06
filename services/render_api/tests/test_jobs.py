from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app import jobs
from app.models import PipelineState


@pytest.mark.parametrize("acquisition,source_enabled,expected", [
    (False, False, ["cleanup"]),
    (False, True, ["cleanup"]),
    (True, False, ["cleanup"]),
    (True, True, ["cleanup", "collect"]),
])
def test_tick_skips_disabled_stage_database_work(monkeypatch, isolated_settings, acquisition, source_enabled, expected):
    isolated_settings.processing_enabled = False
    isolated_settings.acquisition_enabled = acquisition
    isolated_settings.bluesky_enabled = source_enabled
    isolated_settings.youtube_enabled = False
    db = MagicMock()
    db.scalar.return_value = None
    sessions = []

    @contextmanager
    def fake_session():
        sessions.append(True)
        yield db

    advance = MagicMock()
    enqueue = MagicMock()
    monkeypatch.setattr(jobs, "session", fake_session)
    monkeypatch.setattr(jobs.backpressure, "inspect", advance)
    monkeypatch.setattr(jobs, "enqueue", enqueue)
    jobs.schedule_tick()
    advance.assert_called_once_with(db)
    assert [call.args[0] for call in enqueue.call_args_list] == expected
    assert len(sessions) == 1 + len(expected)


@pytest.mark.integration
def test_paused_default_and_idempotency(postgres, isolated_settings):
    jobs.initialize()
    with pytest.raises(ValueError, match="disabled"):
        jobs.enqueue("normalize", None, "test-disabled")
    isolated_settings.processing_enabled = True
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
    one = jobs.enqueue("normalize", None, "same-key-123", 5)
    two = jobs.enqueue("normalize", None, "same-key-123", 5)
    assert one.id == two.id
    with pytest.raises(ValueError, match="conflict"):
        jobs.enqueue("validate", None, "same-key-123", 5)
