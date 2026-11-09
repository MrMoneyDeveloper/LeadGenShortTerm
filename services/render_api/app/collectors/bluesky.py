import copy
import hashlib
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.collectors.base import Batch, SourceError
from app.collectors.profiles import bluesky_post_profile, bluesky_profile
from app.config import rules, settings
from app.models import now
from app.qualification import discovery_match

SEARCH_HOST = "https://api.bsky.app"


def _stamp(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class BlueskySearch:
    """Targeted, recency-bounded search over configured phrases (app.bsky.feed.searchPosts).

    Cursor: query index, page cursor and pages used for the current query, the current pass
    start, the ``since`` lower bound and ``refresh_at``. Each pass after the first only looks
    back to the previous pass start (minus an overlap), so repeated passes stay cheap.
    Matching authors' public bios are fetched in bulk so contacts published there can be found.
    """

    def collect(self, cursor, limit):
        cfg, env = rules("sources")["bluesky"], settings()
        queries = cfg.get("queries", [])
        state = copy.deepcopy(cursor) or {}
        result = Batch(cursor=state)
        if not queries:
            result.exhausted, result.stop_reason = True, "no_targets"
            return result
        current = now()
        query_set = hashlib.sha256("\n".join(queries).encode("utf-8")).hexdigest()[:16]
        if state.get("query_set") != query_set:
            # A changed query list must never inherit the previous list's positional cursor.
            state.clear()
            state["query_set"] = query_set
        if state.get("refresh_at"):
            if current < datetime.fromisoformat(state["refresh_at"]):
                result.exhausted, result.stop_reason = True, "awaiting_refresh"
                return result
            state.pop("refresh_at")
        if "q" not in state or state.get("mode") != "search":
            recency = timedelta(days=rules("scoring")["max_age_days"])
            state.update(mode="search", q=0, page="", pages=0, since=_stamp(current - recency))
        if not state.get("pass_started"):
            state["pass_started"] = _stamp(current)
        deadline = time.monotonic() + env.job_seconds - 8
        requests, records = 0, []
        with httpx.Client(timeout=10, headers={"User-Agent": "leadgen-shortterm/1.0"}) as client:
            while result.scanned < limit and requests < cfg.get("max_requests_per_batch", 10) \
                    and time.monotonic() < deadline:
                if state["q"] >= len(queries):
                    overlap = timedelta(hours=cfg.get("overlap_hours", 1))
                    state.update(
                        q=0, page="", pages=0,
                        since=_stamp(datetime.fromisoformat(state["pass_started"].replace("Z", "+00:00")) - overlap),
                        pass_started="",
                        refresh_at=(current + timedelta(hours=cfg.get("refresh_hours", 6))).isoformat(),
                    )
                    result.exhausted, result.stop_reason = True, "discovery_complete"
                    break
                params = {"q": queries[state["q"]], "limit": min(100, max(1, limit - result.scanned)),
                          "sort": "latest", "since": state["since"]}
                if state["page"]:
                    params["cursor"] = state["page"]
                requests += 1
                data = self._get(client, "/xrpc/app.bsky.feed.searchPosts", params, state)
                if data is None:  # invalid cursor: move on to the next query
                    state.update(q=state["q"] + 1, page="", pages=0)
                    continue
                # Never count past the requested limit even if the AppView returns extra posts.
                posts = data.get("posts", [])[: max(0, limit - result.scanned)]
                for post in posts:
                    result.scanned += 1
                    try:
                        if not discovery_match(post["record"].get("text", "")):
                            continue
                        records.append(bluesky_post_profile(post))
                    except (KeyError, ValueError, TypeError, AttributeError):
                        continue
                state["pages"] += 1
                state["page"] = data.get("cursor") or ""
                if not posts or not state["page"] or state["pages"] >= cfg.get("pages_per_query", 2):
                    state.update(q=state["q"] + 1, page="", pages=0)
                time.sleep(cfg.get("request_delay_seconds", 0.2))
            if records and cfg.get("fetch_profiles", True):
                self._attach_bios(client, records)
        result.records = records
        if result.stop_reason != "discovery_complete":
            result.stop_reason = "batch_limit" if result.scanned >= limit else "request_budget"
        return result

    def _get(self, client, path, params, state):
        try:
            response = client.get(SEARCH_HOST + path, params=params)
        except httpx.HTTPError:
            raise SourceError("bluesky_network", 120) from None
        # Unauthenticated AppView refuses deeper pages (403) and stale cursors (400): end this phrase.
        if response.status_code in (400, 403) and "cursor" in params:
            return None
        if response.status_code == 429:
            try:
                reset = int(response.headers.get("ratelimit-reset", "0")) - int(time.time())
            except ValueError:
                reset = 0
            raise SourceError("bluesky_rate_limited", max(60, min(reset, 3600)))
        if response.status_code >= 500:
            raise SourceError("bluesky_server", 300)
        if response.status_code != 200:
            raise SourceError("bluesky_access_error", 3600)
        try:
            return response.json()
        except ValueError:
            raise SourceError("bluesky_protocol_error", 300) from None

    def _attach_bios(self, client, records):
        """Explicit public profile descriptions only; failures never drop the posts themselves."""
        dids = list(dict.fromkeys(r.identity for r in records))
        bios = {}
        for start in range(0, min(len(dids), 100), 25):
            try:
                response = client.get(SEARCH_HOST + "/xrpc/app.bsky.actor.getProfiles",
                                      params=[("actors", d) for d in dids[start:start + 25]])
                if response.status_code != 200:
                    break
                for actor in response.json().get("profiles", []):
                    bios[actor.get("did")] = actor.get("description") or ""
            except (httpx.HTTPError, ValueError):
                break
        for record in records:
            record.bio = bios.get(record.identity, "")[:1000]


class Bluesky:
    def collect(self, cursor, limit):
        cfg, env = rules("sources")["bluesky"], settings()
        if cfg.get("mode", "firehose") == "search":
            return BlueskySearch().collect(cursor, limit)
        result = Batch(cursor=dict(cursor))
        deadline = time.monotonic() + min(cfg["seconds"], env.job_seconds - 5)
        host = env.bluesky_jetstream_host.removeprefix("wss://").rstrip("/")
        if "/" in host or "@" in host or "?" in host:
            raise SourceError("invalid_jetstream_host", 3600)
        connected = False
        for attempt in range(cfg["reconnects"] + 1):
            params = {"wantedCollections": env.bluesky_collection}
            if result.cursor.get("time_us"):
                params["cursor"] = max(0, int(result.cursor["time_us"]) - cfg["replay_overlap_us"])
            if time.monotonic() >= deadline:
                break
            try:
                with connect(
                    "wss://" + host + "/subscribe?" + urlencode(params),
                    open_timeout=min(5, max(0.1, deadline - time.monotonic())),
                    close_timeout=1,
                    max_size=1024 * 1024,
                ) as ws:
                    connected = True
                    while result.scanned < limit and time.monotonic() < deadline:
                        try:
                            event = json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic())))
                        except TimeoutError:
                            break
                        stamp = event.get("time_us")
                        if not stamp:
                            continue
                        result.cursor["time_us"] = max(int(stamp), result.cursor.get("time_us", 0))
                        commit = event.get("commit", {})
                        if commit.get("operation") != "create" or commit.get("collection") != env.bluesky_collection:
                            continue
                        result.scanned += 1
                        data = commit.get("record", {})
                        text = data.get("text", "")
                        if not discovery_match(text):
                            continue
                        did, rkey = event.get("did"), commit.get("rkey")
                        if not did or not rkey:
                            continue
                        try:
                            record = bluesky_profile(event)
                        except (ValueError, KeyError, TypeError):
                            continue
                        result.records.append(record)
                break
            except (OSError, ConnectionClosed, TimeoutError):
                if attempt < cfg["reconnects"]:
                    time.sleep(min(2**attempt, max(0, deadline - time.monotonic())))
            except (ValueError, KeyError, TypeError):
                raise SourceError("jetstream_protocol_error") from None
        if not connected:
            raise SourceError("jetstream_unavailable")
        result.stop_reason = "batch_limit" if result.scanned >= limit else "time_limit"
        return result
