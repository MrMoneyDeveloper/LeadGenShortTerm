"use strict";
// Stateless LeadGen worker core (contract v1). Pure function of the request: no storage,
// no outbound calls, no secrets in responses. Shared by the Vercel and Netlify entry points.
const crypto = require("node:crypto");

const CONTRACT = 1;
const LIMITS = Object.freeze({
  maxBodyBytes: 512 * 1024,
  maxItems: 200,
  maxTextChars: 8000,
  maxGroups: 200,
  maxTermsPerGroup: 500,
  maxTermChars: 200,
  maxIdChars: 64,
});
const ID = /^[A-Za-z0-9_-]{1,64}$/;
// Python's Unicode \w is letters, numbers and underscore; mirror it exactly for parity.
const WORD = "[\\p{L}\\p{N}_]";

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function hit(text, term) {
  return new RegExp(`(?<!${WORD})${escapeRegExp(term)}(?!${WORD})`, "u").test(text);
}

function matchTerms(text, groups) {
  const matched = [];
  for (const [name, terms] of Object.entries(groups)) {
    if (terms.some((term) => hit(text, term))) matched.push(name);
  }
  return matched;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value, keys) {
  const actual = Object.keys(value).sort();
  const wanted = [...keys].sort();
  return actual.length === wanted.length && actual.every((k, i) => k === wanted[i]);
}

function validateRequest(req) {
  if (!isPlainObject(req) || !exactKeys(req, ["contract", "job_id", "candidate_id", "operation", "payload"])) {
    return "schema";
  }
  if (req.contract !== CONTRACT) return "contract_version";
  if (typeof req.job_id !== "string" || req.job_id.length > LIMITS.maxIdChars) return "schema";
  if (typeof req.candidate_id !== "string" || !ID.test(req.candidate_id)) return "schema";
  if (req.operation !== "match_terms") return "unsupported_operation";
  const p = req.payload;
  if (!isPlainObject(p) || !exactKeys(p, ["groups", "items"])) return "schema";
  if (!isPlainObject(p.groups) || !Array.isArray(p.items)) return "schema";
  const groupNames = Object.keys(p.groups);
  if (groupNames.length === 0 || groupNames.length > LIMITS.maxGroups) return "bounds";
  for (const name of groupNames) {
    const terms = p.groups[name];
    if (name.length > LIMITS.maxIdChars || !Array.isArray(terms) || terms.length > LIMITS.maxTermsPerGroup) {
      return "bounds";
    }
    if (!terms.every((t) => typeof t === "string" && t.length > 0 && t.length <= LIMITS.maxTermChars)) {
      return "bounds";
    }
  }
  if (p.items.length === 0 || p.items.length > LIMITS.maxItems) return "bounds";
  const seen = new Set();
  for (const item of p.items) {
    if (!isPlainObject(item) || !exactKeys(item, ["id", "text"])) return "schema";
    if (typeof item.id !== "string" || !ID.test(item.id) || seen.has(item.id)) return "schema";
    if (typeof item.text !== "string" || item.text.length > LIMITS.maxTextChars) return "bounds";
    seen.add(item.id);
  }
  return null;
}

function authorized(header, secret) {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
  const a = crypto.createHash("sha256").update(header.slice(7)).digest();
  const b = crypto.createHash("sha256").update(secret).digest();
  return crypto.timingSafeEqual(a, b);
}

function reply(status, body) {
  return { status, headers: { "content-type": "application/json", "cache-control": "no-store" }, body: JSON.stringify(body) };
}

function error(status, code) {
  return reply(status, { contract: CONTRACT, status: "error", code });
}

/**
 * @param {{method: string, headers: Record<string, string|undefined>, body: string}} request
 * @param {{secret?: string}} env
 */
function handle(request, env) {
  const method = (request.method || "").toUpperCase();
  if (method === "GET") return reply(200, { contract: CONTRACT, status: "ok", operations: ["match_terms"] });
  if (method !== "POST") return error(405, "method_not_allowed");
  const secret = env.secret || "";
  if (secret.length < 32) return error(503, "worker_unconfigured");
  const headers = Object.fromEntries(Object.entries(request.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
  if (!authorized(headers.authorization, secret)) return error(401, "unauthorized");
  const body = typeof request.body === "string" ? request.body : "";
  if (Buffer.byteLength(body, "utf8") > LIMITS.maxBodyBytes) return error(413, "too_large");
  let req;
  try {
    req = JSON.parse(body);
  } catch {
    return error(400, "invalid_json");
  }
  const problem = validateRequest(req);
  if (problem) return error(problem === "bounds" ? 413 : 400, problem);
  const items = req.payload.items.map((item) => ({ id: item.id, matched: matchTerms(item.text, req.payload.groups) }));
  return reply(200, { contract: CONTRACT, job_id: req.job_id, candidate_id: req.candidate_id, status: "ok", result: { items } });
}

module.exports = { handle, matchTerms, validateRequest, LIMITS, CONTRACT };
