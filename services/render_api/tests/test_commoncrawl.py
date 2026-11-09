"""Common Crawl business-contact source: adapter, business-contact route, assist mode, campaign stop."""

import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import failover, jobs  # noqa: F401 - loaded before the postgres fixture patches sessions
from app.collectors import commoncrawl
from app.models import now
from app.qualification import score
from app.ranking import rank
from app.services import campaign, pipeline  # noqa: F401
from app.validators import validate_contact

CRAWL = "CC-MAIN-2099-01"
STAMP = now() - timedelta(days=5)


def warc(html):
    body = html.encode()
    http = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + body
    head = (b"WARC/1.0\r\nWARC-Type: response\r\nWARC-Date: "
            + STAMP.strftime("%Y-%m-%dT%H:%M:%SZ").encode() + b"\r\n\r\n")
    return gzip.compress(head + http)


PAGES = {
    # Own-domain business mailboxes are kept; a named person, a web designer's address and a
    # non-.za address on the same page are never collected.
    "https://acme.co.za/": "<title>Acme Plumbing</title> info@acme.co.za | sales@acme.co.za | "
                           "john.smith@acme.co.za | site by info@webdesigner.co.za | info@acme.com",
    "https://beta.co.za/": "<title>Beta</title> call us",
    "https://www.gamma.org.za/contact-us": "<title>Gamma Trust</title> Email: Enquiries@Gamma.org.za.",
    "https://delta.co.za/": "<title>Delta Insurance Brokers</title> info@delta.co.za",
}


class Fake:
    """data.commoncrawl.org: cluster.idx, one CDX block and WARC records, all by byte range."""

    def __init__(self, throttle=0):
        self.throttle, self.calls = throttle, []
        warcs, captures = b"", []
        for url, html in PAGES.items():
            record = warc(html)
            surt = "za," + ",".join(reversed(commoncrawl.host_of(url).split(".")))[3:] + ")/"
            captures.append((surt, {"url": url, "status": "200", "mime": "text/html", "filename": "warc/1.gz",
                                    "offset": str(len(warcs)), "length": str(len(record))}))
            warcs += record
        captures.append(("za,co,acme)/logo.png", {"url": "https://acme.co.za/logo.png", "status": "200",
                                                  "mime": "image/png", "filename": "warc/1.gz", "offset": "0",
                                                  "length": "10"}))
        captures.append(("za,co,beta)/gone", {"url": "https://beta.co.za/gone", "status": "404",
                                              "mime": "text/html", "filename": "warc/1.gz", "offset": "0",
                                              "length": "10"}))
        cdx = "".join(f"{surt} 20261020 {json.dumps(item)}\n" for surt, item in sorted(captures, key=lambda c: c[0]))
        block = gzip.compress(cdx.encode())
        lines = [f"aa,x{i:05d})/ 2026\tcdx-00000.gz\t0\t10\t{i}" for i in range(3000)]
        lines += [f"za,co,acme)/ 2026\tcdx-00001.gz\t0\t{len(block)}\t3000"]
        lines += [f"zz,x{i:05d})/ 2026\tcdx-00002.gz\t0\t10\t{3001 + i}" for i in range(50)]
        self.files = {
            f"cc-index/collections/{CRAWL}/indexes/cluster.idx": ("\n".join(lines) + "\n").encode(),
            f"cc-index/collections/{CRAWL}/indexes/cdx-00001.gz": block,
            # The block before the .za section holds other keys only.
            f"cc-index/collections/{CRAWL}/indexes/cdx-00000.gz": gzip.compress(b"aa,x)/ 2026 {}\n"),
            "warc/1.gz": warcs,
        }

    def __call__(self, request):
        path = request.url.path.lstrip("/")
        self.calls.append(path)
        data = self.files.get(path)
        if data is None:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(data))})
        if self.throttle and path.startswith("warc/"):
            self.throttle -= 1
            return httpx.Response(503)
        start, end = request.headers["range"].removeprefix("bytes=").split("-")
        return httpx.Response(206, content=data[int(start): int(end) + 1])


@pytest.fixture
def source(monkeypatch, isolated_settings):
    from app.config import rules

    base = rules("sources")["commoncrawl"]
    cfg = {**base, "crawl": CRAWL, "concurrency": 2, "pages_per_host": 1}
    monkeypatch.setattr(commoncrawl, "rules", lambda name: {"commoncrawl": cfg})
    monkeypatch.setattr(commoncrawl.time, "sleep", lambda _s: None)

    def build(fake):
        return commoncrawl.CommonCrawl(client=httpx.Client(transport=httpx.MockTransport(fake)))
    return build


