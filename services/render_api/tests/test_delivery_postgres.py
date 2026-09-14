import copy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app import jobs
from app.main import app
from app.models import Candidate, Classification, Dedupe, ExportBatch, Lead, PipelineState, now
from app.services import delivery, pipeline

pytestmark = pytest.mark.integration


def setup(postgres, cfg, count=3):
    jobs.initialize()
    cfg.final_delivery_enabled = True
    cfg.google_spreadsheet_id = "test_sheet"
    cfg.google_drive_backup_folder_id = "test_folder"
    cfg.export_batch_size = 2
    cfg.sheet_shard_rows = 2
    with postgres() as db:
        for i in range(count):
            identity = f"{i:064d}"
            candidate = Candidate(
                identity_hash=identity, source="youtube", source_url="https://example.org/source",
                excerpt="fixture", contacts=[], product_type="MOTOR", score=95, status="VALIDATED",
                created_at=now() - timedelta(days=90),
            )
            db.add(candidate)
            db.flush()
            db.add(Classification(candidate_id=candidate.id, provider="rules", version="fixture", result={}))
            db.add(Lead(
                candidate_id=candidate.id, email=f"fixture{i}@example.org", email_hash=identity,
                identity_hash=identity, source="youtube", source_url="https://example.org/source",
                email_source_url="https://example.org/source", excerpt="fixture", product_type="MOTOR", score=95,
                rank_score=9.5, profile={"display_name": "=not a formula \u2014 \U0001f600"},
            ))
            for prefix in ("email:", "lead:", "candidate:"):
                db.add(Dedupe(key=prefix + identity))


def receipt(batch):
    return {
        "batch_id": batch["batch_id"], "checksum": batch["checksum"],
        "spreadsheet_id": batch["spreadsheet_id"], "drive_folder_id": batch["drive_folder_id"],
        "drive_file_id": "verified_drive_delivery_file", "drive_checksum": batch["checksum"],
        "placements": [{k: item[k] for k in ("lead_id", "tab", "row")} for item in batch["items"]],
    }


def test_claim_stable_after_restart_and_ack_drains_only_verified_batch(postgres, isolated_settings):
    setup(postgres, isolated_settings)
    first = delivery.claim()
    assert first["shard_rows"] == 2
    assert first["items"][0]["row"] == 2
    assert first["items"][1]["row"] == 3
    assert first["checksum"] == delivery.checksum(first["items"])
    assert first == delivery.claim()  # JSONB round trip reconstructs JS key order.
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(ExportBatch)) == 1
        assert db.scalar(select(func.count()).select_from(Lead)) == 3
    pipeline.cleanup(20)  # Expired candidate payload must remain while final delivery is pending.
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 3
    ack = receipt(first)
    assert delivery.acknowledge(ack)["deleted_leads"] == 2
    assert delivery.acknowledge(ack)["replayed"]
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 1
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1
        assert db.scalar(select(func.count()).select_from(Classification)) == 1
        batch = db.get(ExportBatch, first["batch_id"])
        assert batch.items == [] and batch.fields == []
        assert batch.ack_digest and batch.drive_file_id
        assert db.get(Dedupe, "email:" + "0" * 64)
        assert db.get(Dedupe, "lead:" + "0" * 64)
        assert db.get(Dedupe, "candidate:" + "0" * 64) is None
    second = delivery.claim()
    assert second["shard_rows"] == 2
    assert second["items"][0]["tab"] == "VALIDATED_002"
    assert second["items"][0]["row"] == 2
    delivery.acknowledge(receipt(second))
    assert delivery.claim() == {"status": "empty", "items": []}


