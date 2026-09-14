from datetime import UTC, datetime

import pytest

from app.collectors.profiles import bluesky_profile, profile, youtube_profile
from app.ranking import rank


def test_youtube_public_profile_does_not_infer_handle_business_or_email():
    record = youtube_profile({"id": "one", "snippet": {
        "publishedAt": datetime.now(UTC).isoformat(), "authorDisplayName": "Alex &amp; Co",
        "authorChannelId": {"value": "UCpublic"}, "authorChannelUrl": "https://youtube.com/channel/UCpublic",
        "textOriginal": "need insurance", "private_data": "never retained",
    }}, "video-one")
    canonical = profile(record)
    assert canonical["display_name"] == "Alex & Co"
    assert canonical["business_name"] == canonical["username"] == ""
    assert canonical["first_name"] == canonical["name_source"] == "" and not canonical["name_reliable"]
    assert canonical["metadata"] == {"video_id": "video-one", "comment_id": "one", "channel_id": "UCpublic"}
    assert "private_data" not in str(canonical)
    assert record.url.endswith("video-one&lc=one")


def test_bluesky_profile_preserves_links_but_does_not_invent_names():
    record = bluesky_profile({"did": "did:plc:public", "commit": {
        "rkey": "post", "collection": "app.bsky.feed.post", "record": {
            "text": "Need car insurance", "createdAt": datetime.now(UTC).isoformat(),
            "facets": [{"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://example.org"}]}],
            "embed": {"external": {"uri": "javascript:bad"}},
        },
    }})
    record.metadata["secret"] = "must not be retained"
    canonical = profile(record)
    assert canonical["urls"] == ["https://example.org"]
    assert canonical["display_name"] == canonical["username"] == ""
    assert "secret" not in canonical["metadata"]
    assert record.identity == "did:plc:public"
    assert canonical["first_name"] == "" and not canonical["name_reliable"]


def test_given_name_requires_reliable_structured_provenance():
    from app.collectors.base import Record

    row = Record("one", "mohammed", "text", "https://example.org", datetime.now(UTC),
                 display_name="Mohammed Smith", first_name="Mohammed", name_reliable=True)
    assert profile(row)["first_name"] == ""
    row.name_source = "display_name"
    assert not profile(row)["name_reliable"]
    row.name_source = "explicit_given_name"
    assert profile(row)["first_name"] == "Mohammed"
    assert profile(row)["name_reliable"]
    row.name_source = "operator_verified"
    assert profile(row)["first_name"] == "Mohammed"
    row.first_name = "person@example.org"
    assert profile(row)["first_name"] == ""
    row.first_name, row.name_reliable = "Mohammed", False
    assert profile(row)["first_name"] == ""


@pytest.mark.parametrize(("score", "route"), [(20, "REJECT"), (50, "DEFERRED"), (80, "SEMANTIC"), (100, "DIRECT_FINAL")])
def test_rank_routes_are_configurable_ordinal_scores(score, route):
    result = rank({"score": score, "product_type": "MOTOR", "signals": ["south_africa"]}, True)
    assert result["route"] == route
    assert 0 <= result["rank_score"] <= 10


def test_rank_hard_gates_and_confident_local_negative():
    result = {"score": 100, "product_type": "MOTOR", "signals": ["south_africa"]}
    assert rank(result, False)["route"] != "DIRECT_FINAL"
    assert rank({**result, "signals": []}, True)["route"] != "DIRECT_FINAL"
    assert rank({**result, "product_type": "UNKNOWN"}, True)["route"] != "DIRECT_FINAL"
    assert rank(result, True, {"label": "ADVERTISEMENT", "confidence": 0.99})["route"] == "REJECT"
