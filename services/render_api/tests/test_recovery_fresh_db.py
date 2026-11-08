"""Full recovery of a fresh database from verified Drive delivery JSON plus a live Sheet snapshot."""

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import jobs
from app.models import Candidate, Dedupe, ExportBatch, Lead, PipelineState
from app.qualification import digest
from app.services import campaign, delivery, recovery

pytestmark = pytest.mark.integration
FIELDS = delivery.FIELDS
SHEET, FOLDER, CAMPAIGN = "sheet_test", "folder_test", "phase2"


def values(n, email, source="bluesky", url=None, status="DELIVERABLE_DOMAIN", created="2026-11-01T10:00:00+00:00"):
    row = dict.fromkeys(FIELDS, "")
    row.update(lead_id=f"{CAMPAIGN}:{n}", campaign_id=CAMPAIGN, email=email, source=source,
               source_url=url or f"https://bsky.app/profile/did:plc:p{n}/post/r{n}", validation_status=status,
               created_at=created)
    return [row[f] for f in FIELDS]


def item(n, tab, row, email, **kw):
    return {"lead_id": n, "tab": tab, "row": row, "values": values(n, email, **kw)}


def write_delivery(path, items, shard_rows=2, **overrides):
    envelope = {"batch_id": str(uuid.uuid4()), "checksum": delivery.checksum(items), "campaign_id": CAMPAIGN,
                "spreadsheet_id": SHEET, "drive_folder_id": FOLDER, "shard_rows": shard_rows,
                "fields": FIELDS, "items": items, **overrides}
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return path


def snapshot(tabs, captured=None, sheet=SHEET):
    return {"spreadsheet_sha256": hashlib.sha256(sheet.encode()).hexdigest(),
            "captured_at": (captured or datetime.now(UTC)).isoformat(), "tabs": tabs}


def configure(cfg, shard=2):
    jobs.initialize()
    cfg.final_delivery_enabled = True
    cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id = SHEET, FOLDER
    cfg.sheet_layout, cfg.sheet_shard_rows, cfg.export_batch_size = "alphabetical", shard, 200
    cfg.require_verified_destination = True
    cfg.campaign_id = CAMPAIGN


def add_lead(postgres, email, status="DELIVERABLE_DOMAIN", identity=None):
    with postgres() as db:
        ident = digest(identity or f"id-{email}-{status}")
        row = Candidate(identity_hash=ident, source="bluesky", source_url="https://bsky.app/x", excerpt="x",
                        contacts=[], product_type="MOTOR", score=95, status="VALIDATED")
        db.add(row)
        db.flush()
        db.add(Lead(candidate_id=row.id, email=email or None, email_hash=digest(email) if email else None,
                    identity_hash=ident, source="bluesky", source_url="https://bsky.app/x", email_source_url="",
                    excerpt="x", product_type="MOTOR", score=95, rank_score=9.5, profile={"username": "kim.bsky.social"},
                    validation_status=status))


def receipt(batch):
    return {"batch_id": batch["batch_id"], "checksum": batch["checksum"], "spreadsheet_id": batch["spreadsheet_id"],
            "drive_folder_id": batch["drive_folder_id"], "drive_file_id": "drive_file", "drive_checksum": batch["checksum"],
            "placements": [{k: i[k] for k in ("lead_id", "tab", "row")} for i in batch["items"]]}


def sheet_fixture(tmp_path):
    """Google holds legacy VALIDATED_001, A_001 full, A_002, OTHER_001 and a PROSPECTS_001 row."""
    write_delivery(tmp_path / "lead-delivery-1.json", [
        item(1, "VALIDATED_001", 2, "legacy1@x.za"),
        item(2, "VALIDATED_001", 3, "x@example.invalid", source="synthetic", url="https://example.invalid/t",
             status="SYNTHETIC_TEST"),
    ])
    write_delivery(tmp_path / "lead-delivery-2.json", [
        item(3, "VALIDATED_A_001", 2, "+alice@x.za"),
        item(4, "VALIDATED_A_001", 3, "=amy@x.za"),
        item(5, "VALIDATED_A_002", 2, "ann@x.za"),
        item(6, "VALIDATED_OTHER_001", 2, "9lives@x.za", source="youtube", url="https://www.youtube.com/watch?v=v&lc=Ugc1"),
        item(7, "PROSPECTS_001", 2, "", status="NO_EMAIL"),
    ])
    return {"VALIDATED_001": 2, "VALIDATED_A_001": 2, "VALIDATED_A_002": 1, "VALIDATED_OTHER_001": 1,
            "PROSPECTS_001": 1, "SUMMARY": 20}


