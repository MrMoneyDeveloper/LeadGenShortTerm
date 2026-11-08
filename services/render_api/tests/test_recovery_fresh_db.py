"""Full recovery of a fresh database from verified Drive delivery JSON, and target accounting."""

import json
import math

import pytest
from sqlalchemy import func, select

from app import jobs
from app.models import Candidate, Dedupe, Lead, PipelineState
from app.qualification import digest
from app.services import campaign, delivery, recovery

pytestmark = pytest.mark.integration
FIELDS = delivery.FIELDS
SHEET, FOLDER = "sheet_test", "folder_test"


def values(email, source="bluesky", url="https://bsky.app/profile/did:plc:p1/post/r1", status="DELIVERABLE_DOMAIN",
           lead="c:1"):
    row = dict.fromkeys(FIELDS, "")
    row.update(lead_id=lead, campaign_id="c", email=email, source=source, source_url=url, validation_status=status)
    return [row[f] for f in FIELDS]


def write_delivery(path, items, shard_rows=5000):
    envelope = {"batch_id": path.stem, "checksum": delivery.checksum(items), "campaign_id": "c",
                "spreadsheet_id": SHEET, "drive_folder_id": FOLDER, "shard_rows": shard_rows,
                "fields": FIELDS, "items": items}
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return path


def item(n, tab, row, vals):
    return {"lead_id": n, "tab": tab, "row": row, "values": vals}


def configure(cfg):
    jobs.initialize()
    cfg.final_delivery_enabled = True
    cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id = SHEET, FOLDER
    cfg.sheet_layout, cfg.sheet_shard_rows, cfg.export_batch_size = "alphabetical", 2, 200
    cfg.require_verified_destination = True


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
    """Google already holds: legacy VALIDATED_001 (2 rows), A_001 full, A_002 one row, OTHER_001 one row."""
    write_delivery(tmp_path / "lead-delivery-1.json", [
        item(1, "VALIDATED_001", 2, values("legacy1@x.za", lead="c:1")),
        item(2, "VALIDATED_001", 3, values("x@example.invalid", source="synthetic", url="https://example.invalid/t",
                                           status="SYNTHETIC_TEST", lead="c:2")),
    ], shard_rows=2)
    write_delivery(tmp_path / "lead-delivery-2.json", [
        item(3, "VALIDATED_A_001", 2, values("+alice@x.za", url="https://bsky.app/profile/did:plc:a1/post/r1", lead="c:3")),
        item(4, "VALIDATED_A_001", 3, values("=amy@x.za", url="https://bsky.app/profile/did:plc:a2/post/r2", lead="c:4")),
        item(5, "VALIDATED_A_002", 2, values("", url="https://bsky.app/profile/did:plc:a3/post/r3", status="NO_EMAIL",
                                             lead="c:5")),
        item(6, "VALIDATED_OTHER_001", 2, values("9lives@x.za", source="youtube",
                                                 url="https://www.youtube.com/watch?v=v&lc=Ugc1", lead="c:6")),
    ], shard_rows=2)
    return {"VALIDATED_001": 2, "VALIDATED_A_001": 2, "VALIDATED_A_002": 1, "VALIDATED_OTHER_001": 1}


def test_fresh_database_refuses_claims_until_recovered(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    add_lead(postgres, "anna@x.za")
    with pytest.raises(delivery.DeliveryError, match="destination_unverified"):
        delivery.claim()


def test_full_recovery_restores_positions_hashes_and_continues_after_occupied_rows(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    tabs = sheet_fixture(tmp_path)
    dry = recovery.recover([tmp_path], tabs)
    assert dry["applied"] is False and dry["rows"] == 6
    assert dry["bucket_positions"] == {"A": 3, "OTHER": 1} and dry["next_position"] == 2
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Dedupe)) == 0  # dry run writes nothing
    done = recovery.recover([tmp_path], tabs, apply=True)
    assert done["verified"] and done["insert_statements"] == math.ceil(done["keys"] / recovery.CHUNK)
    with postgres() as db:
        # Lossless: formula-prefixed emails are hashed exactly as delivered, not CSV-escaped.
        assert db.get(Dedupe, "email:" + digest("+alice@x.za")) and db.get(Dedupe, "email:" + digest("=amy@x.za"))
        assert db.get(Dedupe, "lead:" + digest("bluesky:did:plc:a3"))  # NO_EMAIL row still dedupes the person
        assert db.get(Dedupe, "record:" + digest("youtube:Ugc1"))
        assert not db.get(Dedupe, "email:" + digest("x@example.invalid"))  # synthetic rows create no hashes
        state = db.get(PipelineState, 1)
        assert state.export_destination_verified and state.export_layout == "alphabetical"
    # New leads continue after the last occupied Google rows (shard size 2): A_002 row 3, OTHER_001 row 3.
    add_lead(postgres, "abe@x.za")
    add_lead(postgres, "8ball@x.za")
    batch = delivery.claim()
    places = {i["values"][7]: (i["tab"], i["row"]) for i in batch["items"]}
    assert places == {"abe@x.za": ("VALIDATED_A_002", 3), "8ball@x.za": ("VALIDATED_OTHER_001", 3)}
    with pytest.raises(recovery.RecoveryError, match="database_not_fresh"):
        recovery.recover([tmp_path], tabs, apply=True)


