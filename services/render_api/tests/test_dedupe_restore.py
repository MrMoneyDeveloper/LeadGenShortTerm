"""Restoring compact dedupe hashes from the finished Google dataset (fresh/replaced database)."""

import csv

import pytest
from sqlalchemy import select

from app.models import Candidate, Classification, Dedupe, Lead
from app.qualification import digest
from app.services import dedupe_restore, pipeline

pytestmark = pytest.mark.integration
FIELDS = ["lead_id", "campaign_id", "email", "source", "source_url", "validation_status"]


def export(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_restore_rebuilds_collector_hashes_and_blocks_readmission(postgres, isolated_settings, tmp_path, monkeypatch):
    did = "did:plc:abc123"
    path = export(tmp_path / "VALIDATED.csv", [
        {"lead_id": "c:1", "campaign_id": "c", "email": "Person@Example.co.za", "source": "bluesky",
         "source_url": f"https://bsky.app/profile/{did}/post/3kxyz", "validation_status": "DELIVERABLE_DOMAIN"},
        {"lead_id": "c:2", "campaign_id": "c", "email": "", "source": "youtube",
         "source_url": "https://www.youtube.com/watch?v=vid&lc=Ugcomment1", "validation_status": "NO_EMAIL"},
        {"lead_id": "c:3", "campaign_id": "c", "email": "x@example.invalid", "source": "synthetic",
         "source_url": "https://example.invalid/t", "validation_status": "SYNTHETIC_TEST"},
    ])
    dry = dedupe_restore.restore([path])
    assert dry == {"rows": 2, "keys": 4, "by_kind": {"email": 1, "record": 2, "lead": 1},
                   "already_present": 0, "applied": False}
    with postgres() as db:
        assert db.scalar(select(Dedupe.key).limit(1)) is None  # dry run writes nothing
    applied = dedupe_restore.restore([path], apply=True)
    assert applied["inserted"] == 4 and applied["verified"]
    assert dedupe_restore.restore([path], apply=True)["inserted"] == 0  # idempotent
    # Exactly the keys the collector/pipeline compute.
    with postgres() as db:
        assert db.get(Dedupe, "record:" + digest(f"bluesky:{did}/3kxyz"))
        assert db.get(Dedupe, "lead:" + digest(f"bluesky:{did}"))
        assert db.get(Dedupe, "email:" + digest("person@example.co.za"))
        assert db.get(Dedupe, "record:" + digest("youtube:Ugcomment1"))
        # The same Bluesky account found again after the database replacement is not re-admitted.
        row = Candidate(source="bluesky", source_url="https://bsky.app/x", identity_hash=digest(f"bluesky:{did}"),
                        excerpt="Santam quote", product_type="MOTOR", score=100, status="CLASSIFY", contacts=[], profile={})
        db.add(row)
        db.flush()
        db.add(Classification(candidate_id=row.id, provider="rules", version="f", result={
            "score": 100, "product_type": "MOTOR", "signals": ["south_africa", "quote"], "rejected": False}))
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    pipeline.classify_batch(5)
    with postgres() as db:
        assert db.scalar(select(Lead.id).limit(1)) is None


def test_restore_rejects_files_that_are_not_lead_exports(postgres, tmp_path):
    bad = tmp_path / "other.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing source_url"):
        dedupe_restore.restore([bad])