def test_fresh_database_refuses_claims_until_recovered(postgres, isolated_settings):
    configure(isolated_settings)
    add_lead(postgres, "anna@x.za")
    with pytest.raises(delivery.DeliveryError, match="destination_unverified"):
        delivery.claim()


def test_full_recovery_restores_hashes_positions_ids_and_progress(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    snap = snapshot(sheet_fixture(tmp_path))
    dry = recovery.recover([tmp_path], snap)
    assert dry["applied"] is False and dry["rows"] == 7 and dry["max_lead_id"] == 7
    assert dry["bucket_positions"] == {"A": 3, "OTHER": 1, "PROSPECTS": 1} and dry["next_position"] == 2
    assert dry["qualified_by_campaign"] == {CAMPAIGN: 5}  # synthetic and NO_EMAIL excluded
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Dedupe)) == 0  # dry run writes nothing
    done = recovery.recover([tmp_path], snap, apply=True)
    assert done["verified"] and done["insert_statements"] == math.ceil(done["keys"] / recovery.CHUNK)
    assert done["accepted_finals_after"] == 5  # progress toward the target survives replacement
    with postgres() as db:
        assert db.get(Dedupe, "email:" + digest("+alice@x.za")) and db.get(Dedupe, "email:" + digest("=amy@x.za"))
        assert db.get(Dedupe, "lead:" + digest("bluesky:did:plc:p7"))  # the prospect still dedupes the person
        assert not db.get(Dedupe, "email:" + digest("x@example.invalid"))
        batches = db.scalars(select(ExportBatch)).all()
        assert {b.status for b in batches} == {"ACKNOWLEDGED"} and all(b.items == [] for b in batches)  # no payloads
        assert sum(b.qualified_count for b in batches) == 5
    add_lead(postgres, "abe@x.za")
    add_lead(postgres, None, status="NO_EMAIL", identity="new-prospect")
    with postgres() as db:
        assert min(lead.id for lead in db.scalars(select(Lead))) > 7  # IDs never reuse delivered ones
    batch = delivery.claim()
    places = {i["values"][0]: (i["tab"], i["row"]) for i in batch["items"]}
    assert sorted(places.values()) == [("PROSPECTS_001", 3), ("VALIDATED_A_002", 3)]
    with pytest.raises(recovery.RecoveryError, match="database_not_fresh"):
        recovery.recover([tmp_path], snap, apply=True)


@pytest.mark.parametrize("mutate,error", [
    (lambda tabs, path: (snapshot(tabs), []), "no_delivery_inputs"),
    (lambda tabs, path: (None, [path]), "no_destination_snapshot"),
    (lambda tabs, path: (snapshot({**tabs, "VALIDATED_A_001": 3}), [path]), "incomplete_coverage"),  # missing part
    (lambda tabs, path: (snapshot({**tabs, "VALIDATED_Z_001": 1}), [path]), "incomplete_coverage"),
    (lambda tabs, path: (snapshot({k: v for k, v in tabs.items() if k != "PROSPECTS_001"}), [path]), "placement_outside"),
    (lambda tabs, path: (snapshot(tabs, captured=datetime.now(UTC) - timedelta(hours=3)), [path]), "snapshot_not_current"),
    (lambda tabs, path: (snapshot(tabs, sheet="another"), [path]), "snapshot_destination_mismatch"),
])
def test_incomplete_stale_or_empty_inputs_are_rejected(postgres, isolated_settings, tmp_path, mutate, error):
    configure(isolated_settings)
    tabs = sheet_fixture(tmp_path)
    snap, inputs = mutate(tabs, tmp_path)
    with pytest.raises(recovery.RecoveryError, match=error):
        recovery.recover(inputs, snap)


@pytest.mark.parametrize("override,error", [
    ({"fields": []}, "fields"),  # checksum covers items only: an emptied schema must not pass
    ({"fields": list(reversed(FIELDS))}, "fields"),
    ({"campaign_id": "other"}, "identifier_mismatch"),
    ({"batch_id": "not-a-uuid"}, "batch_id"),
    ({"checksum": "0" * 64}, "checksum"),
])
def test_tampered_envelopes_are_rejected(postgres, isolated_settings, tmp_path, override, error):
    configure(isolated_settings)
    tabs = sheet_fixture(tmp_path)
    path = tmp_path / "lead-delivery-2.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(override)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match=error):
        recovery.recover([tmp_path], snapshot(tabs))