@pytest.mark.parametrize("mutate,error", [
    (lambda tabs, path: (tabs, []), "no_delivery_inputs"),
    (lambda tabs, path: ({}, [path]), "no_expected_tab_rows"),
    (lambda tabs, path: ({**tabs, "VALIDATED_A_001": 3}, [path]), "incomplete_coverage"),  # a missing part
    (lambda tabs, path: ({**tabs, "VALIDATED_Z_001": 1}, [path]), "incomplete_coverage"),
    (lambda tabs, path: ({k: v for k, v in tabs.items() if k != "VALIDATED_OTHER_001"}, [path]), "placement_outside"),
])
def test_incomplete_or_empty_inputs_are_rejected(postgres, isolated_settings, tmp_path, mutate, error):
    configure(isolated_settings)
    tabs = sheet_fixture(tmp_path)
    expected, inputs = mutate(tabs, tmp_path)
    with pytest.raises(recovery.RecoveryError, match=error):
        recovery.recover(inputs, expected)


def test_tampered_or_conflicting_files_are_rejected(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    tabs = sheet_fixture(tmp_path)
    path = tmp_path / "lead-delivery-2.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][0]["values"][7] = "changed@x.za"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="checksum_mismatch"):
        recovery.recover([tmp_path], tabs)
    sheet_fixture(tmp_path)
    write_delivery(tmp_path / "lead-delivery-3.json", [item(9, "VALIDATED_A_001", 2, values("other@x.za"))], 2)
    with pytest.raises(recovery.RecoveryError, match="conflicting_placement"):
        recovery.recover([tmp_path], tabs)


def test_large_restore_is_processed_in_bounded_chunks(postgres, isolated_settings, tmp_path):
    configure(isolated_settings)
    isolated_settings.sheet_shard_rows = 5000
    letters = [chr(c) for c in range(ord("a"), ord("z") + 1)]
    counts, files, n = {}, 0, 0
    for batch in range(500):  # 100,000 delivered rows across 500 delivery files
        items = []
        for _ in range(200):
            letter = letters[n % 26]
            bucket = letter.upper()
            pos = counts.get(bucket, 0)
            counts[bucket] = pos + 1
            items.append(item(n + 1, f"VALIDATED_{bucket}_{pos // 5000 + 1:03d}", pos % 5000 + 2,
                              values(f"{letter}{n}@x.za", url=f"https://bsky.app/profile/did:plc:u{n}/post/p{n}",
                                     lead=f"c:{n + 1}")))
            n += 1
        write_delivery(tmp_path / f"lead-delivery-{batch:04d}.json", items)
        files += 1
    tabs = {f"VALIDATED_{b}_001": c for b, c in counts.items()}
    result = recovery.recover([tmp_path], tabs, apply=True)
    assert result["rows"] == 100_000 and result["keys"] == 300_000  # email + record + lead per row
    assert result["insert_statements"] == 300 and result["verified"]  # 1,000-row bulk inserts
    assert result["bucket_positions"] == counts


def test_no_email_and_synthetic_rows_never_count_toward_the_target(postgres, isolated_settings):
    configure(isolated_settings)
    isolated_settings.require_verified_destination = False
    add_lead(postgres, "real@x.za")
    add_lead(postgres, None, status="NO_EMAIL")
    add_lead(postgres, "test@example.invalid", status="SYNTHETIC_TEST")
    with postgres() as db:
        state = db.get(PipelineState, 1)
        assert campaign.accepted_finals(db, state) == 1
        assert campaign.no_email_prospects(db, state) == 1
    batch = delivery.claim()
    delivery.acknowledge(receipt(batch))
    with postgres() as db:  # payloads deleted: counts survive through the batch's qualified_count
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        state = db.get(PipelineState, 1)
        assert campaign.accepted_finals(db, state) == 1
        assert campaign.no_email_prospects(db, state) == 2  # NO_EMAIL + synthetic delivered, reported apart


def test_confirm_empty_destination_is_explicit(postgres, isolated_settings):
    configure(isolated_settings)
    with pytest.raises(delivery.DeliveryError, match="destination_not_confirmed"):
        delivery.confirm_empty_destination("another_sheet")
    assert delivery.confirm_empty_destination(SHEET) == {"export_destination_verified": True}
    add_lead(postgres, "ben@x.za")
    assert delivery.claim()["items"][0]["tab"] == "VALIDATED_B_001"
