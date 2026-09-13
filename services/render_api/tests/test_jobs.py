import pytest

from app import jobs
from app.models import PipelineState


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
