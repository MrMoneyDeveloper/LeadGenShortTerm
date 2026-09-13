import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker, { authorized, runCoordinator } from "../src/worker.mjs";

const env = {
  COORDINATOR_ENABLED: "true",
  ENVIRONMENT: "test",
  SCHEDULER_OWNER: "cloudflare",
  RENDER_BASE_URL: "https://isolated-render.example",
  DASHBOARD_API_TOKEN: "fixture-dashboard-token-xxxxxxxxxxxxxxxxxxxx",
  PROCESSOR_TRIGGER_TOKEN: "fixture-processor-token-xxxxxxxxxxxxxxxxxxxx",
  COORDINATOR_TRIGGER_TOKEN: "fixture-coordinator-token-xxxxxxxxxxxxxxxxxx",
};

function status(mode = "collect", extra = {}) {
  return { mode, counts: { raw: 10, candidates: 2, exports: 1 }, ...extra };
}

function fixture(responses) {
  const calls = [];
  return {
    calls,
    fetch: async (url, init) => {
      calls.push({ url, ...init });
      assert.ok(calls.length <= responses.length, "No unexpected outbound requests");
      const result = responses[calls.length - 1];
      if (result instanceof Error) throw result;
      if (typeof result === "function") return result(url, init);
      return new Response(JSON.stringify(result.body || {}), { status: result.status || 200 });
    },
    now: () => 300_000,
  };
}

test("checked-in Worker has no triggers, public routes or enabled schedules", async () => {
  const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
  assert.deepEqual(config.triggers.crons, []);
  assert.equal(config.vars.COORDINATOR_ENABLED, "false");
  assert.equal(config.vars.SCHEDULER_OWNER, "none");
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
  assert.equal(config.routes, undefined);
});

test("disabled and incomplete environments make no outbound calls", async () => {
  for (const config of [
    {}, { ...env, COORDINATOR_ENABLED: "false" }, { ...env, COORDINATOR_ENABLED: "TRUE" },
    { ...env, ENVIRONMENT: "" }, { ...env, SCHEDULER_OWNER: "render" },
    { ...env, ENVIRONMENT: "production" }, { ...env, DASHBOARD_API_TOKEN: "short" },
  ]) {
    const fake = fixture([]);
    const result = await runCoordinator(config, fake);
    assert.ok(["disabled", "misconfigured"].includes(result.status));
    assert.equal(result.requests, 0);
    assert.equal(fake.calls.length, 0);
  }
});

test("HTTPS origin rejects paths, embedded credentials and query destinations", async () => {
  for (const origin of [
    "http://isolated-render.example", "https://username:password@isolated-render.example",
    "https://isolated-render.example/other", "https://isolated-render.example/?redirect=evil",
    "https://isolated-render.example/#fragment", "invalid",
  ]) {
    const result = await runCoordinator({ ...env, RENDER_BASE_URL: origin }, fixture([]));
    assert.deepEqual(result, { status: "misconfigured", code: "invalid_configuration", requests: 0 });
  }
});

test("healthy Render receives exactly one bounded tick and separated authentication", async () => {
  const fake = fixture([
    { body: { status: "ok" } }, { body: status() }, { status: 202, body: { secret: env.PROCESSOR_TRIGGER_TOKEN } },
  ]);
  const result = await runCoordinator(env, fake);
  assert.deepEqual(result, { status: "tick_requested", ...status(), export_required: true, requests: 3 });
  assert.deepEqual(fake.calls.map((call) => [new URL(call.url).pathname, call.method]), [
    ["/health", "GET"], ["/dashboard/backpressure", "GET"], ["/jobs/tick", "POST"],
  ]);
  assert.equal(fake.calls[0].headers.Authorization, undefined);
  assert.equal(fake.calls[1].headers.Authorization, `Bearer ${env.DASHBOARD_API_TOKEN}`);
  assert.equal(fake.calls[2].headers.Authorization, `Bearer ${env.PROCESSOR_TRIGGER_TOKEN}`);
  assert.equal(fake.calls[2].headers["Idempotency-Key"], "cloudflare:test:1");
  assert.ok(fake.calls.every((call) => call.redirect === "manual" && call.signal instanceof AbortSignal));
});

