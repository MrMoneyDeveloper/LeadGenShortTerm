"""Optional stateless remote workers (Vercel / Netlify) behind one strict contract.

Render stays authoritative. A remote worker only receives already-normalized text plus
pre-normalized term groups and returns which groups matched (operation ``match_terms``).
It holds no state, writes nothing durable and never sees credentials or contact decisions.

Routing and safety:

* Eligible only when REMOTE_WORKERS_ENABLED and the worker is enabled, configured, under its
  daily call cap and its circuit is closed. Otherwise the work simply runs locally.
* A failure, timeout, schema violation or parity mismatch counts against that worker; repeated
  failures (or any mismatch) open its circuit for REMOTE_WORKER_COOLDOWN_SECONDS.
* A sampled fraction (REMOTE_WORKER_VERIFY_RATE) of remote results is recomputed locally;
  mismatching results are discarded and the local result is used.
* Worker state (calls, failures, circuit) persists in ``provider_rate_state`` so it survives
  Render restarts. No lead payload is stored there.
"""

import hashlib
import json
import random
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.database import session
from app.models import ProviderRateState, now
from app.qualification import match_terms

CONTRACT_VERSION = 1
OPERATION = "match_terms"
PATHS = {"vercel": "/api/worker", "netlify": "/.netlify/functions/worker"}
MAX_TEXT_CHARS = 8000
MAX_REQUEST_BYTES = 512 * 1024


@dataclass(frozen=True)
class Worker:
    name: str
    url: str
    secret: str
    daily_cap: int

    @property
    def provider(self):
        return "worker_" + self.name


class WorkerError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def configured_workers(cfg=None):
    cfg = cfg or settings()
    workers = []
    for name in ("vercel", "netlify"):
        url = getattr(cfg, name + "_base_url").rstrip("/")
        secret = getattr(cfg, name + "_shared_secret").get_secret_value()
        if getattr(cfg, name + "_enabled") and url and secret:
            workers.append(Worker(name, url + PATHS[name], secret, getattr(cfg, name + "_daily_call_cap")))
    return workers


def _state(db, provider, lock=False):
    db.execute(insert(ProviderRateState).values(provider=provider, state={}).on_conflict_do_nothing())
    query = select(ProviderRateState).where(ProviderRateState.provider == provider)
    return db.scalar(query.with_for_update() if lock else query)


def _fresh_day(state, current):
    day = current.date().isoformat()
    if state.get("day") != day:
        state.update(day=day, calls_today=0, failures_today=0, items_today=0, mismatches_today=0)
    return state


def eligible(cfg=None):
    """Workers whose circuit is closed and whose daily cap has headroom, in preference order.

    Vercel is preferred; Netlify's credit-based Free plan makes it a conservative fallback lane.
    """
    cfg = cfg or settings()
    if not cfg.remote_workers_enabled:
        return []
    current = now()
    ranked = []  # configured_workers() already yields preference order
    with session() as db:
        for worker in configured_workers(cfg):
            state = _fresh_day(dict(_state(db, worker.provider).state or {}), current)
            if state.get("open_until", 0) > current.timestamp():
                continue
            if state["calls_today"] >= worker.daily_cap:
                continue
            ranked.append(worker)
    return ranked


def _record(worker, ok, code=None, items=0, mismatch=False):
    cfg, current = settings(), now()
    with session() as db:
        row = _state(db, worker.provider, lock=True)
        state = _fresh_day(dict(row.state or {}), current)
        state["calls_today"] += 1
        state["items_today"] += items
        state["last_call_at"] = current.timestamp()
        if ok and not mismatch:
            state.update(consecutive_failures=0, last_ok_at=current.timestamp())
        else:
            state["failures_today"] += 1
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            state["last_error"] = code
            if mismatch:
                state["mismatches_today"] += 1
                state["mismatches_total"] = state.get("mismatches_total", 0) + 1
            if mismatch or state["consecutive_failures"] >= cfg.remote_worker_failure_threshold:
                state["open_until"] = current.timestamp() + cfg.remote_worker_cooldown_seconds
                state["circuit_opens"] = state.get("circuit_opens", 0) + 1
        row.state = state


def build_request(job_id, items, groups):
    batch_key = hashlib.sha256("\n".join(sorted(i["id"] for i in items)).encode()).hexdigest()[:32]
    return {
        "contract": CONTRACT_VERSION,
        "job_id": job_id,
        "candidate_id": batch_key,
        "operation": OPERATION,
        "payload": {"groups": groups, "items": items},
    }


