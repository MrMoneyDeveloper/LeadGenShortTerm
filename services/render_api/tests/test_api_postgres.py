from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import app
from app.models import Job, Lead, PipelineState

pytestmark = pytest.mark.integration

READ = "r" * 40
WRITE = "w" * 40


@pytest.fixture
def client(postgres, isolated_settings):
    isolated_settings.environment = "test"
    isolated_settings.dashboard_api_token = SecretStr(READ)
    isolated_settings.processor_trigger_token = SecretStr(WRITE)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client, postgres, isolated_settings


def auth(token):
    return {"Authorization": "Bearer " + token}


def test_health_auth_controls_and_invalid_input(client):
    api, postgres, cfg = client
    assert api.get("/health").status_code == 200
    assert api.get("/dashboard/summary").status_code == 401
    assert api.get("/dashboard/summary", headers=auth("bad")).status_code == 401
    summary = api.get("/dashboard/summary", headers=auth(READ))
    assert summary.status_code == 200 and summary.json()["environment"] == "test"
    assert summary.json()["paused"] is True
    assert api.post("/admin/pipeline/resume", headers=auth(READ)).status_code == 401
    assert api.post("/admin/pipeline/resume", headers=auth(WRITE)).status_code == 200
    assert api.post("/admin/sources/bluesky/enable", headers=auth(WRITE)).status_code == 200
    assert api.post("/admin/sources/unknown/enable", headers=auth(WRITE)).status_code == 422
    assert (
        api.post("/jobs/process", headers={**auth(WRITE), "Idempotency-Key": "short"}, json={}).status_code == 422
    )
    cfg.processing_enabled = True
    queued = api.post(
        "/jobs/process",
        headers={**auth(WRITE), "Idempotency-Key": "phase2-api-job-0001"},
        json={"stage": "normalize", "batch_size": 2},
    )
    assert queued.status_code == 202 and queued.json()["status"] == "QUEUED"
    repeated = api.post(
        "/jobs/process",
        headers={**auth(WRITE), "Idempotency-Key": "phase2-api-job-0001"},
        json={"stage": "normalize", "batch_size": 2},
    )
    assert repeated.json()["id"] == queued.json()["id"]
    conflict = api.post(
        "/jobs/process",
        headers={**auth(WRITE), "Idempotency-Key": "phase2-api-job-0001"},
        json={"stage": "validate", "batch_size": 2},
    )
    assert conflict.status_code == 409
    assert api.get("/jobs/does-not-exist", headers=auth(READ)).status_code == 404
    assert api.post("/admin/pipeline/pause", headers=auth(WRITE)).json()["paused"] is True
    blocked = api.post(
        "/jobs/process",
        headers={**auth(WRITE), "Idempotency-Key": "phase2-api-job-0002"},
        json={"stage": "normalize"},
    )
    assert blocked.status_code == 409
    cleanup = api.post(
        "/jobs/cleanup", headers={**auth(WRITE), "Idempotency-Key": "phase2-cleanup-0001"}, json={"batch_size": 2}
    )
    assert cleanup.status_code == 202


def test_export_pagination_and_retry_api(client):
    api, postgres, _ = client
    with postgres() as db:
        for i in range(3):
            db.add(
                Lead(
                    email=f"person{i}@example.org",
                    email_hash=f"email{i}",
                    identity_hash=f"identity{i}",
                    source="synthetic",
                    source_url=f"https://example.org/{i}",
                    email_source_url=f"https://example.org/{i}",
                    excerpt="'=formula-safe",
                    product_type="MOTOR",
                    score=90,
                )
            )
        failed = Job(
            idempotency_key="failed-job-api",
            kind="normalize",
            source=None,
            batch_size=1,
            status="FAILED",
            completed_at=datetime.now(UTC),
        )
        db.add(failed)
        db.flush()
        failed_id = failed.id
    page1 = api.get("/exports/validated?format=json&limit=2", headers=auth(READ)).json()
    assert len(page1["items"]) == 2 and page1["next_after"] is not None and page1["through"] >= 3
    page2 = api.get(
        f"/exports/validated?format=json&limit=2&after={page1['next_after']}&through={page1['through']}",
        headers=auth(READ),
    ).json()
    assert len(page2["items"]) == 1 and page2["next_after"] is None
    csv_response = api.get("/exports/validated?format=csv&limit=2", headers=auth(READ))
    assert csv_response.status_code == 200 and csv_response.headers["x-export-through"]
    assert "'=formula-safe" in csv_response.text
    retry = api.post(f"/admin/jobs/{failed_id}/retry", headers=auth(WRITE))
    assert retry.status_code == 200 and retry.json()["status"] == "QUEUED"
    assert api.post(f"/admin/jobs/{failed_id}/retry", headers=auth(WRITE)).status_code == 409
    assert api.get("/dashboard/source-stats", headers=auth(READ)).status_code == 200
    assert api.get("/dashboard/queue", headers=auth(READ)).status_code == 200
    assert api.get("/dashboard/failures", headers=auth(READ)).status_code == 200
    assert api.get("/exports/source-stats", headers=auth(READ)).status_code == 200
    assert api.get("/exports/processing-summary", headers=auth(READ)).status_code == 200


def test_process_next_background_completes_job(client):
    api, postgres, cfg = client
    cfg.processing_enabled = True
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
    job = api.post(
        "/jobs/process",
        headers={**auth(WRITE), "Idempotency-Key": "phase2-background-0001"},
        json={"stage": "normalize", "batch_size": 1},
    ).json()
    assert api.post("/jobs/process-next", headers=auth(WRITE)).status_code == 202
    status = api.get(f"/jobs/{job['id']}", headers=auth(READ)).json()
    assert status["status"] == "COMPLETE" and status["result"] == {"normalized": 0}