def test_collects_only_own_domain_business_mailboxes(source):
    batch = source(Fake()).collect({}, 100)
    assert [r.identity for r in batch.records] == ["acme.co.za", "delta.co.za", "gamma.org.za"]
    acme = batch.records[0]
    assert acme.text.endswith("Published contact: info@acme.co.za, sales@acme.co.za")
    assert "john" not in acme.text and "webdesigner" not in acme.text and "acme.com" not in acme.text
    assert acme.url == "https://acme.co.za/" and acme.business_name == "Acme Plumbing"
    assert acme.published_at.tzinfo is not None and abs((acme.published_at - STAMP).total_seconds()) < 2
    assert "enquiries@gamma.org.za" in batch.records[2].text  # case folded, trailing dot dropped
    assert batch.scanned == 4  # one HTML page per .za host; image and 404 captures skipped
    assert batch.stop_reason == "discovery_complete" and batch.exhausted and batch.cursor["complete"]
    again = source(Fake()).collect(batch.cursor, 100)
    assert again.scanned == 0 and again.stop_reason == "discovery_complete"


def test_limit_is_hard_and_cursor_resumes_mid_block(source):
    fake = Fake()
    first = source(fake).collect({}, 1)
    assert first.scanned == 1 and first.cursor["host"] == 1 and not first.cursor.get("complete")
    second = source(fake).collect(first.cursor, 2)
    assert second.scanned == 2 and second.cursor["host"] == 3
    third = source(fake).collect(second.cursor, 100)
    hosts = [r.identity for b in (first, second, third) for r in b.records]
    assert hosts == ["acme.co.za", "delta.co.za", "gamma.org.za"]  # nothing repeated or skipped
    assert third.stop_reason == "discovery_complete"


def test_throttling_is_retried_politely(source):
    batch = source(Fake(throttle=2)).collect({}, 100)
    assert len(batch.records) == 3


def test_title_addresses_and_bare_suffixes_never_leak():
    cfg = {"business_locals": ["info"], "max_emails_per_host": 3}
    assert commoncrawl.business_emails("info@co.za info@acme.co.za", "acme.co.za", cfg) == ["info@acme.co.za"]
    body = (b"<title>Jane Doe jane.doe@acme.co.za | info@acme.co.za</title> info@acme.co.za")
    record = commoncrawl.CommonCrawl._record("acme.co.za", [({"url": "https://acme.co.za/"}, STAMP, body)], cfg)
    assert "jane.doe" not in record.text and record.text.count("@") == 1
    assert "@" not in record.business_name


class ThrottleOne(Fake):
    """Answers 503 for the first ``n`` requests of one page (the acme home page)."""

    def __init__(self, n):
        super().__init__()
        self.n = n

    def __call__(self, request):
        if request.url.path.endswith("warc/1.gz") and request.headers["range"].startswith("bytes=0-") and self.n:
            self.n -= 1
            return httpx.Response(503)
        return super().__call__(request)


def test_throttled_host_is_queued_and_retried_never_skipped(source):
    fake = ThrottleOne(3)  # acme fails all 3 attempts in the first batch
    first = source(fake).collect({}, 100)
    assert [r.identity for r in first.records] == ["delta.co.za", "gamma.org.za"]  # batch kept going
    assert [e["host"] for e in first.cursor["retry"]] == ["acme.co.za"]
    second = source(fake).collect(first.cursor, 100)
    assert [r.identity for r in second.records] == ["acme.co.za"] and second.cursor["retry"] == []


def test_persistently_throttled_host_is_dropped_after_bounded_tries(source):
    fake = ThrottleOne(10_000)
    batch = source(fake).collect({}, 100)
    for _ in range(commoncrawl.RETRY_TRIES):
        batch = source(fake).collect(batch.cursor, 100)
    assert batch.cursor["retry"] == [] and batch.stop_reason in {"retry_exhausted", "discovery_complete"}


@pytest.mark.integration
def test_business_validation_never_feeds_the_consumer_cache(postgres, isolated_settings, monkeypatch):
    from app.models import Candidate, EmailValidation

    isolated_settings.processing_enabled = True
    seen = []
    monkeypatch.setattr(pipeline, "validate_contact", lambda email, **kw: seen.append(kw) or (
        ("DELIVERABLE_DOMAIN", "syntax_and_mx_passed") if kw.get("allow_role") else ("RISKY", "role_account")))
    with postgres() as db:
        for n, product in enumerate(("BUSINESS_CONTACT", "MOTOR")):
            db.add(Candidate(source="commoncrawl" if n == 0 else "bluesky", source_url="https://acme.co.za/",
                             identity_hash=str(n) * 64, excerpt="x", profile={}, product_type=product, score=50,
                             status="VALIDATE", contacts=[{"email": "info@acme.co.za",
                                                           "source_url": "https://acme.co.za/"}]))
    pipeline.validate_batch(5)
    with postgres() as db:
        contacts = {c.product_type: c.contacts for c in db.query(Candidate)}
        cached = db.query(EmailValidation).one()
    assert contacts["BUSINESS_CONTACT"][0]["status"] == "DELIVERABLE_DOMAIN"
    assert contacts["MOTOR"] == []  # consumer path still refuses the role mailbox (contact dropped)
    assert cached.reason == "role_account" and seen == [{"allow_role": True}, {}]


