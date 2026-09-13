import json
from datetime import UTC, datetime

from app.collectors import bluesky, youtube


def test_bluesky_checkpoint_and_limit(monkeypatch):
    event = {
        "time_us": 100,
        "did": "did:plc:test",
        "commit": {
            "operation": "create",
            "collection": "app.bsky.feed.post",
            "rkey": "one",
            "record": {"text": "need car insurance", "createdAt": datetime.now(UTC).isoformat()},
        },
    }

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def recv(self, timeout):
            return json.dumps(event)

    monkeypatch.setattr(bluesky, "connect", lambda *args, **kwargs: Socket())
    result = bluesky.Bluesky().collect({}, 1)
    assert result.scanned == 1 and len(result.records) == 1
    assert result.cursor["time_us"] == 100


def test_youtube_quota_stops_without_fetch(isolated_settings, monkeypatch):
    from pydantic import SecretStr

    isolated_settings.youtube_api_key = SecretStr("fake")
    monkeypatch.setattr(youtube, "reserve", lambda *args: False)
    result = youtube.YouTube().collect({}, 3)
    assert result.stop_reason == "youtube_search_cap"
    assert result.cursor["target"] == 0
    assert result.records == []


def test_youtube_comment_pagination_checkpoint_and_source_url(isolated_settings, monkeypatch):
    from pydantic import SecretStr

    isolated_settings.youtube_api_key = SecretStr("controlled-fixture-key")
    isolated_settings.job_seconds = 30
    source_config = {
        "video_ids": ["video-one"],
        "channel_ids": [],
        "queries": [],
        "max_requests_per_batch": 3,
        "search_unit_cost": 100,
        "request_delay_seconds": 0,
        "refresh_hours": 24,
        "search_pages_per_query": 1,
        "comments_order": "time",
    }
    monkeypatch.setattr(youtube, "rules", lambda _name: {"youtube": source_config})
    monkeypatch.setattr(youtube, "reserve", lambda *_args: True)

    class Response:
        status_code = 200

        def __init__(self, number):
            self.number = number

        def json(self):
            comment_id = f"comment-{self.number}"
            payload = {
                "items": [
                    {
                        "snippet": {
                            "topLevelComment": {
                                "id": comment_id,
                                "snippet": {
                                    "authorChannelId": {"value": f"author-{self.number}"},
                                    "textOriginal": f"Need car insurance quote {self.number}",
                                    "publishedAt": datetime.now(UTC).isoformat(),
                                },
                            }
                        }
                    }
                ]
            }
            if self.number == 1:
                payload["nextPageToken"] = "page-two"
            return payload

    class Client:
        def __init__(self, **_kwargs):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, params, headers):
            assert url.endswith("/commentThreads")
            assert headers.get("X-Goog-Api-Key")
            self.calls += 1
            if self.calls == 2:
                assert params["pageToken"] == "page-two"
            return Response(self.calls)

    monkeypatch.setattr(youtube.httpx, "Client", Client)
    result = youtube.YouTube().collect({}, 2)
    assert result.scanned == 2 and [record.source_record_id for record in result.records] == ["comment-1", "comment-2"]
    assert result.records[0].url == "https://www.youtube.com/watch?v=video-one&lc=comment-1"
    assert result.cursor["target"] == 1 and result.cursor["comment_page"] == ""
