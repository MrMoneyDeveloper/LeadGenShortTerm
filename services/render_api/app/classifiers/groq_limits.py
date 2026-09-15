"""Conservative persisted admission; no source text or credentials in rate state.

Token reservations include the completion ceiling and are not refunded after failure.
This intentionally sacrifices some allowance to remain safe after ambiguous timeouts.
"""

import math
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.database import session
from app.models import ProviderRateState, Usage, now


def seconds(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", str(value))
        if not parts or "".join(a + b for a, b in parts) != str(value):
            return None
        result = sum(float(a) * {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}[b]
                     for a, b in parts)
    return result if math.isfinite(result) and 0 <= result <= 604800 else None


def locked(db):
    db.execute(insert(ProviderRateState).values(provider="groq", state={}).on_conflict_do_nothing())
    return db.scalar(select(ProviderRateState).where(ProviderRateState.provider == "groq").with_for_update())


def admit(state, cfg, tokens, current, units=0, used_tokens=0):
    """Mutates detached JSON under a DB lock; returns a code and eligible timestamp."""
    stamp = current.timestamp()
    day = current.date().isoformat()
    tomorrow = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if state.get("day") != day:
        state.update(day=day, daily_requests=units, daily_reserved_tokens=used_tokens)
    events = [event for event in state.get("events", []) if event[0] > stamp - 60]
    state["events"] = events
    request_cap = min(cfg.groq_daily_request_soft_cap, cfg.groq_rpd_limit,
                      state.get("requests", {}).get("limit", cfg.groq_rpd_limit))
    token_cap = min(cfg.groq_tpm_soft_cap, cfg.groq_tpm_limit,
                    state.get("tokens", {}).get("limit", cfg.groq_tpm_limit))
    if tokens > token_cap:
        return "prompt_exceeds_minute_budget", None
    code, retry = "ok", stamp
    if state.get("next_allowed_at", 0) > stamp:
        code, retry = "cooldown", state["next_allowed_at"]
    elif state["daily_requests"] >= request_cap:
        code, retry = "unit_cap", tomorrow
    elif state["daily_reserved_tokens"] + tokens > min(cfg.groq_daily_token_soft_cap, cfg.groq_tpd_limit):
        code, retry = "token_cap", tomorrow
    elif len(events) >= min(cfg.groq_rpm_soft_cap, cfg.groq_rpm_limit):
        code, retry = "minute_cap", events[0][0] + 60 if events else stamp + 60
    elif sum(event[1] for event in events) + tokens > token_cap:
        code, retry = "minute_cap", events[0][0] + 60 if events else stamp + 60
    else:
        for kind, required in (("requests", 1), ("tokens", tokens)):
            window = state.get(kind, {})
            if window.get("reset_at", 0) > stamp and window.get("remaining", required) < required:
                code, retry = "provider_cap", max(retry, window["reset_at"])
    if code != "ok":
        state["deferred_count"] = state.get("deferred_count", 0) + 1
        return code, datetime.fromtimestamp(retry, UTC)
    events.append([stamp, tokens])
    state["daily_requests"] += 1
    state["daily_reserved_tokens"] += tokens
    for kind, required in (("requests", 1), ("tokens", tokens)):
        window = state.get(kind, {})
        if window.get("reset_at", 0) > stamp and "remaining" in window:
            window["remaining"] = max(0, window["remaining"] - required)
    return "ok", None


def reserve_request(tokens):
    cfg, current = settings(), now()
    with session() as db:
        row = locked(db)
        state = dict(row.state)
        db.execute(insert(Usage).values(day=current.date(), provider="groq", units=0,
                                       input_tokens=0, output_tokens=0, estimated_cost=0).on_conflict_do_nothing())
        usage = db.scalar(select(Usage).where(Usage.day == current.date(), Usage.provider == "groq").with_for_update())
        result = admit(state, cfg, tokens, current, usage.units, usage.input_tokens + usage.output_tokens)
        row.state = state
        if result[0] == "ok":
            usage.units += 1
        return result


def observe(state, headers, status, current):
    headers = {k.lower(): v for k, v in headers.items()}
    stamp = current.timestamp()
    state["last_response_at"] = stamp
    for kind in ("requests", "tokens"):
        window = dict(state.get(kind, {}))
        reset = seconds(headers.get("x-ratelimit-reset-" + kind))
        for field in ("limit", "remaining"):
            try:
                value = int(headers["x-ratelimit-" + field + "-" + kind])
                if value >= 0:
                    window[field] = value
            except (KeyError, ValueError, TypeError):
                pass
        if reset is not None:
            window["reset_at"] = stamp + reset
        elif "remaining" in window:
            window["reset_at"] = max(window.get("reset_at", 0), stamp + (86400 if kind == "requests" else 60))
        state[kind] = window
    if status == 429:
        delay = seconds(headers.get("retry-after"))
        retry = stamp + max(1, delay if delay is not None else 60)
        for kind in ("requests", "tokens"):
            window = state[kind]
            if window.get("remaining") == 0:
                retry = max(retry, window.get("reset_at", retry))
        state["next_allowed_at"] = max(state.get("next_allowed_at", 0), retry)
        state["responses_429"] = state.get("responses_429", 0) + 1
        return datetime.fromtimestamp(state["next_allowed_at"], UTC)
    return None


def record_response(headers, status):
    with session() as db:
        row = locked(db)
        state = dict(row.state)
        retry = observe(state, headers, status, now())
        row.state = state
        return retry