def test_changed_crawl_restarts_cursor(source):
    batch = source(Fake()).collect({"crawl": "CC-MAIN-2000-01", "complete": True, "offset": 5}, 100)
    assert batch.cursor["crawl"] == CRAWL and len(batch.records) == 3


# --- business-contact qualification -------------------------------------------------------------

def test_business_contact_route_is_direct_and_never_semantic():
    text = "Business website acme.co.za (Acme Plumbing). Published contact: info@acme.co.za"
    result = score(text, "commoncrawl", now())
    assert result["product_type"] == "BUSINESS_CONTACT" and not result["rejected"]
    assert result["geography_evidence"] == "email_domain" and "south_africa" in result["signals"]
    assert rank(result, True)["route"] == "DIRECT_FINAL"
    assert rank(result, False)["route"] == "REJECT"  # no validated mailbox: never a lead
    broker = score("Business website x.co.za (Delta Insurance Brokers). Published contact: info@x.co.za",
                   "commoncrawl", now())
    assert broker["rejected"] and rank(broker, True)["route"] == "REJECT"
    foreign = score("Business website x.com (X). Published contact: info@x.com", "commoncrawl", now())
    assert foreign["rejected"]
    old = score(text, "commoncrawl", now() - timedelta(days=500))
    assert old["rejected"]


def test_consumer_sources_are_unaffected():
    text = "Business website acme.co.za (Acme Plumbing). Published contact: info@acme.co.za"
    result = score(text, "bluesky", now())
    assert result["product_type"] != "BUSINESS_CONTACT" and result["rejected"]


def test_role_mailbox_allowed_only_on_the_business_path():
    class Resolver:
        lifetime = timeout = 0

        def resolve(self, domain, kind):
            class Answer:
                exchange = "mx.example."
            return [Answer()]

    assert validate_contact("info@acme.co.za", Resolver()) == ("RISKY", "role_account")
    assert validate_contact("info@acme.co.za", Resolver(), allow_role=True) == ("DELIVERABLE_DOMAIN",
                                                                               "syntax_and_mx_passed")
    assert validate_contact("info@mailinator.com", Resolver(), allow_role=True)[1] == "disposable_domain"


def test_recovery_rebuilds_commoncrawl_keys():
    from app.qualification import digest
    from app.services.recovery import keys_for_values

    keys = keys_for_values({"email": "info@acme.co.za", "source": "commoncrawl",
                            "source_url": "https://www.acme.co.za/contact"})
    assert {"record:" + digest("commoncrawl:acme.co.za"), "lead:" + digest("commoncrawl:acme.co.za")} <= keys


