"""Stateless worker contract, Python/JavaScript parity, routing, circuit breaker and fallback."""

import csv
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.models import now
from app.qualification import match_terms, normalize, score, term_groups
from app.services import remote_workers

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "services" / "stateless_worker" / "src" / "core.cjs"
SECRET = "w" * 40
EDGE_CASES = [
    "discover coverage", "carinsurance or café", "éc ar car-cover", "_car", "2car", "car²",
    "need car insurance in joburg!", "bakkie cover, asap", "insurance's premium", "INSURANCE QUOTE",
    "straße insurance", "ΣΊΣΥΦΟΣ insurance", "car insurance", "car insurance™", "😀car insurance😀",
    "short-term insurance", "short term insurance", "home&nbsp;insurance", "x" * 50,
]


def corpus():
    with (Path(__file__).parent / "fixtures" / "short_term_quality.csv").open(encoding="utf-8") as f:
        texts = [row["text"] for row in csv.DictReader(f)]
    return [normalize(t) for t in texts + EDGE_CASES]


def js_match(texts, groups):
    script = (
        "const core=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
        "const {texts,groups}=JSON.parse(s);process.stdout.write(JSON.stringify(texts.map(t=>core.matchTerms(t,groups))))})"
    )
    out = subprocess.run(["node", "-e", script, str(CORE)], input=json.dumps({"texts": texts, "groups": groups}),
                         capture_output=True, text=True, encoding="utf-8", check=True, timeout=60)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for parity")
def test_javascript_worker_matches_python_exactly_on_quality_corpus():
    groups, texts = term_groups(), corpus()
    expected = [match_terms(t, groups) for t in texts]
    assert js_match(texts, groups) == expected
    assert sum(1 for e in expected if e) > 40  # the corpus genuinely exercises matching


def test_score_with_remote_matches_equals_local_score():
    published = now() - timedelta(hours=1)
    for text in corpus():
        assert score(text, "bluesky", published, match_terms(normalize(text), term_groups())) == \
            score(text, "bluesky", published)


def request():
    return remote_workers.build_request("job-1", [{"id": "r1", "text": "car insurance"},
                                                  {"id": "r2", "text": "nothing"}], term_groups())


def response(req, items=None, **overrides):
    body = {"contract": 1, "job_id": req["job_id"], "candidate_id": req["candidate_id"], "status": "ok",
            "result": {"items": items if items is not None else [{"id": "r1", "matched": ["product:MOTOR"]},
                                                                 {"id": "r2", "matched": []}]}}
    return {**body, **overrides}


def test_contract_response_validation_is_strict():
    req, groups = request(), term_groups()
    assert remote_workers.validate_response(req, response(req), groups) == {"r1": ["product:MOTOR"], "r2": []}
    bad = [
        ({**response(req), "extra": 1}, "worker_schema"),
        (response(req, job_id="other"), "worker_identity_mismatch"),
        (response(req, status="error"), "worker_identity_mismatch"),
        (response(req, items=[{"id": "r1", "matched": []}]), "worker_missing_id"),
        (response(req, items=[{"id": "r1", "matched": []}, {"id": "zz", "matched": []}]), "worker_unexpected_id"),
        (response(req, items=[{"id": "r1", "matched": ["invented"]}, {"id": "r2", "matched": []}]),
         "worker_unknown_group"),
        (response(req, items=[{"id": "r1", "matched": ["relevance", "relevance"]}, {"id": "r2", "matched": []}]),
         "worker_unknown_group"),
        (response(req, items=[{"id": "r1", "matched": [], "x": 1}, {"id": "r2", "matched": []}]), "worker_schema"),
    ]
    for data, code in bad:
        with pytest.raises(remote_workers.WorkerError, match=code):
            remote_workers.validate_response(req, data, groups)


def enable(cfg, vercel=True, netlify=True):
    cfg.remote_workers_enabled = True
    cfg.vercel_enabled, cfg.vercel_base_url, cfg.vercel_shared_secret = vercel, "https://v.example", SecretStr(SECRET)
    cfg.netlify_enabled, cfg.netlify_base_url = netlify, "https://n.example"
    cfg.netlify_shared_secret = SecretStr(SECRET)
    cfg.remote_worker_failure_threshold = 2


