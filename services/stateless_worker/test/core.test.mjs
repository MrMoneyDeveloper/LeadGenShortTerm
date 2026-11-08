import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const core = require("../src/core.cjs");
const netlify = require("../netlify/functions/worker.js");

const SECRET = "s".repeat(40);
const env = { secret: SECRET };
const groups = {
  "product:MOTOR": ["car insurance", "bakkie cover"],
  "signal:quote": ["quote"],
  relevance: ["insurance"],
};

function request(overrides = {}) {
  return {
    contract: 1,
    job_id: "job-1",
    candidate_id: "batch_1",
    operation: "match_terms",
    payload: { groups, items: [{ id: "r1", text: "need a car insurance quote" }, { id: "r2", text: "nothing here" }] },
    ...overrides,
  };
}

function call(body, headers = { Authorization: `Bearer ${SECRET}` }, method = "POST") {
  const res = core.handle({ method, headers, body: typeof body === "string" ? body : JSON.stringify(body) }, env);
  return { status: res.status, body: JSON.parse(res.body) };
}

test("valid auth returns matched groups by stable id", () => {
  const res = call(request());
  assert.equal(res.status, 200);
  assert.deepEqual(res.body, {
    contract: 1, job_id: "job-1", candidate_id: "batch_1", status: "ok",
    result: { items: [{ id: "r1", matched: ["product:MOTOR", "signal:quote", "relevance"] }, { id: "r2", matched: [] }] },
  });
});

test("invalid, missing and malformed auth are rejected without echoing input", () => {
  for (const headers of [{}, { Authorization: "Bearer wrong" }, { Authorization: SECRET }]) {
    const res = call(request(), headers);
    assert.equal(res.status, 401);
    assert.deepEqual(res.body, { contract: 1, status: "error", code: "unauthorized" });
  }
});

test("worker without a strong secret refuses work", () => {
  const res = core.handle({ method: "POST", headers: { authorization: "Bearer x" }, body: "{}" }, { secret: "short" });
  assert.equal(res.status, 503);
});

test("malformed input and strict schema", () => {
  assert.equal(call("{not json").status, 400);
  assert.equal(call({ ...request(), extra: 1 }).body.code, "schema");
  assert.equal(call(request({ operation: "delete_everything" })).body.code, "unsupported_operation");
  assert.equal(call(request({ contract: 2 })).body.code, "contract_version");
  const dup = request();
  dup.payload.items = [{ id: "a", text: "x" }, { id: "a", text: "y" }];
  assert.equal(call(dup).body.code, "schema");
  const badId = request();
  badId.payload.items = [{ id: "../etc", text: "x" }];
  assert.equal(call(badId).body.code, "schema");
});

test("bounded payloads", () => {
  const big = request();
  big.payload.items = [{ id: "a", text: "x".repeat(core.LIMITS.maxTextChars + 1) }];
  assert.equal(call(big).status, 413);
  const many = request();
  many.payload.items = Array.from({ length: core.LIMITS.maxItems + 1 }, (_, i) => ({ id: `i${i}`, text: "x" }));
  assert.equal(call(many).status, 413);
  const huge = "x".repeat(core.LIMITS.maxBodyBytes + 1);
  assert.equal(call(huge).status, 413);
});

test("duplicate requests are idempotent", () => {
  assert.deepEqual(call(request()), call(request()));
});

test("unicode word boundaries mirror Python", () => {
  const g = { cover: ["cover"], car: ["car"] };
  assert.deepEqual(core.matchTerms("discover coverage", g), []);
  assert.deepEqual(core.matchTerms("carinsurance or café", g), []);
  assert.deepEqual(core.matchTerms("éc ar car-cover", g), ["cover", "car"]);
  assert.deepEqual(core.matchTerms("_car", g), []);
  assert.deepEqual(core.matchTerms("2car", g), []);
  assert.deepEqual(core.matchTerms("car²", g), []);
  assert.deepEqual(core.matchTerms("(c.a.r) car.", { dots: ["c.a.r"], car: ["car"] }), ["dots", "car"]);
});

test("health and method handling", () => {
  assert.equal(call("", {}, "GET").status, 200);
  assert.equal(call("", {}, "DELETE").status, 405);
});

test("netlify adapter decodes base64 bodies", async () => {
  const body = Buffer.from(JSON.stringify(request())).toString("base64");
  process.env.WORKER_SHARED_SECRET = SECRET;
  const res = await netlify.handler({ httpMethod: "POST", headers: { authorization: `Bearer ${SECRET}` }, body, isBase64Encoded: true });
  assert.equal(res.statusCode, 200);
  assert.equal(JSON.parse(res.body).result.items.length, 2);
});

test("vercel adapter uses Web Request/Response", async () => {
  process.env.WORKER_SHARED_SECRET = SECRET;
  const { POST } = await import("../api/worker.mjs");
  const res = await POST(new Request("https://worker.test/api/worker", {
    method: "POST", headers: { authorization: `Bearer ${SECRET}` }, body: JSON.stringify(request()),
  }));
  assert.equal(res.status, 200);
  assert.equal((await res.json()).candidate_id, "batch_1");
});