def test_value_bounds_and_lead_id_consistency(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    long = item(1, "VALIDATED_L_001", 2, "lee@x.za")
    long["values"][FIELDS.index("source_evidence")] = "x" * 3000
    write_delivery(tmp_path / "lead-delivery-1.json", [long])
    with pytest.raises(recovery.RecoveryError, match="values"):
        recovery.recover([tmp_path], snapshot({"VALIDATED_L_001": 1}))
    wrong = item(2, "VALIDATED_L_001", 2, "lee@x.za")
    wrong["lead_id"] = 99
    write_delivery(tmp_path / "lead-delivery-1.json", [wrong])
    with pytest.raises(recovery.RecoveryError, match="identifier_mismatch"):
        recovery.recover([tmp_path], snapshot({"VALIDATED_L_001": 1}))


def test_conflicting_placements_are_rejected(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    tabs = sheet_fixture(tmp_path)
    write_delivery(tmp_path / "lead-delivery-3.json", [item(9, "VALIDATED_A_001", 2, "other@x.za")])
    with pytest.raises(recovery.RecoveryError, match="conflicting_placement"):
        recovery.recover([tmp_path], snapshot(tabs))


def test_large_restore_is_processed_in_bounded_chunks(postgres, isolated_settings, tmp_path):
    configure(isolated_settings, shard=5000)
    letters = [chr(c) for c in range(ord("a"), ord("z") + 1)]
    counts, n = {}, 0
    for batch in range(500):  # 100,000 delivered rows across 500 delivery files
        items = []
        for _ in range(200):
            n += 1
            letter = letters[n % 26]
            pos = counts.get(letter.upper(), 0)
            counts[letter.upper()] = pos + 1
            items.append(item(n, f"VALIDATED_{letter.upper()}_{pos // 5000 + 1:03d}", pos % 5000 + 2, f"{letter}{n}@x.za"))
        write_delivery(tmp_path / f"lead-delivery-{batch:04d}.json", items, shard_rows=5000)
    result = recovery.recover([tmp_path], snapshot({f"VALIDATED_{b}_001": c for b, c in counts.items()}), apply=True)
    assert result["rows"] == 100_000 and result["keys"] == 300_000 and result["receipts"] == 500
    assert result["insert_statements"] == 300 and result["verified"]  # 1,000-key bulk inserts
    assert result["accepted_finals_after"] == 100_000 and result["max_lead_id"] == 100_000


def test_no_email_and_synthetic_rows_never_count_and_prospects_stay_out_of_email_tabs(postgres, isolated_settings):
    configure(isolated_settings)
    isolated_settings.require_verified_destination = False
    add_lead(postgres, "real@x.za")
    add_lead(postgres, None, status="NO_EMAIL")
    add_lead(postgres, "test@example.invalid", status="SYNTHETIC_TEST")
    with postgres() as db:
        state = db.get(PipelineState, 1)
        assert campaign.accepted_finals(db, state) == 1 and campaign.no_email_prospects(db, state) == 1
    batch = delivery.claim()
    tabs = {i["values"][FIELDS.index("validation_status")]: i["tab"] for i in batch["items"]}
    assert tabs == {"DELIVERABLE_DOMAIN": "VALIDATED_R_001", "NO_EMAIL": "PROSPECTS_001",
                    "SYNTHETIC_TEST": "VALIDATED_T_001"}
    delivery.acknowledge(receipt(batch))
    with postgres() as db:  # payloads deleted: counts survive through qualified_count
        state = db.get(PipelineState, 1)
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        assert campaign.accepted_finals(db, state) == 1 and campaign.no_email_prospects(db, state) == 2


def test_destination_approval_is_bound_to_exact_configuration(postgres, isolated_settings):
    configure(isolated_settings)
    with pytest.raises(delivery.DeliveryError, match="destination_not_confirmed"):
        delivery.confirm_empty_destination("another_sheet")
    delivery.confirm_empty_destination(SHEET)
    add_lead(postgres, "ben@x.za")
    isolated_settings.google_drive_backup_folder_id = "a_different_folder"
    with pytest.raises(delivery.DeliveryError, match="destination_unverified"):
        delivery.claim()  # approval was for another folder
    isolated_settings.google_drive_backup_folder_id = FOLDER
    isolated_settings.sheet_layout = "sequential"
    with pytest.raises(delivery.DeliveryError, match="destination_unverified"):
        delivery.claim()  # approval was for the alphabetical layout
    isolated_settings.sheet_layout = "alphabetical"
    assert delivery.claim()["items"][0]["tab"] == "VALIDATED_B_001"


def test_sequential_offset_adoption_is_refused_for_alphabetical_layout(postgres, isolated_settings):
    configure(isolated_settings)
    with pytest.raises(delivery.DeliveryError, match="adopt_destination_sequential_only"):
        delivery.adopt_destination(5)