def test_disabled_workers_never_touch_network_or_database(isolated_settings):
    assert remote_workers.match_batch([("r1", "car insurance")], term_groups()) == ({}, None)
    isolated_settings.remote_workers_enabled = True  # enabled lane but no configured worker
    assert remote_workers.configured_workers(isolated_settings) == []


class Router:
    """httpx transport that emulates both remote workers using the real JavaScript-equivalent logic."""

    def __init__(self):
        self.calls, self.mode = [], {"v.example": "ok", "n.example": "ok"}

    def __call__(self, request):
        host = request.url.host
        self.calls.append(host)
        assert request.headers["authorization"] == "Bearer " + SECRET
        mode = self.mode[host]
        if mode == "down":
            raise httpx.ConnectError("down")
        if mode == "timeout":
            raise httpx.ReadTimeout("slow")
        if mode == "500":
            return httpx.Response(500, json={"status": "error"})
        body = json.loads(request.content)
        items = [{"id": i["id"], "matched": match_terms(i["text"], body["payload"]["groups"])}
                 for i in body["payload"]["items"]]
        if mode == "lie":
            items = [{"id": i["id"], "matched": []} for i in items]
        return httpx.Response(200, json={"contract": 1, "job_id": body["job_id"],
                                         "candidate_id": body["candidate_id"], "status": "ok",
                                         "result": {"items": items}})


@pytest.mark.integration
def test_router_prefers_vercel_then_falls_back_and_opens_circuit(postgres, isolated_settings):
    enable(isolated_settings)
    router = Router()
    client = httpx.Client(transport=httpx.MockTransport(router))
    records = [("r1", normalize("Need car insurance quote")), ("r2", normalize("weather today"))]
    groups = term_groups()

    matched, worker = remote_workers.match_batch(records, groups, client=client)
    assert worker == "vercel" and matched["r1"] == match_terms(records[0][1], groups) and matched["r2"] == []
    router.mode["v.example"] = "500"
    matched, worker = remote_workers.match_batch(records, groups, client=client)
    assert worker == "netlify"  # Vercel preferred; Netlify is the conservative fallback lane

    router.mode["v.example"] = "down"
    router.mode["n.example"] = "timeout"
    assert remote_workers.match_batch(records, groups, client=client) == ({}, None)  # caller runs locally
    assert remote_workers.match_batch(records, groups, client=client) == ({}, None)
    from app.database import session

    with session() as db:
        status = {s["worker"]: s for s in remote_workers.status(db)}
    assert status["vercel"]["circuit_open"] and status["netlify"]["circuit_open"]
    assert status["netlify"]["last_error"] == "worker_timeout"
    calls_before = len(router.calls)
    router.mode = {"v.example": "ok", "n.example": "ok"}
    assert remote_workers.match_batch(records, groups, client=client) == ({}, None)
    assert len(router.calls) == calls_before  # open circuits are not called at all


@pytest.mark.integration
def test_parity_mismatch_discards_result_and_opens_circuit(postgres, isolated_settings):
    enable(isolated_settings, netlify=False)
    router = Router()
    router.mode["v.example"] = "lie"
    client = httpx.Client(transport=httpx.MockTransport(router))
    records = [("r1", normalize("car insurance quote please"))]
    assert remote_workers.match_batch(records, term_groups(), client=client) == ({}, None)
    from app.database import session

    with session() as db:
        vercel = remote_workers.status(db)[0]
    assert vercel["circuit_open"] and vercel["mismatches_today"] == 1


@pytest.mark.integration
def test_daily_call_cap_routes_to_other_worker(postgres, isolated_settings):
    enable(isolated_settings)
    isolated_settings.vercel_daily_call_cap = 0
    client = httpx.Client(transport=httpx.MockTransport(Router()))
    assert remote_workers.match_batch([("r1", "car insurance")], term_groups(), client=client)[1] == "netlify"


@pytest.mark.integration
def test_normalize_stage_uses_worker_and_survives_worker_outage(postgres, isolated_settings, monkeypatch):
    from app.database import session
    from app.models import Candidate, Metric, SourceRecord
    from app.services import pipeline

    enable(isolated_settings)
    router = Router()
    real_client = httpx.Client
    monkeypatch.setattr(remote_workers.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(router)))
    text = normalize("Johannesburg: need a car insurance quote, my premium went up. mail me at a@example.org")
    for i, mode in enumerate(["ok", "down"]):
        router.mode = {"v.example": mode, "n.example": mode}
        with session() as db:
            db.add(SourceRecord(source="bluesky", source_record_id=f"r{i}", source_url="https://bsky.app/x",
                                identity_hash=f"id{i}", content_hash=f"c{i}", text=text, profile={},
                                published_at=now() - timedelta(hours=1)))
        assert pipeline.normalize_batch(5) == {"normalized": 1}
    with session() as db:
        assert db.query(Candidate).count() == 2  # same decision with and without the remote lane
        names = {m.name: m.value for m in db.query(Metric)}
    assert names.get("remote_vercel_matched") == 1