test("cold-start health failure stops without backlog read or retry", async () => {
  const fake = fixture([{ status: 503, body: { password: env.DASHBOARD_API_TOKEN } }]);
  assert.deepEqual(await runCoordinator(env, fake), {
    status: "wake_pending", code: "render_health_unavailable", http_status: 503, requests: 1,
  });
});

test("network timeouts cancel one attempt, never loop or reveal exception text", async () => {
  let aborted = false;
  const fake = fixture([(_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => {
      aborted = true;
      reject(new Error(`private URL token ${env.DASHBOARD_API_TOKEN}`));
    }, { once: true });
  })]);
  const result = await runCoordinator(env, { ...fake, requestTimeoutMs: 2 });
  assert.equal(aborted, true);
  assert.deepEqual(result, { status: "wake_pending", code: "render_health_unavailable", requests: 1 });
});

test("redirect responses are not followed", async () => {
  const fake = fixture([{ status: 302 }]);
  const result = await runCoordinator(env, fake);
  assert.equal(result.status, "wake_pending");
  assert.equal(result.requests, 1);
});

test("untrusted queue modes, counts, huge bodies and denied tokens cannot trigger jobs", async () => {
  for (const reply of [
    { body: status("unknown") }, { body: status("collect", { counts: { raw: -1, candidates: 0, exports: 0 } }) },
    { body: status("collect", { counts: { raw: "10", candidates: 0, exports: 0 } }) },
    { body: status("collect", { counts: { raw: 0 } }) },
    { body: { ...status(), raw_payload: "x".repeat(16_385) } },
    { status: 401, body: { detail: env.DASHBOARD_API_TOKEN } },
    { status: 429, body: { detail: env.DASHBOARD_API_TOKEN } },
    () => new Response("invalid JSON"),
  ]) {
    const fake = fixture([{}, reply]);
    const result = await runCoordinator(env, fake);
    assert.equal(result.status, "upstream_unavailable");
    assert.equal(result.code, "backpressure_failed");
    assert.equal(result.requests, 2);
    assert.equal(JSON.stringify(result).includes(env.DASHBOARD_API_TOKEN), false);
  }
});

test("paused state does not request any jobs, but surfaces exports", async () => {
  const fake = fixture([{}, { body: status("paused") }]);
  const result = await runCoordinator(env, fake);
  assert.equal(result.status, "paused");
  assert.equal(result.requests, 2);
  assert.equal(result.export_required, true);
});

test("pressure, drain and deadline modes use only adaptive tick, never direct collection", async () => {
  for (const mode of ["drain", "storage_pressure", "campaign_complete"]) {
    const fake = fixture([{}, { body: status(mode) }, { status: 202 }]);
    const result = await runCoordinator(env, fake);
    assert.equal(result.mode, mode);
    assert.equal(result.status, "tick_requested");
    assert.equal(result.requests, 3);
    assert.equal(new URL(fake.calls[2].url).pathname, "/jobs/tick");
    assert.equal(fake.calls[2].body, undefined);
  }
});

test("tick errors and ambiguous timeouts do not retry or return provider content", async () => {
  for (const reply of [
    { status: 503, body: { detail: env.PROCESSOR_TRIGGER_TOKEN } },
    { status: 401 }, { status: 429 }, { status: 200 }, new Error(env.PROCESSOR_TRIGGER_TOKEN),
  ]) {
    const fake = fixture([{}, { body: status() }, reply]);
    const result = await runCoordinator(env, fake);
    assert.equal(result.status, "upstream_unavailable");
    assert.ok(["tick_failed", "tick_unconfirmed"].includes(result.code));
    assert.equal(result.requests, 3);
    assert.equal(JSON.stringify(result).includes(env.PROCESSOR_TRIGGER_TOKEN), false);
  }
});

test("production requires explicit environment, enablement and scheduler ownership", async () => {
  const fake = fixture([{}, { body: status() }, { status: 202 }]);
  const result = await runCoordinator({ ...env, ENVIRONMENT: "production", COORDINATOR_ALLOW_PRODUCTION: "true" }, fake);
  assert.equal(result.status, "tick_requested");
  assert.equal(fake.calls[2].headers["Idempotency-Key"], "cloudflare:production:1");
});

