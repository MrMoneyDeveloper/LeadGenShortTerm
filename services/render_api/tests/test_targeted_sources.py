"""Targeted Bluesky search, YouTube recency/relevance filters, public-bio contacts and sample store."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.collectors import bluesky, youtube
from app.collectors.profiles import bluesky_post_profile, profile

NOW = datetime.now(UTC)


def post(n, text, did=None, age_days=0):
    did = did or f"did:plc:author{n}"
    return {
        "uri": f"at://{did}/app.bsky.feed.post/rkey{n}",
        "author": {"did": did, "handle": f"user{n}.bsky.social", "displayName": f"Display {n}"},
        "record": {"text": text, "createdAt": (NOW - timedelta(days=age_days)).isoformat().replace("+00:00", "Z"),
                   "langs": ["en"]},
    }


class Fake:
    """Scripted Bluesky AppView."""

    def __init__(self, pages, bios=None, status=200):
        self.pages, self.bios, self.status, self.calls = pages, bios or {}, status, []

    def __call__(self, request):
        self.calls.append((request.url.path, dict(request.url.params)))
        if self.status != 200:
            return httpx.Response(self.status, headers={"ratelimit-reset": "0"}, json={})
        if request.url.path.endswith("getProfiles"):
            actors = request.url.params.get_list("actors")
            return httpx.Response(200, json={"profiles": [
                {"did": a, "description": self.bios.get(a, "")} for a in actors]})
        key = (request.url.params["q"], request.url.params.get("cursor", ""))
        return httpx.Response(200, json=self.pages.get(key, {"posts": []}))


@pytest.fixture
def search(monkeypatch):
    cfg = {"mode": "search", "queries": ['"car insurance"', '"funeral cover"'], "max_requests_per_batch": 10,
           "pages_per_query": 2, "refresh_hours": 6, "overlap_hours": 1, "request_delay_seconds": 0,
           "fetch_profiles": True}
    monkeypatch.setattr(bluesky, "rules", lambda name: {"bluesky": cfg} if name == "sources" else {"max_age_days": 30})
    monkeypatch.setattr(bluesky.time, "sleep", lambda _s: None)

    def install(fake):
        real = httpx.Client
        monkeypatch.setattr(bluesky.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(fake), **kw))
        return fake

    return cfg, install


def test_search_pages_filters_attaches_bios_and_schedules_refresh(search, isolated_settings):
    _cfg, install = search
    fake = install(Fake({
        ('"car insurance"', ""): {"posts": [post(1, "Need car insurance quote in Joburg"),
                                            post(2, "lovely weather today")], "cursor": "c2"},
        ('"car insurance"', "c2"): {"posts": [post(3, "Which insurer for car insurance?")]},
        ('"funeral cover"', ""): {"posts": [post(4, "Looking for funeral cover for my parents")]},
    }, bios={"did:plc:author1": "Contact: person.one@example.co.za | Durban"}))
    result = bluesky.Bluesky().collect({}, 500)
    assert result.scanned == 4
    assert [r.source_record_id for r in result.records] == [
        "did:plc:author1/rkey1", "did:plc:author3/rkey3", "did:plc:author4/rkey4"]
    assert result.records[0].bio.startswith("Contact:") and result.records[1].bio == ""
    assert result.stop_reason == "discovery_complete" and result.exhausted
    state = result.cursor
    assert state["q"] == 0 and state["refresh_at"] and not state["pass_started"]
    searches = [p for path, p in fake.calls if path.endswith("searchPosts")]
    assert all(p["sort"] == "latest" and p["since"] for p in searches)
    assert searches[1]["cursor"] == "c2"
    # Until refresh, no network calls are made.
    calls = len(fake.calls)
    again = bluesky.Bluesky().collect(state, 500)
    assert again.stop_reason == "awaiting_refresh" and len(fake.calls) == calls


def test_search_respects_request_budget_and_resumes(search, isolated_settings):
    cfg, install = search
    cfg["max_requests_per_batch"] = 1
    install(Fake({('"car insurance"', ""): {"posts": [post(1, "need car insurance")], "cursor": "c2"},
                  ('"car insurance"', "c2"): {"posts": [post(2, "car insurance quote please")]}}))
    first = bluesky.Bluesky().collect({}, 500)
    assert first.stop_reason == "request_budget" and first.cursor["page"] == "c2"
    second = bluesky.Bluesky().collect(first.cursor, 500)
    assert [r.source_record_id for r in second.records] == ["did:plc:author2/rkey2"]


def test_search_rate_limit_is_a_source_backoff(search, isolated_settings):
    _cfg, install = search
    install(Fake({}, status=429))
    with pytest.raises(bluesky.SourceError) as exc:
        bluesky.Bluesky().collect({}, 10)
    assert exc.value.code == "bluesky_rate_limited" and exc.value.retry_seconds >= 60


def test_post_profile_uses_explicit_public_fields_only():
    record = bluesky_post_profile(post(7, "need life cover"))
    data = profile(record)
    assert record.url == "https://bsky.app/profile/did:plc:author7/post/rkey7"
    assert data["username"] == "user7.bsky.social" and data["display_name"] == "Display 7"
    assert data["first_name"] == "" and not data["name_reliable"]  # never inferred from display name
    assert data["profile_url"] == "https://bsky.app/profile/did:plc:author7"
    with pytest.raises(ValueError):
        bluesky_post_profile({**post(8, "x"), "uri": "at://did:plc:x/app.bsky.feed.post/"})


def test_youtube_recency_and_relevance_filters(isolated_settings, monkeypatch):
    from pydantic import SecretStr

    isolated_settings.youtube_api_key = SecretStr("fixture-key")
    cfg = {"video_ids": [], "channel_ids": [], "queries": ["car insurance South Africa"], "max_requests_per_batch": 3,
           "search_unit_cost": 100, "request_delay_seconds": 0, "refresh_hours": 24, "search_pages_per_query": 1,
           "comments_order": "time", "recent_videos_only": True, "region_code": "ZA", "relevance_language": "en",
           "store_recent_only": True, "require_discovery_match": True}
    monkeypatch.setattr(youtube, "rules", lambda name: {"youtube": cfg} if name == "sources" else {"max_age_days": 30})
    monkeypatch.setattr(youtube, "reserve", lambda *_a: True)
    monkeypatch.setattr(youtube.time, "sleep", lambda _s: None)
    seen = {}

    def comment(cid, text, age):
        return {"snippet": {"topLevelComment": {"id": cid, "snippet": {
            "authorChannelId": {"value": "a" + cid}, "textOriginal": text,
            "publishedAt": (NOW - timedelta(days=age)).isoformat()}}}}

    def handler(request):
        if request.url.path.endswith("/search"):
            seen["search"] = dict(request.url.params)
            return httpx.Response(200, json={"items": [{"id": {"videoId": "v1"}}]})
        return httpx.Response(200, json={"items": [
            comment("c1", "Need car insurance quote", 2), comment("c2", "great video", 2),
            comment("c3", "Need car insurance quote", 90)]})

    real = httpx.Client
    monkeypatch.setattr(youtube.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    result = youtube.YouTube().collect({}, 100)
    assert result.scanned == 3 and [r.source_record_id for r in result.records] == ["c1"]
    assert seen["search"]["regionCode"] == "ZA" and seen["search"]["publishedAfter"].endswith("Z")


@pytest.mark.integration
def test_bio_contact_keeps_profile_provenance_and_samples_are_bounded(postgres, isolated_settings, monkeypatch):
    from app.database import session
    from app.models import Candidate, SampleRecord, SourceRecord, now
    from app.services import pipeline, samples

    isolated_settings.processing_enabled = True
    isolated_settings.sample_store_max_per_source = 3

    def raw(n, text, bio=""):
        return SourceRecord(
            source="bluesky", source_record_id=f"did:plc:a{n}/r{n}", source_url=f"https://bsky.app/profile/a{n}/post/r{n}",
            identity_hash=f"{n:064d}", content_hash=f"c{n:063d}", text=text, published_at=now() - timedelta(hours=1),
            profile={"bio": bio, "profile_url": f"https://bsky.app/profile/a{n}"} if bio else {})

    with session() as db:
        db.add(raw(1, "Need car insurance quote in Durban, which insurer is best?", "mail me: someone@example.co.za"))
        for n in range(2, 6):
            db.add(raw(n, f"weather chat number {n}"))
    assert pipeline.normalize_batch(10) == {"normalized": 5}
    with session() as db:
        candidate = db.query(Candidate).one()
        assert candidate.contacts == [{"email": "someone@example.co.za", "source_url": "https://bsky.app/profile/a1"}]
        stored = db.query(SampleRecord).order_by(SampleRecord.priority.desc()).all()
    assert len(stored) == 3  # capped per source
    assert stored[0].outcome == "candidate" and stored[0].candidate_id == candidate.id
    assert stored[0].profile["bio"].startswith("mail me")

    with session() as db:
        pipeline._terminal(db.get(Candidate, candidate.id), "NO_CONTACT")
    with session() as db:
        assert samples.listing(db, "bluesky")[0]["outcome"] == "no_contact"
        assert samples.remove(db, source_record_id="did:plc:a1/r1") == 1
        assert samples.remove(db, source="bluesky") == 2


@pytest.mark.integration
def test_sample_store_disabled_by_default(postgres, isolated_settings):
    from app.database import session
    from app.models import SampleRecord, SourceRecord, now
    from app.services import pipeline

    with session() as db:
        db.add(SourceRecord(source="bluesky", source_record_id="x/1", source_url="https://bsky.app/x", identity_hash="1" * 64,
                            content_hash="2" * 64, text="weather", published_at=now()))
    pipeline.normalize_batch(5)
    with session() as db:
        assert db.query(SampleRecord).count() == 0
