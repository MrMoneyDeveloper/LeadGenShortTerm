"""Explicit-start local-device failover watchdog.

The watchdog never starts by itself (no OS startup task, no daemon). It is launched by the
operator through ``scripts/failover/Start-LeadgenFailover.ps1`` or ``python -m app.failover``.

Each cycle it:

1. probes the Render HTTP health endpoint (bounded timeout);
2. reads durable progress evidence from PostgreSQL: the age of the **cloud** executor heartbeat
   (written by ``jobs.run_next`` on Render) and whether work is due;
3. classifies the cloud as healthy, ``render_unreachable`` or ``scheduler_stalled`` (healthy HTTP
   but no cloud executor ticks), or the database as unavailable;
4. applies failure/recovery thresholds so it never flaps between cloud and device;
5. in device mode, and only when ``FAILOVER_ENABLED=true`` and the database/campaign identity is
   verified, runs a bounded number of the SAME durable jobs through ``jobs.schedule_tick`` and
   ``jobs.run_next``. The PostgreSQL advisory executor lock, source cursors, idempotency keys,
   campaign controls and dedupe hashes are shared with Render, so cloud and device never run
   uncoordinated executors.

A database outage pauses the watchdog; it never creates or switches to another database. Gates
(acquisition, processing, sources, Groq) come from the operator's ignored ``.env``; this module
does not enable any of them. Device fallback needs power, connectivity and database access.
"""

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, select, text

from app.config import ROOT, settings

STATE_DIR = ROOT / ".failover"
STATE_FILE = STATE_DIR / "state.json"
STOP_FILE = STATE_DIR / "STOP"
LOCK_FILE = STATE_DIR / "watchdog.lock"


@dataclass
class Assessment:
    database_ok: bool
    http_ok: bool | None
    cloud_healthy: bool | None
    reason: str
    cloud_heartbeat_age: float | None = None
    work_due: bool | None = None
    http_ms: int | None = None


def default_state():
    return {"mode": "cloud", "consecutive_unhealthy": 0, "consecutive_healthy": 0, "cycles": 0,
            "device_jobs_run": 0, "last_transition_at": None, "last_assessment": None, "last_cycle_at": None}


def load_state(path=None):
    path = Path(path or STATE_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**default_state(), **{k: v for k, v in data.items() if k in default_state()}}
    except (OSError, ValueError):
        return default_state()


def save_state(state, path=None):
    path = Path(path or STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    os.replace(temp, path)  # atomic: a crash never leaves a half-written state file


def probe_http(cfg, client=None):
    """Bounded health probe; never follows redirects."""
    base = (cfg.render_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        return False, None
    owned = client is None
    client = client or httpx.Client(timeout=cfg.failover_health_timeout_seconds, follow_redirects=False)
    started = time.monotonic()
    try:
        response = client.get(base + "/health")
        ok = response.status_code == 200
    except httpx.HTTPError:
        ok = False
    finally:
        if owned:
            client.close()
    return ok, round((time.monotonic() - started) * 1000)


def read_evidence(db):
    """Durable progress evidence. Cloud and device heartbeats are separate rows."""
    from app.models import Candidate, Job, ProviderRateState, SourceRecord, now

    current = now().timestamp()
    cloud = db.get(ProviderRateState, "executor_cloud")
    last = (cloud.state or {}).get("last_tick_at") if cloud else None
    raw_pending = db.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.status == "PENDING"))
    due = db.scalar(select(func.count()).select_from(Candidate).where(
        Candidate.status.in_(["VALIDATE", "CLASSIFY"]), Candidate.available_at <= now()))
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.status == "QUEUED", Job.available_at <= now()))
    return {"cloud_heartbeat_age": None if last is None else max(0.0, current - float(last)),
            "work_due": bool(raw_pending or due or queued)}


def assess(http_ok, evidence, cfg, http_ms=None):
    if evidence is None:
        return Assessment(False, http_ok, None, "database_unavailable", http_ms=http_ms)
    age = evidence["cloud_heartbeat_age"]
    common = {"cloud_heartbeat_age": age, "work_due": evidence["work_due"], "http_ms": http_ms}
    if not http_ok:
        return Assessment(True, http_ok, False, "render_unreachable", **common)
    if age is None or age > cfg.failover_stall_seconds:
        # Healthy HTTP endpoint but no cloud executor ticks: scheduler/processing has stopped.
        return Assessment(True, http_ok, False, "scheduler_stalled", **common)
    return Assessment(True, http_ok, True, "cloud_healthy", **common)


