import json
import time
from urllib.parse import urlencode

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.collectors.base import Batch, SourceError
from app.collectors.profiles import bluesky_profile
from app.config import rules, settings
from app.qualification import discovery_match


class Bluesky:
    def collect(self, cursor, limit):
        cfg, env = rules("sources")["bluesky"], settings()
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