@pytest.mark.parametrize("bad_id", [["r1"], {"id": "r1"}, 1, None, True, 1.5])
def test_malformed_item_ids_are_contract_errors_not_type_errors(bad_id):
    req, groups = request(), term_groups()
    data = response(req, items=[{"id": bad_id, "matched": []}, {"id": "r2", "matched": []}])
    with pytest.raises(remote_workers.WorkerError, match="worker_unexpected_id"):
        remote_workers.validate_response(req, data, groups)


@pytest.mark.parametrize("field,value", [("contract", True), ("contract", 1.0), ("contract", "1"),
                                         ("job_id", ["job-1"]), ("candidate_id", {"x": 1})])
def test_envelope_field_types_are_strict(field, value):
    req = request()
    with pytest.raises(remote_workers.WorkerError):
        remote_workers.validate_response(req, response(req, **{field: value}), term_groups())


@pytest.mark.parametrize("payload", [
    lambda req: response(req, items=[{"id": ["r1"], "matched": []}, {"id": "r2", "matched": []}]),
    lambda req: response(req, items=[{"id": "r1", "matched": [["relevance"]]}, {"id": "r2", "matched": []}]),
    lambda req: response(req, items=[{"id": "r1", "matched": [{"g": 1}]}, {"id": "r2", "matched": []}]),
    lambda req: [response(req)],
    lambda req: "ok",
    lambda req: {**response(req), "result": None},
])
def test_any_malformed_worker_output_reaches_fallback(isolated_settings, payload, monkeypatch):
    """No malformed worker body may raise out of match_batch (which would roll back normalization)."""
    enable(isolated_settings, netlify=False)
    recorded = []
    monkeypatch.setattr(remote_workers, "eligible", lambda cfg=None: remote_workers.configured_workers(cfg))
    monkeypatch.setattr(remote_workers, "_record", lambda worker, ok, code=None, items=0, mismatch=False:
                        recorded.append((worker.name, ok, code)))

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(200, json=payload(body))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    records = [("r1", "car insurance quote"), ("r2", "weather")]
    assert remote_workers.match_batch(records, term_groups(), client=client) == ({}, None)
    assert recorded and recorded[0][0] == "vercel" and recorded[0][1] is False
    assert recorded[0][2].startswith("worker_")


def test_deeply_nested_or_non_json_body_reaches_fallback(isolated_settings, monkeypatch):
    enable(isolated_settings, netlify=False)
    recorded = []
    monkeypatch.setattr(remote_workers, "eligible", lambda cfg=None: remote_workers.configured_workers(cfg))
    monkeypatch.setattr(remote_workers, "_record", lambda worker, ok, code=None, items=0, mismatch=False:
                        recorded.append(code))
    bodies = iter([b"[" * 100000 + b"]" * 100000, b"<html>Authentication Required</html>"])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=next(bodies))))
    for _ in range(2):
        assert remote_workers.match_batch([("r1", "car insurance")], term_groups(), client=client) == ({}, None)
    assert recorded == ["worker_invalid_json", "worker_invalid_json"]


def test_redirects_and_protection_pages_are_failures_without_following(isolated_settings, monkeypatch):
    """Deployment protection/SSO pages must fail closed; the bearer secret is never sent elsewhere."""
    enable(isolated_settings, netlify=False)
    recorded, hosts = [], []
    monkeypatch.setattr(remote_workers, "eligible", lambda cfg=None: remote_workers.configured_workers(cfg))
    monkeypatch.setattr(remote_workers, "_record", lambda worker, ok, code=None, items=0, mismatch=False:
                        recorded.append(code))

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(307, headers={"location": "https://vercel.com/sso-api?url=x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert remote_workers.match_batch([("r1", "car insurance")], term_groups(), client=client) == ({}, None)
    assert recorded == ["worker_http_307"] and hosts == ["v.example"]