def decide(state, assessment, cfg, current=None):
    """Hysteresis: switch only after consecutive evidence; a database outage changes nothing."""
    state = dict(state)
    state["last_assessment"] = asdict(assessment)
    if not assessment.database_ok:
        return state  # pause: no processing, no transition, no competing database
    if assessment.cloud_healthy:
        state["consecutive_healthy"] += 1
        state["consecutive_unhealthy"] = 0
        if state["mode"] == "device" and state["consecutive_healthy"] >= cfg.failover_recovery_threshold:
            state["mode"], state["last_transition_at"] = "cloud", current or time.time()
    else:
        state["consecutive_unhealthy"] += 1
        state["consecutive_healthy"] = 0
        if state["mode"] == "cloud" and state["consecutive_unhealthy"] >= cfg.failover_failure_threshold:
            state["mode"], state["last_transition_at"] = "device", current or time.time()
    return state


def verify_identity(db, cfg, expected_head=None):
    """Problems that forbid device processing. Empty list means the target is verified."""
    from app.models import PipelineState

    problems = []
    if cfg.executor_role != "device":
        problems.append("executor_role_not_device")
    if cfg.environment not in {"test", "staging", "production"}:
        problems.append("environment_not_deployable")
    expected_head = expected_head or alembic_head()
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if version != expected_head:
        problems.append("migration_head_mismatch")
    problems += database_url_problems(cfg.database_url.get_secret_value())
    if cfg.failover_expected_database:
        if db.execute(text("SELECT current_database()")).scalar() != cfg.failover_expected_database:
            problems.append("database_name_mismatch")
    state = db.get(PipelineState, 1)
    if state is None:
        problems.append("pipeline_uninitialized")
    elif state.campaign_id and state.campaign_id != cfg.campaign_id:
        problems.append("campaign_id_mismatch")
    return problems


def database_url_problems(url):
    """The device must use the EXTERNAL database URL over TLS. Never returns or logs the URL."""
    from urllib.parse import parse_qs, urlsplit

    try:
        parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://"))
        host = parts.hostname or ""
    except ValueError:
        return ["database_url_invalid"]
    if host in {"localhost", "127.0.0.1", "::1"}:
        return []  # disposable local test database
    problems = []
    if "." not in host:
        problems.append("database_url_internal_host")  # Render internal hostnames have no domain
    if parse_qs(parts.query).get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}:
        problems.append("database_tls_not_required")
    return problems


def alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ROOT / "services" / "render_api" / "alembic.ini"))).get_current_head()


def assist_kinds(cfg):
    return {k.strip() for k in cfg.failover_assist_kinds.split(",") if k.strip()} or None


def process(cfg, stop_requested=lambda: False, kinds=None):
    """Run at most FAILOVER_MAX_JOBS_PER_CYCLE bounded jobs through the shared executor lock."""
    from app import jobs

    results = []
    jobs.schedule_tick()  # enqueue-only; same idempotent stage scheduling as Render
    for _ in range(cfg.failover_max_jobs_per_cycle):
        if stop_requested():
            break
        result = jobs.run_next(kinds) if kinds else jobs.run_next()
        results.append(result.get("status"))
        if result.get("status") in {"idle", "busy"}:
            break  # nothing eligible, or another executor (e.g. recovered Render) holds the lock
    return results


