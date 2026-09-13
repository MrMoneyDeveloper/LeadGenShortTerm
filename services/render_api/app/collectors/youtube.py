import copy
import time
from datetime import datetime, timedelta

import httpx

from app.collectors.base import Batch, SourceError
from app.collectors.profiles import youtube_profile
from app.config import rules, settings
from app.models import now
from app.repositories import reserve


class YouTube:
    def collect(self, cursor, limit):
        env, cfg = settings(), rules("sources")["youtube"]
        if not env.youtube_api_key.get_secret_value():
            raise SourceError("youtube_key_missing", 3600)
        state = copy.deepcopy(cursor) or {"target": 0, "page": "", "videos": [], "comment_page": "", "search_pages": 0}
        result = Batch(cursor=state)
        targets = (
            [{"video": v} for v in cfg["video_ids"]]
            + [{"channelId": c} for c in cfg["channel_ids"]]
            + [{"q": q} for q in cfg["queries"]]
        )
        if not targets:
            result.exhausted, result.stop_reason = True, "no_targets"
            return result
        if state.get("refresh_at"):
            if now() < datetime.fromisoformat(state["refresh_at"]):
                result.exhausted, result.stop_reason = True, "awaiting_refresh"
                return result
            state = {"target": 0, "page": "", "videos": [], "comment_page": "", "search_pages": 0}
            result.cursor = state
        deadline = time.monotonic() + env.job_seconds - 5
        requests = 0

        def fetch(client, path, params):
            nonlocal requests
            if requests >= cfg["max_requests_per_batch"] or time.monotonic() >= deadline:
                raise SourceError("youtube_batch_budget", 1)
            requests += 1
            if path == "search" and not reserve("youtube_search", 1, env.youtube_daily_search_cap):
                raise SourceError("youtube_search_cap", 3600)
            cost = cfg["search_unit_cost"] if path == "search" else 1
            if not reserve("youtube", cost, env.youtube_daily_unit_cap):
                raise SourceError("youtube_unit_cap", 3600)
            time.sleep(cfg["request_delay_seconds"])
            try:
                response = client.get(
                    "https://www.googleapis.com/youtube/v3/" + path,
                    params=params,
                    headers={"X-Goog-Api-Key": env.youtube_api_key.get_secret_value()},
                )
            except httpx.HTTPError:
                raise SourceError("youtube_network") from None
            if response.status_code != 200:
                try:
                    reason = response.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
                except (ValueError, IndexError):
                    reason = ""
                if reason in {"commentsDisabled", "videoNotFound"}:
                    return {"items": []}
                if reason == "invalidPageToken":
                    raise SourceError("youtube_invalid_page", 60)
                if reason in {"quotaExceeded", "dailyLimitExceeded"}:
                    raise SourceError("youtube_quota_exhausted", 86400)
                if response.status_code == 429 or response.status_code >= 500:
                    raise SourceError("youtube_rate_or_server", 300)
                raise SourceError("youtube_access_error", 3600)
            return response.json()

        with httpx.Client(timeout=10) as client:
            while result.scanned < limit and requests < cfg["max_requests_per_batch"] and time.monotonic() < deadline:
                if state["target"] >= len(targets):
                    state["refresh_at"] = (now() + timedelta(hours=cfg["refresh_hours"])).isoformat()
                    result.exhausted, result.stop_reason = True, "discovery_complete"
                    break
                target = targets[state["target"]]
                try:
                    if not state["videos"]:
                        if "video" in target:
                            state["videos"] = [target["video"]]
                            state["discovery_done"] = True
                        elif not state.get("discovery_done"):
                            params = {"part": "id", "type": "video", "maxResults": 50, "order": "date", **target}
                            if state["page"]:
                                params["pageToken"] = state["page"]
                            data = fetch(client, "search", params)
                            state["videos"] = [
                                x["id"]["videoId"] for x in data.get("items", []) if x.get("id", {}).get("videoId")
                            ]
                            state["page"] = data.get("nextPageToken", "")
                            state["search_pages"] += 1
                            state["discovery_done"] = (
                                not state["page"] or state["search_pages"] >= cfg["search_pages_per_query"]
                            )
                        if not state["videos"]:
                            state.update(target=state["target"] + 1, page="", search_pages=0, discovery_done=False)
                            continue
                    video = state["videos"][0]
                    params = {
                        "part": "snippet",
                        "videoId": video,
                        "maxResults": min(100, limit - result.scanned),
                        "textFormat": "plainText",
                        "order": cfg["comments_order"],
                    }
                    if state["comment_page"]:
                        params["pageToken"] = state["comment_page"]
                    data = fetch(client, "commentThreads", params)
                    for item in data.get("items", []):
                        result.scanned += 1
                        try:
                            record = youtube_profile(item["snippet"]["topLevelComment"], video)
                        except (KeyError, ValueError, TypeError):
                            continue
                        result.records.append(record)
                    state["comment_page"] = data.get("nextPageToken", "")
                    if not state["comment_page"]:
                        state["videos"].pop(0)
                        if not state["videos"] and state.get("discovery_done"):
                            state.update(target=state["target"] + 1, page="", search_pages=0, discovery_done=False)
                except SourceError as exc:
                    if exc.code == "youtube_invalid_page":
                        state["comment_page"] = ""
                        state["page"] = ""
                    result.stop_reason = exc.code
                    result.retry_seconds = 0 if exc.code == "youtube_batch_budget" else exc.retry_seconds
                    break
        return result