test("manual authorization rejects absent, invalid and wrong-length bearer tokens", () => {
  for (const value of ["", "Basic token", "Bearer short", `Bearer ${"x".repeat(5000)}`]) {
    const request = new Request("https://worker.example/tick", { headers: { Authorization: value } });
    assert.equal(authorized(request, env.COORDINATOR_TRIGGER_TOKEN), false);
  }
  const request = new Request("https://worker.example/tick", {
    headers: { Authorization: `Bearer ${env.COORDINATOR_TRIGGER_TOKEN}` },
  });
  assert.equal(authorized(request, env.COORDINATOR_TRIGGER_TOKEN), true);
  assert.equal(authorized(request, ""), false);
  assert.equal(authorized(request, undefined), false);
});

test("manual routes are authenticated; health never contacts Render", async (t) => {
  const fake = fixture([]);
  t.mock.method(globalThis, "fetch", fake.fetch);
  for (const [path, method, expected] of [
    ["/health", "GET", 200], ["/unknown", "GET", 404], ["/tick", "GET", 405], ["/tick", "POST", 401],
  ]) {
    const result = await worker.fetch(new Request(`https://worker.example${path}`, { method }), env);
    assert.equal(result.status, expected);
  }
  const result = await worker.fetch(new Request("https://worker.example/tick", {
    method: "POST", headers: { Authorization: `Bearer ${env.COORDINATOR_TRIGGER_TOKEN}` },
  }), { ...env, COORDINATOR_ENABLED: "false" });
  assert.equal(result.status, 200);
  assert.equal((await result.json()).status, "disabled");
  assert.equal(fake.calls.length, 0);
});

test("manual request body cannot bypass server queue mode or change request budget", async (t) => {
  const fake = fixture([{}, { body: status("storage_pressure") }, { status: 202 }]);
  t.mock.method(globalThis, "fetch", fake.fetch);
  const result = await worker.fetch(new Request("https://worker.example/tick", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.COORDINATOR_TRIGGER_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "collect", batch_size: 200000, max_requests: 1000, source: "youtube" }),
  }), env);
  assert.equal(result.status, 202);
  const body = await result.json();
  assert.equal(body.mode, "storage_pressure");
  assert.equal(body.requests, 3);
  assert.equal(fake.calls[2].body, undefined);
});

test("manual cold-start response is structured and returns 503 without retry", async (t) => {
  const fake = fixture([new Error(env.PROCESSOR_TRIGGER_TOKEN)]);
  t.mock.method(globalThis, "fetch", fake.fetch);
  const result = await worker.fetch(new Request("https://worker.example/tick", {
    method: "POST", headers: { Authorization: `Bearer ${env.COORDINATOR_TRIGGER_TOKEN}` },
  }), env);
  assert.equal(result.status, 503);
  assert.equal(result.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await result.json(), { status: "wake_pending", code: "render_health_unavailable", requests: 1 });
});

test("scheduled handler logs only safe status and performs no calls while disabled", async (t) => {
  const fake = fixture([]);
  t.mock.method(globalThis, "fetch", fake.fetch);
  const log = t.mock.method(console, "log", () => {});
  const pending = [];
  await worker.scheduled({ scheduledTime: 300_000 }, { ...env, COORDINATOR_ENABLED: "false" }, {
    waitUntil: (promise) => pending.push(promise),
  });
  await Promise.all(pending);
  assert.equal(fake.calls.length, 0);
  assert.equal(log.mock.calls.length, 1);
  assert.deepEqual(JSON.parse(log.mock.calls[0].arguments[0]), {
    event: "coordinator_tick", status: "disabled", requests: 0,
  });
});

test("scheduled failure logging omits all secrets and untrusted raw response fields", async (t) => {
  const fake = fixture([
    {}, { body: { ...status(), secret: env.DASHBOARD_API_TOKEN } }, new Error(env.PROCESSOR_TRIGGER_TOKEN),
  ]);
  t.mock.method(globalThis, "fetch", fake.fetch);
  const log = t.mock.method(console, "log", () => {});
  const pending = [];
  await worker.scheduled({ scheduledTime: 300_000 }, env, { waitUntil: (promise) => pending.push(promise) });
  await Promise.all(pending);
  const text = log.mock.calls[0].arguments[0];
  assert.equal(JSON.parse(text).code, "tick_unconfirmed");
  for (const secret of [env.DASHBOARD_API_TOKEN, env.PROCESSOR_TRIGGER_TOKEN, env.COORDINATOR_TRIGGER_TOKEN]) {
    assert.equal(text.includes(secret), false);
  }
  assert.equal(text.includes("secret"), false);
});