def cycle(cfg, state, client=None, session_factory=None, expected_head=None, stop_requested=lambda: False):
    from app.database import session

    session_factory = session_factory or session
    http_ok, http_ms = probe_http(cfg, client)
    try:
        with session_factory() as db:
            evidence = read_evidence(db)
    except Exception:  # noqa: BLE001 - any database failure means pause, never fail over elsewhere
        evidence = None
    assessment = assess(http_ok, evidence, cfg, http_ms)
    state = decide(state, assessment, cfg)
    state["cycles"] += 1
    state["last_cycle_at"] = time.time()
    report = {"mode": state["mode"], "assessment": asdict(assessment), "action": "observe", "jobs": []}
    # Assist mode: with a healthy cloud the device also runs bounded jobs. They go through the same
    # advisory executor lock and fencing epoch, so device and Render interleave job by job.
    assisting = state["mode"] == "cloud" and cfg.failover_assist and bool(assessment.cloud_healthy)
    if not assessment.database_ok:
        report["action"] = "paused_database_unavailable"
    elif state["mode"] == "device" or assisting:
        if not cfg.failover_enabled:
            report["action"] = "observe_only_failover_disabled"
        else:
            try:
                with session_factory() as db:
                    problems = verify_identity(db, cfg, expected_head)
                if problems:
                    report["action"], report["identity_problems"] = "refused_identity", problems
                else:
                    # In assist mode only the configured kinds run here; full failover runs all.
                    report["jobs"] = process(cfg, stop_requested, assist_kinds(cfg) if assisting else None)
                    state["device_jobs_run"] += sum(1 for s in report["jobs"] if s not in {"idle", "busy", "fenced"})
                    report["action"] = "assist_processed" if assisting else "device_processed"
            except Exception as exc:  # noqa: BLE001 - pause and retry next cycle; never exit
                report["action"], report["error"] = "paused_error", type(exc).__name__  # class name only
    return state, report


def safe_cycle(cfg, state, **kwargs):
    """Recovery boundary for the loop: any unexpected failure becomes a paused cycle."""
    try:
        return cycle(cfg, state, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return state, {"mode": state.get("mode"), "action": "paused_error", "error": type(exc).__name__, "jobs": []}


@contextmanager
def single_instance(path=None):
    """OS-level exclusive lock; released automatically if the process dies."""
    path = Path(path or LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")  # noqa: SIM115 - held for the lifetime of the watchdog
    try:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("another failover watchdog is already running") from None
        yield
    finally:
        handle.close()


def run(cfg, max_cycles=None, sleep=time.sleep, until=None, clock=time.time):
    """Loop until STOP, ``max_cycles`` or the wall-clock ``until`` (epoch seconds) is reached."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if STOP_FILE.exists():
        STOP_FILE.unlink()  # a stale stop request from a previous session
    with single_instance():
        state = load_state()
        cycles = 0
        while not STOP_FILE.exists() and (until is None or clock() < until):
            state, report = safe_cycle(cfg, state, stop_requested=STOP_FILE.exists)
            try:
                save_state(state)
            except OSError:
                report["state_saved"] = False
            print(json.dumps(report, default=str), flush=True)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            for _ in range(cfg.failover_interval_seconds):
                if STOP_FILE.exists() or (until is not None and clock() >= until):
                    break
                sleep(1)
        if STOP_FILE.exists():
            STOP_FILE.unlink()
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit-start local-device failover watchdog")
    parser.add_argument("command", choices=["status", "once", "run", "stop"])
    parser.add_argument("--hours", type=float, help="run: stop by itself after this many hours")
    args = parser.parse_args(argv)
    if args.hours is not None and not 0 < args.hours <= 72:
        parser.error("--hours must be between 0 and 72")
    cfg = settings()
    if args.command == "stop":
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text("stop", encoding="utf-8")
        print(json.dumps({"stop_requested": True}))
        return 0
    if args.command == "status":
        # Observe-only: never processes, never changes thresholds/mode.
        from app.database import session

        http_ok, http_ms = probe_http(cfg)
        try:
            with session() as db:
                evidence = read_evidence(db)
                problems = verify_identity(db, cfg)
        except Exception:  # noqa: BLE001
            evidence, problems = None, ["database_unavailable"]
        print(json.dumps({"saved_state": load_state(), "assessment": asdict(assess(http_ok, evidence, cfg, http_ms)),
                          "failover_enabled": cfg.failover_enabled, "failover_assist": cfg.failover_assist,
                          "executor_role": cfg.executor_role,
                          "identity_problems": problems}, indent=1, default=str))
        return 0
    if args.command == "once":
        with single_instance():
            state, report = safe_cycle(cfg, load_state())
            save_state(state)
            print(json.dumps(report, indent=1, default=str))
        return 0
    run(cfg, until=None if args.hours is None else time.time() + args.hours * 3600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
