"""Local-device failover watchdog: decisions, identity, lock sharing, handback and persistence."""

from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest

from app import failover, jobs  # noqa: F401 - loaded before the postgres fixture patches sessions


def http(status):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})))


def down():
    def handler(request):
        raise httpx.ConnectError("unreachable")
    return httpx.Client(transport=httpx.MockTransport(handler))


def configure(cfg, enabled=True, failure=2, recovery=2):
    cfg.render_base_url = "https://render.example"
    cfg.environment = "test"
    cfg.executor_role = "device"
    cfg.failover_enabled = enabled
    cfg.failover_failure_threshold = failure
    cfg.failover_recovery_threshold = recovery
    cfg.failover_stall_seconds = 600
    cfg.failover_max_jobs_per_cycle = 3
    cfg.processing_enabled = True
    import os

    from pydantic import SecretStr
    cfg.database_url = SecretStr(os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1/test"))
    return cfg


def test_assessment_distinguishes_failures(isolated_settings):
    cfg = configure(isolated_settings)
    fresh, stale = {"cloud_heartbeat_age": 30, "work_due": True}, {"cloud_heartbeat_age": 3600, "work_due": True}
    assert failover.assess(True, fresh, cfg).reason == "cloud_healthy"
    assert failover.assess(False, fresh, cfg).reason == "render_unreachable"
    # Healthy HTTP endpoint, but the cloud executor has not been ticked: scheduler failure.
    assert failover.assess(True, stale, cfg).reason == "scheduler_stalled"
    assert failover.assess(True, {"cloud_heartbeat_age": None, "work_due": False}, cfg).reason == "scheduler_stalled"
    paused = failover.assess(True, None, cfg)
    assert paused.reason == "database_unavailable" and not paused.database_ok and paused.cloud_healthy is None


def test_hysteresis_prevents_rapid_switching(isolated_settings):
    cfg = configure(isolated_settings, failure=3, recovery=3)
    healthy = failover.assess(True, {"cloud_heartbeat_age": 1, "work_due": False}, cfg)
    unhealthy = failover.assess(False, {"cloud_heartbeat_age": 1, "work_due": False}, cfg)
    state = failover.default_state()
    for item in [unhealthy, healthy, unhealthy, healthy, unhealthy]:  # flapping never switches
        state = failover.decide(state, item, cfg)
    assert state["mode"] == "cloud"
    for _ in range(3):
        state = failover.decide(state, unhealthy, cfg)
    assert state["mode"] == "device"
    for _ in range(2):
        state = failover.decide(state, healthy, cfg)
    assert state["mode"] == "device"
    state = failover.decide(state, healthy, cfg)
    assert state["mode"] == "cloud"


def test_database_outage_pauses_without_transition(isolated_settings):
    cfg = configure(isolated_settings, failure=1)
    state = failover.decide(failover.default_state(), failover.assess(False, None, cfg), cfg)
    assert state["mode"] == "cloud" and state["consecutive_unhealthy"] == 0

    @contextmanager
    def broken():
        raise RuntimeError("database down")
        yield  # pragma: no cover

    state, report = failover.cycle(cfg, failover.default_state(), client=down(), session_factory=broken)
    assert report["action"] == "paused_database_unavailable" and report["jobs"] == []


def test_state_survives_restart_atomically(tmp_path):
    path = tmp_path / "state.json"
    state = {**failover.default_state(), "mode": "device", "consecutive_unhealthy": 4, "device_jobs_run": 7}
    failover.save_state(state, path)
    assert failover.load_state(path)["mode"] == "device"
    assert failover.load_state(path)["device_jobs_run"] == 7
    path.write_text("{corrupt", encoding="utf-8")
    assert failover.load_state(path) == failover.default_state()  # never crashes on a bad file


def test_single_instance_lock(tmp_path):
    lock = tmp_path / "watchdog.lock"
    with failover.single_instance(lock):
        with pytest.raises(SystemExit, match="already running"):
            with failover.single_instance(lock):
                pass
    with failover.single_instance(lock):  # released after the first holder exits
        pass


def test_stop_file_ends_run_loop(isolated_settings, monkeypatch, tmp_path):
    cfg = configure(isolated_settings, enabled=False)
    cfg.failover_interval_seconds = 15
    monkeypatch.setattr(failover, "STATE_DIR", tmp_path)
    monkeypatch.setattr(failover, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(failover, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(failover, "LOCK_FILE", tmp_path / "watchdog.lock")
    cycles = []

    def fake_cycle(cfg, state, **_kwargs):
        cycles.append(1)
        if len(cycles) == 2:
            (tmp_path / "STOP").write_text("stop")
        return state, {"mode": state["mode"], "action": "observe", "jobs": []}

    monkeypatch.setattr(failover, "cycle", fake_cycle)
    failover.run(cfg, max_cycles=10, sleep=lambda _s: None)
    assert len(cycles) == 2 and not (tmp_path / "STOP").exists()


# --- integration with a disposable PostgreSQL schema -------------------------------------------

def seed_raw(db, n):
    from app.models import SourceRecord, now

    for i in range(n):
        db.add(SourceRecord(source="bluesky", source_record_id=f"fo-{i}", source_url=f"https://example.invalid/{i}",
                            identity_hash=f"{i:064d}", content_hash=f"{i + 100:064d}", text="weather chat only",
                            published_at=now()))


def queue_normalize(db):
    from app.models import Job

    db.add(Job(idempotency_key="fo-normalize-1", kind="normalize", source=None, batch_size=10))


def stale_cloud(db, seconds):
    from app.models import ProviderRateState, now

    db.merge(ProviderRateState(provider="executor_cloud", state={"last_tick_at": now().timestamp() - seconds}))


@pytest.mark.integration
def test_scheduler_stall_with_healthy_http_hands_work_to_device(postgres, isolated_settings):
    from app.models import Job, PipelineState, SourceRecord

    jobs.initialize()
    cfg = configure(isolated_settings, failure=2)
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        seed_raw(db, 3)
        queue_normalize(db)
        stale_cloud(db, 3600)
    state = failover.default_state()
    state, report = failover.cycle(cfg, state, client=http(200), session_factory=postgres)
    assert report["assessment"]["reason"] == "scheduler_stalled" and report["action"] == "observe"
    assert report["jobs"] == []  # threshold not yet reached: no premature switch
    state, report = failover.cycle(cfg, state, client=http(200), session_factory=postgres)
    assert state["mode"] == "device" and report["action"] == "device_processed"
    assert "complete" in report["jobs"]
    with postgres() as db:
        assert db.query(SourceRecord).count() == 0  # same durable queue drained by the device
        assert db.query(Job).filter(Job.status == "COMPLETE").count() >= 1
        from app.models import ProviderRateState
        assert db.get(ProviderRateState, "executor_device").state["ticks"] >= 1
        cloud = db.get(ProviderRateState, "executor_cloud").state
        assert "ticks" not in cloud  # device work never masquerades as cloud recovery


@pytest.mark.integration
def test_device_respects_shared_executor_lock(postgres, isolated_settings):
    import os

    from sqlalchemy import create_engine, text

    from app.models import PipelineState, SourceRecord

    jobs.initialize()
    cfg = configure(isolated_settings, failure=1)
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        seed_raw(db, 2)
        queue_normalize(db)
        stale_cloud(db, 3600)
    other = create_engine(os.environ["TEST_DATABASE_URL"]).connect()
    try:
        assert other.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": jobs.EXECUTOR_LOCK})
        other.commit()
        _state, report = failover.cycle(cfg, failover.default_state(), client=down(), session_factory=postgres)
        assert report["jobs"] == ["busy"]  # Render (or anyone) holds the lock: device does nothing
        with postgres() as db:
            assert db.query(SourceRecord).count() == 2
    finally:
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": jobs.EXECUTOR_LOCK})
        other.commit()
        other.close()


@pytest.mark.integration
def test_identity_mismatch_or_disabled_failover_never_processes(postgres, isolated_settings):
    from app.models import PipelineState, SourceRecord

    jobs.initialize()
    cfg = configure(isolated_settings, failure=1)
    with postgres() as db:
        state = db.get(PipelineState, 1)
        state.paused, state.campaign_id = False, "some-other-campaign"
        seed_raw(db, 1)
        queue_normalize(db)
        stale_cloud(db, 3600)
    _s, report = failover.cycle(cfg, failover.default_state(), client=down(), session_factory=postgres)
    assert report["action"] == "refused_identity" and "campaign_id_mismatch" in report["identity_problems"]
    cfg.failover_expected_database = "not-this-database"
    with postgres() as db:
        db.get(PipelineState, 1).campaign_id = cfg.campaign_id
        assert failover.verify_identity(db, cfg) == ["database_name_mismatch"]
        assert "migration_head_mismatch" in failover.verify_identity(db, cfg, expected_head="9999_future")
    cfg.failover_expected_database = ""
    cfg.failover_enabled = False
    _s, report = failover.cycle(cfg, failover.default_state(), client=down(), session_factory=postgres)
    assert report["action"] == "observe_only_failover_disabled"
    cfg.executor_role = "cloud"
    with postgres() as db:
        assert "executor_role_not_device" in failover.verify_identity(db, cfg)
        assert db.query(SourceRecord).count() == 1


@pytest.mark.integration
def test_handback_after_cloud_recovers(postgres, isolated_settings):
    from app.models import PipelineState, ProviderRateState, now

    jobs.initialize()
    cfg = configure(isolated_settings, failure=1, recovery=2)
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        stale_cloud(db, 3600)
    state, _ = failover.cycle(cfg, failover.default_state(), client=down(), session_factory=postgres)
    assert state["mode"] == "device"
    with postgres() as db:  # Render resumes ticking
        db.merge(ProviderRateState(provider="executor_cloud", state={"last_tick_at": now().timestamp()}))
    state, report = failover.cycle(cfg, state, client=http(200), session_factory=postgres)
    assert state["mode"] == "device"  # one healthy observation is not enough
    state, report = failover.cycle(cfg, state, client=http(200), session_factory=postgres)
    assert state["mode"] == "cloud" and report["action"] == "observe" and report["jobs"] == []


@pytest.mark.integration
def test_cloud_executor_records_its_own_heartbeat(postgres, isolated_settings):
    from app.models import ProviderRateState

    jobs.initialize()
    assert isolated_settings.executor_role == "cloud"
    jobs.run_next()
    with postgres() as db:
        state = db.get(ProviderRateState, "executor_cloud").state
    assert state["ticks"] == 1 and state["last_lock_at"] == state["last_tick_at"]
    assert set(state) == {"last_tick_at", "ticks", "last_lock_at"}  # no host names or payloads


def test_stale_threshold_uses_configuration(isolated_settings):
    cfg = configure(isolated_settings)
    cfg.failover_stall_seconds = int(timedelta(minutes=20).total_seconds())
    assert failover.assess(True, {"cloud_heartbeat_age": 1199, "work_due": True}, cfg).cloud_healthy
    assert not failover.assess(True, {"cloud_heartbeat_age": 1201, "work_due": True}, cfg).cloud_healthy


def test_device_requires_external_database_url_with_tls():
    assert failover.database_url_problems("postgresql://u:p@dpg-abc123-a/db") == [
        "database_url_internal_host", "database_tls_not_required"]
    assert failover.database_url_problems("postgresql://u:p@dpg-abc123-a.oregon-postgres.render.com/db") == [
        "database_tls_not_required"]
    assert failover.database_url_problems(
        "postgresql://u:p@dpg-abc123-a.oregon-postgres.render.com/db?sslmode=require") == []
    assert failover.database_url_problems("postgresql+psycopg://postgres@127.0.0.1:5432/test") == []


@pytest.mark.integration
def test_cloud_connection_loss_mid_job_is_reclaimed_once_by_device(postgres, isolated_settings):
    import os

    from sqlalchemy import create_engine, text

    from app.models import Job, PipelineState, SourceRecord

    jobs.initialize()
    cfg = configure(isolated_settings, failure=1)
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        seed_raw(db, 2)
        # The cloud claimed the job (RUNNING, attempt 1) and then lost its connection mid-stage.
        db.add(Job(idempotency_key="fo-crash-1", kind="normalize", batch_size=10, status="RUNNING", attempts=1))
        stale_cloud(db, 3600)
    cloud = create_engine(os.environ["TEST_DATABASE_URL"]).connect()
    assert cloud.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": jobs.EXECUTOR_LOCK})
    cloud.commit()
    _s, report = failover.cycle(cfg, failover.default_state(), client=down(), session_factory=postgres)
    assert report["jobs"] == ["busy"]  # while the cloud session lives, the device never runs the job
    cloud.invalidate()  # real connection loss (DBAPI connection closed) releases the advisory lock
    _s, report = failover.cycle(cfg, _s, client=down(), session_factory=postgres)
    assert report["jobs"][0] == "complete"
    with postgres() as db:
        job = db.query(Job).filter(Job.idempotency_key == "fo-crash-1").one()
        assert job.status == "COMPLETE" and job.attempts == 2
        assert db.query(SourceRecord).count() == 0
        assert db.query(Job).filter(Job.kind == "normalize", Job.status.in_(["QUEUED", "RUNNING"])).count() == 0