@pytest.mark.parametrize("profile,expected", [
    ({"first_name": "Mohammed", "name_reliable": True, "name_source": "explicit_given_name"}, "Mohammed"),
    ({"first_name": "Thandi", "name_reliable": True, "name_source": "operator_verified"}, "Thandi"),
    ({"display_name": "Mohammed Smith"}, ""),
    ({"first_name": "Mohammed", "name_reliable": False, "name_source": "explicit_given_name"}, ""),
    ({"first_name": "Mohammed", "name_reliable": True, "name_source": "model_guess"}, ""),
    ({"first_name": "=IMPORTXML(x)", "name_reliable": True, "name_source": "operator_verified"}, ""),
    ({"first_name": "Mohammed", "name_reliable": True, "name_source": "operator_verified", "business_name": "Example"}, ""),
])
def test_final_payload_personalization_and_evidence(postgres, isolated_settings, profile, expected):
    setup(postgres, isolated_settings, count=1)
    with postgres() as db:
        lead = db.scalar(select(Lead))
        lead.profile = profile
        lead.excerpt = "Public request for a motor insurance quote."
    batch = delivery.claim()
    row = dict(zip(batch["fields"], batch["items"][0]["values"], strict=True))
    assert row["first_name"] == expected
    assert row["salutation"] == (f"Good day {expected}," if expected else "Good day,")
    assert row["source_evidence"] == "Public request for a motor insurance quote."
    assert row["email_source_url"] == row["source_url"]
    assert batch == delivery.claim()


def test_shard_size_cannot_change_after_first_allocation(postgres, isolated_settings):
    setup(postgres, isolated_settings)
    delivery.claim()
    isolated_settings.sheet_shard_rows = 3
    with pytest.raises(delivery.DeliveryError, match="destination_changed"):
        delivery.claim()


@pytest.mark.parametrize("field,value", [
    ("checksum", "0" * 64), ("drive_checksum", "0" * 64),
    ("spreadsheet_id", "wrong_sheet"), ("drive_folder_id", "wrong_folder"),
    ("placements", []),
])
def test_invalid_ack_cannot_delete_data(postgres, isolated_settings, field, value):
    setup(postgres, isolated_settings)
    batch = delivery.claim()
    ack = receipt(batch)
    ack[field] = value
    with pytest.raises(delivery.DeliveryError):
        delivery.acknowledge(ack)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 3
        assert db.get(ExportBatch, batch["batch_id"]).status == "CLAIMED"


def test_atomic_ack_rollback_and_destination_binding(postgres, isolated_settings, monkeypatch):
    setup(postgres, isolated_settings)
    batch = delivery.claim()
    original = delivery.metric

    def broken_metric(*args):
        if args[2] == "delivery_acknowledged":
            raise RuntimeError("simulated interruption after payload deletion")
        return original(*args)

    monkeypatch.setattr(delivery, "metric", broken_metric)
    with pytest.raises(RuntimeError):
        delivery.acknowledge(receipt(batch))
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 3
        assert db.get(ExportBatch, batch["batch_id"]).status == "CLAIMED"
    isolated_settings.google_spreadsheet_id = "another_sheet"
    with pytest.raises(delivery.DeliveryError, match="destination_changed"):
        delivery.claim()


def test_delivery_endpoints_enforce_auth_schema_and_conflicting_replay(postgres, isolated_settings):
    setup(postgres, isolated_settings)
    isolated_settings.dashboard_api_token = SecretStr("r" * 40)
    isolated_settings.processor_trigger_token = SecretStr("w" * 40)
    headers = {"Authorization": "Bearer " + "w" * 40}
    with TestClient(app) as client:
        assert client.post("/exports/claim", json={}).status_code == 401
        assert client.post("/exports/claim", json={}, headers={"Authorization": "Bearer " + "r" * 40}).status_code == 401
        assert client.post("/exports/claim", json={"limit": 201}, headers=headers).status_code == 422
        batch = client.post("/exports/claim", json={}, headers=headers).json()
        ack = receipt(batch)
        assert client.post("/exports/ack", json=ack, headers=headers).status_code == 200
        changed = copy.deepcopy(ack)
        changed["drive_file_id"] = "different_file"
        assert client.post("/exports/ack", json=changed, headers=headers).status_code == 409
        with postgres() as db:
            assert db.get(PipelineState, 1).paused  # Export can drain while collection is paused.
