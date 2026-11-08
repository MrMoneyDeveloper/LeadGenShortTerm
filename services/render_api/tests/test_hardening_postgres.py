"""Alphabetical delivery, final-lead target accounting, cross-source dedupe and quota isolation."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app import jobs
from app.main import app
from app.models import Candidate, Classification, Dedupe, ExportBatch, Lead, PipelineState, SourceRecord, now
from app.qualification import digest
from app.services import backpressure, campaign, delivery, pipeline

pytestmark = pytest.mark.integration


def destination(cfg, layout="alphabetical", shard=5000):
    jobs.initialize()
    cfg.final_delivery_enabled = True
    cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id = "sheet_test", "folder_test"
    cfg.sheet_layout, cfg.sheet_shard_rows, cfg.export_batch_size = layout, shard, 200


def add_leads(postgres, emails):
    with postgres() as db:
        for n, email in enumerate(emails):
            identity = digest(f"identity-{email}-{n}")
            row = Candidate(identity_hash=identity, source="bluesky", source_url="https://example.invalid/post",
                            excerpt="fixture", contacts=[], product_type="MOTOR", score=95, status="VALIDATED")
            db.add(row)
            db.flush()
            db.add(Lead(candidate_id=row.id, email=email, email_hash=digest(email), identity_hash=identity,
                        source="bluesky", source_url="https://example.invalid/post",
                        email_source_url="https://example.invalid/profile", excerpt="fixture", product_type="MOTOR",
                        score=95, rank_score=9.5, profile={}))
            db.add(Dedupe(key="email:" + digest(email)))
            db.add(Dedupe(key="lead:" + identity))


def receipt(batch):
    return {"batch_id": batch["batch_id"], "checksum": batch["checksum"], "spreadsheet_id": batch["spreadsheet_id"],
            "drive_folder_id": batch["drive_folder_id"], "drive_file_id": "verified_drive_file",
            "drive_checksum": batch["checksum"],
            "placements": [{k: item[k] for k in ("lead_id", "tab", "row")} for item in batch["items"]]}


def placements(batch):
    return {item["values"][7]: (item["tab"], item["row"]) for item in batch["items"]}  # values[7] = email


# --- alphabetical layout -----------------------------------------------------------------------

def test_bucket_rules():
    assert [delivery.bucket(e) for e in ["alice@x.za", "Zed@x.za", "9lives@x.za", "_x@x.za", "élan@x.za", ""]] == \
        ["A", "Z", "OTHER", "OTHER", "OTHER", "OTHER"]
    assert delivery.placement("alphabetical", 4999, 5000, "K") == ("VALIDATED_K_001", 5001)
    assert delivery.placement("alphabetical", 5000, 5000, "K") == ("VALIDATED_K_002", 2)
    assert delivery.placement("sequential", 5000, 5000) == ("VALIDATED_002", 2)


def test_alphabetical_buckets_other_and_small_shard_rollover(postgres, isolated_settings):
    destination(isolated_settings, shard=2)
    add_leads(postgres, ["alice@x.za", "amy@x.za", "anna@x.za", "bob@x.za", "7up@x.za", "zoe@x.za"])
    first = delivery.claim()
    assert first["layout"] == "alphabetical"
    assert placements(first) == {
        "alice@x.za": ("VALIDATED_A_001", 2), "amy@x.za": ("VALIDATED_A_001", 3), "anna@x.za": ("VALIDATED_A_002", 2),
        "bob@x.za": ("VALIDATED_B_001", 2), "7up@x.za": ("VALIDATED_OTHER_001", 2), "zoe@x.za": ("VALIDATED_Z_001", 2)}
    delivery.acknowledge(receipt(first))
    add_leads(postgres, ["adam@x.za", "8ball@x.za"])
    second = delivery.claim()
    # Durable per-bucket cursors continue; acknowledged rows are never reused or reordered.
    assert placements(second) == {"adam@x.za": ("VALIDATED_A_002", 3), "8ball@x.za": ("VALIDATED_OTHER_001", 3)}
    with postgres() as db:
        state = db.get(PipelineState, 1)
        assert state.export_bucket_positions == {"A": 4, "B": 1, "OTHER": 2, "Z": 1}
        assert state.export_next_position == 0 and state.export_layout == "alphabetical"
        status = delivery.shard_status(db)
    tabs = {t["tab"]: t["data_rows"] for t in status["tabs"]}
    assert tabs == {"VALIDATED_A_001": 2, "VALIDATED_A_002": 2, "VALIDATED_B_001": 1,
                    "VALIDATED_OTHER_001": 2, "VALIDATED_Z_001": 1}


def test_claim_replay_is_stable_and_lost_ack_does_not_duplicate(postgres, isolated_settings):
    destination(isolated_settings)
    add_leads(postgres, ["carl@x.za", "cara@x.za"])
    batch = delivery.claim()
    assert delivery.claim() == batch  # outstanding batch replayed, not reallocated
    body = receipt(batch)
    assert delivery.acknowledge(body)["replayed"] is False
    assert delivery.acknowledge(body)["replayed"] is True  # lost ACK response, same receipt
    assert delivery.claim() == {"status": "empty", "items": []}
    with postgres() as db:
        assert db.get(PipelineState, 1).export_bucket_positions == {"C": 2}
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0  # payload deleted only after ACK
        assert db.scalar(select(func.count()).select_from(Dedupe).where(Dedupe.key.like("email:%"))) == 2


def test_sequential_destination_is_never_switched_blindly(postgres, isolated_settings):
    destination(isolated_settings, layout="sequential")
    add_leads(postgres, ["dan@x.za"])
    batch = delivery.claim()
    assert batch["items"][0]["tab"] == "VALIDATED_001" and batch["layout"] == "sequential"
    delivery.acknowledge(receipt(batch))
    isolated_settings.sheet_layout = "alphabetical"
    add_leads(postgres, ["dora@x.za"])
    with pytest.raises(delivery.DeliveryError, match="final_layout_changed"):
        delivery.claim()
    with pytest.raises(delivery.DeliveryError, match="destination_not_confirmed"):
        delivery.adopt_layout("alphabetical", "some_other_sheet")
    switched = delivery.adopt_layout("alphabetical", "sheet_test")
    assert switched["previous_layout"] == "sequential" and switched["legacy_next_position"] == 1
    batch = delivery.claim()
    assert batch["items"][0]["tab"] == "VALIDATED_D_001" and batch["items"][0]["row"] == 2
    with pytest.raises(delivery.DeliveryError, match="claimed_delivery_pending"):
        delivery.adopt_layout("sequential", "sheet_test")
    with postgres() as db:
        assert db.get(PipelineState, 1).export_next_position == 1  # legacy cursor preserved
    with pytest.raises(delivery.DeliveryError, match="destination_already_in_use"):
        delivery.adopt_destination(5)


def test_ack_api_accepts_alphabetical_tabs_and_rejects_malformed(postgres, isolated_settings):
    destination(isolated_settings)
    isolated_settings.processor_trigger_token = SecretStr("p" * 40)
    isolated_settings.dashboard_api_token = SecretStr("d" * 40)
    add_leads(postgres, ["ella@x.za"])
    client = TestClient(app)
    auth = {"Authorization": "Bearer " + "p" * 40}
    batch = client.post("/exports/claim", json={}, headers=auth).json()
    bad = receipt(batch)
    bad["placements"][0]["tab"] = "VALIDATED_e_001"
    assert client.post("/exports/ack", json=bad, headers=auth).status_code == 422
    assert client.post("/exports/ack", json=receipt(batch), headers=auth).json()["status"] == "ACKNOWLEDGED"
    shards = client.get("/dashboard/shards", headers={"Authorization": "Bearer " + "d" * 40}).json()
    assert shards["layout"] == "alphabetical" and shards["tabs"][0]["tab"] == "VALIDATED_E_001"


def test_capacity_plan_for_100k_rows():
    uniform = {name: 100_000 // 27 + (1 if i < 100_000 % 27 else 0) for i, name in enumerate(delivery.BUCKETS)}
    assert sum(uniform.values()) == 100_000
    plan = delivery.capacity_plan(uniform, 5000)
    assert plan["tabs"] == 27 and plan["delivery_cells"] == (100_000 + 27) * 17
    assert plan["within_limit"] and plan["headroom_cells"] > 8_000_000
    skewed = delivery.capacity_plan({"A": 60_000, "OTHER": 40_000}, 5000)
    assert skewed["tabs"] == 20 and skewed["delivery_cells"] == (100_000 + 20) * 17
    assert not delivery.capacity_plan(uniform, 5000, existing_cells=9_000_000)["within_limit"]


# --- final-lead target accounting -----------------------------------------------------------------

def test_target_counts_pending_and_acknowledged_once_then_stops_acquisition(postgres, isolated_settings):
    destination(isolated_settings)
    isolated_settings.final_lead_target = 3
    isolated_settings.acquisition_enabled = isolated_settings.bluesky_enabled = True
    campaign.start(raw_target=1000, final_target=3)
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        from app.models import SourceCursor
        db.get(SourceCursor, "bluesky").enabled = True
        assert backpressure.inspect(db)["mode"] == "collect"
    add_leads(postgres, ["fay@x.za", "finn@x.za"])
    batch = delivery.claim()  # claimed but not yet acknowledged: still counted once
    with postgres() as db:
        assert campaign.accepted_finals(db, db.get(PipelineState, 1)) == 2
    delivery.acknowledge(receipt(batch))
    with postgres() as db:
        assert campaign.accepted_finals(db, db.get(PipelineState, 1)) == 2  # moved from pending to delivered
    with postgres() as db:
        db.add(SourceRecord(source="bluesky", source_record_id="raw-1", source_url="https://example.invalid/r",
                            identity_hash="r" * 64, content_hash="c" * 64, text="raw", published_at=now()))
    with postgres() as db:
        assert campaign.accepted_finals(db, db.get(PipelineState, 1)) == 2  # raw records never count
    add_leads(postgres, ["gus@x.za"])
    with postgres() as db:
        pressure = backpressure.inspect(db)
        assert pressure["final_target_reached"] and pressure["accepted_finals"] == 3
        assert pressure["mode"] == "drain" and pressure["collect_allowance"] == 0
        assert db.get(PipelineState, 1).campaign_status == "DRAINING"
    with pytest.raises(ValueError, match="pipeline_or_source_disabled"):
        jobs.enqueue("collect", "bluesky", "after-target")
    assert campaign.status()["finals_remaining"] == 0


def test_previous_campaign_deliveries_do_not_count_toward_a_new_campaign(postgres, isolated_settings):
    destination(isolated_settings)
    add_leads(postgres, ["hal@x.za"])
    delivery.acknowledge(receipt(delivery.claim()))
    isolated_settings.campaign_id = "next-campaign"
    started = campaign.start(final_target=5)
    assert started["accepted_finals"] == 0 and started["final_target"] == 5


# --- dedupe and quota isolation -------------------------------------------------------------------

def test_previously_delivered_email_is_rejected_from_another_source(postgres, isolated_settings, monkeypatch):
    destination(isolated_settings)
    add_leads(postgres, ["ivy@x.za"])
    delivery.acknowledge(receipt(delivery.claim()))
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))
    with postgres() as db:
        row = Candidate(source="youtube", source_url="https://example.invalid/yt", identity_hash=digest("other-identity"),
                        excerpt="Joburg need car insurance quote", product_type="MOTOR", score=95, status="VALIDATE",
                        contacts=[{"email": "ivy@x.za", "source_url": "https://example.invalid/yt"}], profile={})
        db.add(row)
        db.flush()
        db.add(Classification(candidate_id=row.id, provider="rules", version="fixture", result={
            "score": 95, "product_type": "MOTOR", "signals": ["south_africa", "quote"], "rejected": False}))
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 0  # dedupe survives delivery cleanup


def test_exhausted_groq_defers_semantic_work_without_stopping_other_stages(postgres, isolated_settings, monkeypatch):
    jobs.initialize()
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))
    retry = now() + timedelta(hours=6)

    def exhausted(*_args):
        raise pipeline.semantic.SemanticUnavailable("groq_daily_request_cap", retry)

    monkeypatch.setattr(pipeline.semantic, "classify", exhausted)
    with postgres() as db:
        for name, score, signals in [("ambiguous", 85, ["south_africa"]), ("direct", 100, ["south_africa", "quote"])]:
            row = Candidate(source="bluesky", source_url="https://example.invalid/" + name, identity_hash=digest(name),
                            excerpt=name, product_type="MOTOR", score=score, status="VALIDATE", profile={},
                            contacts=[{"email": name + "@x.za", "source_url": "https://example.invalid/" + name}])
            db.add(row)
            db.flush()
            db.add(Classification(candidate_id=row.id, provider="rules", version="fixture", result={
                "score": score, "product_type": "MOTOR", "signals": signals, "rejected": False}))
        db.add(SourceRecord(source="bluesky", source_record_id="q-1", source_url="https://example.invalid/q",
                            identity_hash="q" * 64, content_hash="w" * 64, text="weather only", published_at=now()))
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    assert pipeline.normalize_batch(5) == {"normalized": 1}  # deterministic stages keep running
    with postgres() as db:
        leads = {lead.email for lead in db.scalars(select(Lead))}
        waiting = db.scalar(select(Candidate).where(Candidate.identity_hash == digest("ambiguous")))
    assert leads == {"direct@x.za"}  # direct finals still flow while semantic capacity is exhausted
    assert waiting.status == "CLASSIFY" and waiting.attempts == 0 and waiting.available_at == retry
    with postgres() as db:  # the uncertain record was neither finalized nor counted toward the target
        assert campaign.accepted_finals(db, db.get(PipelineState, 1)) == 1


def test_export_batches_record_layout(postgres, isolated_settings):
    destination(isolated_settings)
    add_leads(postgres, ["jo@x.za"])
    delivery.claim()
    with postgres() as db:
        assert db.scalar(select(ExportBatch.layout)) == "alphabetical"


def test_lead_without_email_is_finalized_and_delivered_by_handle_bucket(postgres, isolated_settings, monkeypatch):
    destination(isolated_settings)
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    with postgres() as db:
        row = Candidate(source="bluesky", source_url="https://bsky.app/profile/x/post/1", identity_hash=digest("no-email"),
                        excerpt="Santam car insurance quote please", product_type="MOTOR", score=100, status="VALIDATE",
                        contacts=[], profile={"username": "kagiso.bsky.social", "profile_url": "https://bsky.app/profile/x"})
        db.add(row)
        db.flush()
        db.add(Classification(candidate_id=row.id, provider="rules", version="fixture", result={
            "score": 100, "product_type": "MOTOR", "signals": ["south_africa", "quote"], "rejected": False}))
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    with postgres() as db:
        lead = db.scalar(select(Lead))
        assert lead.email is None and lead.validation_status == "NO_EMAIL"
        assert lead.email_source_url == "https://bsky.app/profile/x"
        assert db.get(Dedupe, "lead:" + digest("no-email"))
    batch = delivery.claim()
    item = batch["items"][0]
    assert item["tab"] == "VALIDATED_K_001" and item["values"][7] == "" and item["values"][15] == "NO_EMAIL"
    delivery.acknowledge(receipt(batch))
    # The same identity can never be admitted twice.
    with postgres() as db:
        row = Candidate(source="bluesky", source_url="https://bsky.app/profile/x/post/2", identity_hash=digest("no-email"),
                        excerpt="again", product_type="MOTOR", score=100, status="CLASSIFY", contacts=[], profile={})
        db.add(row)
        db.flush()
        db.add(Classification(candidate_id=row.id, provider="rules", version="fixture", result={
            "score": 100, "product_type": "MOTOR", "signals": ["south_africa", "quote"], "rejected": False}))
    pipeline.classify_batch(5)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 0


def test_without_sa_evidence_no_email_lead_still_needs_semantic_pass(postgres, isolated_settings, monkeypatch):
    jobs.initialize()
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline.semantic, "classify", lambda *_a: (_ for _ in ()).throw(
        pipeline.semantic.SemanticUnavailable("groq_daily_request_cap", now() + timedelta(hours=1))))
    with postgres() as db:
        row = Candidate(source="bluesky", source_url="https://bsky.app/p", identity_hash=digest("us-person"),
                        excerpt="need car insurance quote", product_type="MOTOR", score=100, status="VALIDATE",
                        contacts=[], profile={})
        db.add(row)
        db.flush()
        db.add(Classification(candidate_id=row.id, provider="rules", version="fixture", result={
            "score": 100, "product_type": "MOTOR", "signals": ["quote"], "rejected": False}))
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 0  # no SA evidence: not a final
        assert db.scalar(select(Candidate.status)) == "CLASSIFY"  # waits for semantic capacity