def validate_response(request, data, groups):
    """Strict contract check; never trusts positions, only stable IDs."""
    if not isinstance(data, dict) or set(data) != {"contract", "job_id", "candidate_id", "status", "result"}:
        raise WorkerError("worker_schema")
    if (data["contract"], data["job_id"], data["candidate_id"], data["status"]) != (
        CONTRACT_VERSION, request["job_id"], request["candidate_id"], "ok"
    ):
        raise WorkerError("worker_identity_mismatch")
    result = data["result"]
    if not isinstance(result, dict) or set(result) != {"items"} or not isinstance(result["items"], list):
        raise WorkerError("worker_schema")
    expected = {item["id"] for item in request["payload"]["items"]}
    names = set(groups)
    matched = {}
    for item in result["items"]:
        if not isinstance(item, dict) or set(item) != {"id", "matched"} or not isinstance(item["matched"], list):
            raise WorkerError("worker_schema")
        if item["id"] not in expected or item["id"] in matched:
            raise WorkerError("worker_unexpected_id")
        if not all(isinstance(n, str) and n in names for n in item["matched"]) or \
                len(set(item["matched"])) != len(item["matched"]):
            raise WorkerError("worker_unknown_group")
        matched[item["id"]] = item["matched"]
    if set(matched) != expected:
        raise WorkerError("worker_missing_id")
    return matched


def call(worker, request, groups, client=None):
    body = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise WorkerError("request_too_large")
    timeout = settings().remote_worker_timeout_seconds
    owned = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        response = client.post(worker.url, content=body, headers={
            "Authorization": "Bearer " + worker.secret, "Content-Type": "application/json",
            "X-Leadgen-Contract": str(CONTRACT_VERSION),
        })
    except httpx.TimeoutException:
        raise WorkerError("worker_timeout") from None
    except httpx.HTTPError:
        raise WorkerError("worker_network") from None
    finally:
        if owned:
            client.close()
    if response.status_code != 200:
        raise WorkerError(f"worker_http_{response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise WorkerError("worker_invalid_json") from None
    return validate_response(request, data, groups)


def match_batch(records, groups, job_id=None, client=None):
    """records: iterable of (id, normalized_text). Returns ({id: matched}, worker_name or None).

    Items too long for the contract, and every item when no worker succeeds, are absent from the
    result; callers compute those locally. This function never raises for worker problems.
    """
    cfg = settings()
    items = [{"id": str(rid), "text": text} for rid, text in records if len(text) <= MAX_TEXT_CHARS]
    items = items[: cfg.remote_worker_max_items]
    if not items:
        return {}, None
    workers = eligible(cfg)
    if not workers:
        return {}, None
    request = build_request(job_id or str(uuid.uuid4()), items, groups)
    texts = {item["id"]: item["text"] for item in items}
    for worker in workers:
        try:
            matched = call(worker, request, groups, client)
        except WorkerError as exc:
            _record(worker, False, exc.code, len(items))
            continue
        # Shadow verification: any disagreement discards this worker's whole batch.
        sample = [i for i in matched if random.random() < cfg.remote_worker_verify_rate]
        if any(sorted(match_terms(texts[i], groups)) != sorted(matched[i]) for i in sample):
            _record(worker, False, "worker_parity_mismatch", len(items), mismatch=True)
            continue
        _record(worker, True, items=len(items))
        return matched, worker.name
    return {}, None


def status(db):
    cfg, current = settings(), now()
    configured = {w.name: w for w in configured_workers(cfg)}
    result = []
    for name in ("vercel", "netlify"):
        row = db.get(ProviderRateState, "worker_" + name)
        state = _fresh_day(dict(row.state or {}) if row else {}, current)
        result.append({
            "worker": name,
            "enabled": bool(cfg.remote_workers_enabled and getattr(cfg, name + "_enabled")),
            "configured": name in configured,
            "circuit_open": state.get("open_until", 0) > current.timestamp(),
            "open_until": state.get("open_until"),
            "calls_today": state["calls_today"],
            "daily_call_cap": getattr(cfg, name + "_daily_call_cap"),
            "items_today": state["items_today"],
            "failures_today": state["failures_today"],
            "mismatches_today": state["mismatches_today"],
            "consecutive_failures": state.get("consecutive_failures", 0),
            "last_error": state.get("last_error"),
            "last_ok_at": state.get("last_ok_at"),
        })
    return result