@pytest.mark.integration
def test_business_contact_end_to_end_without_groq(postgres, isolated_settings, monkeypatch):
    from app.collectors.base import Batch, Record
    from app.models import Candidate, Job, Lead, SourceCursor

    isolated_settings.processing_enabled = True
    calls = []
    monkeypatch.setattr(pipeline.semantic, "classify", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(pipeline, "validate_contact",
                        lambda email, **kw: ("DELIVERABLE_DOMAIN", "syntax_and_mx_passed") if kw.get("allow_role")
                        else ("RISKY", "role_account"))

    class Fake:
        def collect(self, cursor, limit):
            return Batch(records=[Record(
                source_record_id="acme.co.za", identity="acme.co.za", url="https://acme.co.za/",
                text="Business website acme.co.za (Acme Plumbing). Published contact: info@acme.co.za",
                published_at=datetime.now(UTC) - timedelta(days=3), business_name="Acme Plumbing")],
                cursor={"offset": 1}, scanned=1)

    monkeypatch.setitem(pipeline.ADAPTERS, "commoncrawl", Fake)
    with postgres() as db:
        db.add(SourceCursor(source="commoncrawl", enabled=True))
        job = Job(idempotency_key="cc-collect", kind="collect", source="commoncrawl", batch_size=10)
        db.add(job)
        db.flush()
    pipeline.collect(job)
    pipeline.normalize_batch(10)
    pipeline.validate_batch(10)
    pipeline.classify_batch(10)
    with postgres() as db:
        lead = db.query(Lead).one()
        assert lead.email == "info@acme.co.za" and lead.product_type == "BUSINESS_CONTACT"
        assert lead.validation_status == "DELIVERABLE_DOMAIN" and lead.profile["business_name"] == "Acme Plumbing"
        assert db.query(Candidate).filter(Candidate.status != "VALIDATED").count() == 0
    assert calls == []  # business contacts never use Groq


@pytest.mark.integration
def test_business_contact_without_valid_mailbox_is_rejected_not_no_email(postgres, isolated_settings, monkeypatch):
    from app.models import Candidate, Classification, Lead

    isolated_settings.processing_enabled = True
    monkeypatch.setattr(pipeline, "validate_contact", lambda email, **kw: ("NO_MX", "no_mx"))
    with postgres() as db:
        candidate = Candidate(source="commoncrawl", source_url="https://acme.co.za/", identity_hash="c" * 64,
                              excerpt="x", contacts=[{"email": "info@acme.co.za", "source_url": "https://acme.co.za/"}],
                              profile={}, product_type="BUSINESS_CONTACT", score=50, status="VALIDATE")
        db.add(candidate)
        db.flush()
        db.add(Classification(candidate_id=candidate.id, provider="rules", version="rules-v4",
                              result=score("Published contact: info@acme.co.za", "commoncrawl", now())))
    pipeline.validate_batch(5)
    with postgres() as db:
        assert db.query(Lead).count() == 0
        assert db.query(Candidate).one().status == "CONTACT_REVIEW"  # never a NO_EMAIL prospect


# --- assist mode, timed run and operator campaign stop ------------------------------------------

def configure(cfg, assist):
    import os

    from pydantic import SecretStr

    cfg.render_base_url, cfg.environment, cfg.executor_role = "https://render.example", "test", "device"
    cfg.failover_enabled, cfg.failover_assist, cfg.processing_enabled = True, assist, True
    cfg.failover_stall_seconds, cfg.failover_max_jobs_per_cycle = 600, 3
    cfg.database_url = SecretStr(os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1/test"))
    return cfg


def healthy_cloud(db):
    from app.models import ProviderRateState

    db.merge(ProviderRateState(provider="executor_cloud", state={"last_tick_at": now().timestamp()}))


@pytest.mark.integration
@pytest.mark.parametrize("assist", [False, True])
def test_assist_mode_processes_alongside_a_healthy_cloud(postgres, isolated_settings, assist):
    from app.models import Job, PipelineState, SourceRecord

    jobs.initialize()
    cfg = configure(isolated_settings, assist)
    with postgres() as db:
        db.get(PipelineState, 1).paused = False
        db.add(SourceRecord(source="bluesky", source_record_id="a-1", source_url="https://example.invalid/1",
                            identity_hash="1" * 64, content_hash="2" * 64, text="weather chat only",
                            published_at=now()))
        db.add(Job(idempotency_key="assist-normalize", kind="normalize", source=None, batch_size=10))
        healthy_cloud(db)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    state, report = failover.cycle(cfg, failover.default_state(), client=client, session_factory=postgres)
    assert state["mode"] == "cloud" and report["assessment"]["reason"] == "cloud_healthy"
    with postgres() as db:
        remaining = db.query(SourceRecord).count()
    if assist:
        assert report["action"] == "assist_processed" and "complete" in report["jobs"] and remaining == 0
    else:
        assert report["action"] == "observe" and report["jobs"] == [] and remaining == 1


def test_timed_run_stops_by_itself(isolated_settings, monkeypatch, tmp_path):
    cfg = configure(isolated_settings, False)
    cfg.failover_interval_seconds = 15
    for name, value in (("STATE_DIR", tmp_path), ("STATE_FILE", tmp_path / "s.json"),
                        ("STOP_FILE", tmp_path / "STOP"), ("LOCK_FILE", tmp_path / "l.lock")):
        monkeypatch.setattr(failover, name, value)
    clock = [1000.0]
    monkeypatch.setattr(failover, "safe_cycle", lambda cfg, state, **kw: (state, {"jobs": []}))

    def sleep(_s):
        clock[0] += 1
    failover.run(cfg, sleep=sleep, until=1000.0 + 40, clock=lambda: clock[0])
    assert 1040 <= clock[0] <= 1041  # ended at the deadline, not after another full interval


@pytest.mark.integration
def test_finish_campaign_drains_and_keeps_counters(postgres, isolated_settings):
    from app.models import PipelineState

    jobs.initialize()
    with postgres() as db:
        state = db.get(PipelineState, 1)
        state.campaign_id, state.campaign_status, state.campaign_raw_scanned = "old", "RUNNING", 42
        state.campaign_started_at = now()
    result = campaign.finish()
    assert result["status"] == "DRAINING" and result["raw_scanned"] == 42
    with postgres() as db:
        state = db.get(PipelineState, 1)
        assert state.draining and state.campaign_raw_scanned == 42
        campaign.observe(db, 0, 0)
        campaign.observe(db, 0, 0)
        assert state.campaign_status == "COMPLETE"
    assert campaign.finish()["status"] == "COMPLETE"  # idempotent: nothing else changes
