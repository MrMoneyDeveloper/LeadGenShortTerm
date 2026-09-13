"""Small source profiles from explicit public fields, never guessed enrichment."""

import html
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from app.collectors.base import Record


def clean_label(value, limit=160):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", html.unescape(value)).split())[:limit]


def public_url(value):
    if not isinstance(value, str) or len(value) > 1000:
        return ""
    try:
        parts = urlsplit(value)
        return value if parts.scheme in {"http", "https"} and parts.hostname and not parts.username else ""
    except ValueError:
        return ""


def profile(record):
    """The only profile fields allowed into temporary storage and final exports."""
    return {
        "display_name": clean_label(record.display_name),
        "username": clean_label(record.username),
        "business_name": clean_label(record.business_name),
        "urls": list(dict.fromkeys(url for item in record.urls[:10] if (url := public_url(item))))[:5],
        "metadata": {
            key: clean_label(value, 200)
            for key, value in record.metadata.items()
            if key in {"video_id", "comment_id", "channel_id", "did", "rkey", "collection"}
        },
    }


def youtube_profile(top, video):
    data = top["snippet"]
    published = datetime.fromisoformat(data["publishedAt"].replace("Z", "+00:00"))
    if published.tzinfo is None:
        raise ValueError("source_timestamp_timezone_missing")
    channel = data.get("authorChannelId", {}).get("value", "")
    return Record(
        source_record_id=top["id"],
        identity=channel or top["id"],
        text=data.get("textOriginal", data.get("textDisplay", ""))[:16000],
        url=f"https://www.youtube.com/watch?v={video}&lc={top['id']}",
        published_at=published,
        display_name=clean_label(data.get("authorDisplayName")),
        # authorDisplayName is not a handle and must not be copied into username.
        urls=[url] if (url := public_url(data.get("authorChannelUrl"))) else [],
        metadata={"video_id": video, "comment_id": top["id"], "channel_id": channel},
    )


def bluesky_profile(event):
    commit = event["commit"]
    data, did, rkey = commit["record"], event["did"], commit["rkey"]
    published = datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00"))
    if published.tzinfo is None:
        raise ValueError("source_timestamp_timezone_missing")
    urls = []
    for facet in data.get("facets", [])[:20]:
        for feature in facet.get("features", [])[:5]:
            if feature.get("$type") == "app.bsky.richtext.facet#link":
                urls.append(feature.get("uri", ""))
    external = data.get("embed", {}).get("external", {})
    if external.get("uri"):
        urls.append(external["uri"])
    return Record(
        source_record_id=f"{did}/{rkey}",
        identity=did,
        text=data.get("text", "")[:16000],
        url=f"https://bsky.app/profile/{did}/post/{rkey}",
        published_at=published,
        # Jetstream post events do not carry an authoritative display name/handle.
        urls=urls,
        metadata={"did": did, "rkey": rkey, "collection": commit.get("collection", "")},
    )
