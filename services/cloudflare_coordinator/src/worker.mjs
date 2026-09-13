import { createHash, timingSafeEqual } from "node:crypto";

const REQUEST_TIMEOUT_MS = 10_000;
const MAX_REQUESTS = 3;
const MAX_RESPONSE_BYTES = 16_384;
const MODES = new Set(["collect", "drain", "storage_pressure", "paused", "campaign_complete"]);

function configured(env) {
  if (env.COORDINATOR_ENABLED !== "true") return { status: "disabled" };
  if (!["test", "production"].includes(env.ENVIRONMENT)) {
    return { status: "misconfigured", code: "environment_required" };
  }
  if (env.ENVIRONMENT === "production" && env.COORDINATOR_ALLOW_PRODUCTION !== "true") {
    return { status: "misconfigured", code: "production_disabled" };
  }
  if (env.SCHEDULER_OWNER !== "cloudflare") {
    return { status: "misconfigured", code: "scheduler_owner_required" };
  }
  try {
    const origin = new URL(env.RENDER_BASE_URL);
    // An exact configured HTTPS origin prevents credentials in URLs and redirects to other hosts.
    if (origin.protocol !== "https:" || origin.username || origin.password ||
        origin.pathname !== "/" || origin.search || origin.hash) throw new Error();
    for (const key of ["DASHBOARD_API_TOKEN", "PROCESSOR_TRIGGER_TOKEN", "COORDINATOR_TRIGGER_TOKEN"]) {
      if (typeof env[key] !== "string" || env[key].length < 32 || env[key].length > 4096 ||
          /[\r\n]/u.test(env[key])) throw new Error();
    }
    return { origin: origin.origin };
  } catch {
    return { status: "misconfigured", code: "invalid_configuration" };
  }
}

export function authorized(request, expected) {
  if (typeof expected !== "string" || expected.length < 32 || expected.length > 4096) return false;
  const header = request.headers.get("Authorization") || "";
  if (!header.startsWith("Bearer ") || header.length > 4103) return false;
  // Hash to equal lengths before the native constant-time comparison.
  const digest = (value) => createHash("sha256").update(value, "utf8").digest();
  return timingSafeEqual(digest(header.slice(7)), digest(expected));
}

async function boundedJson(response) {
  if (!response.body) throw new Error("invalid_response");
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      length += next.value.byteLength;
      if (length > MAX_RESPONSE_BYTES) throw new Error("response_too_large");
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function parseBackpressure(value) {
  if (!value || !MODES.has(value.mode) || !value.counts) throw new Error("invalid_response");
  const counts = {};
  for (const field of ["raw", "candidates", "exports"]) {
    if (!Number.isSafeInteger(value.counts[field]) || value.counts[field] < 0) {
      throw new Error("invalid_response");
    }
    counts[field] = value.counts[field];
  }
  return {
    mode: value.mode,
    counts,
    export_required: value.export_required === true || counts.exports > 0,
  };
}

/** One invocation: at most one wake, one queue read and one bounded Render tick. */
export async function runCoordinator(env, options = {}) {
  const config = configured(env);
  if (config.status) return { ...config, requests: 0 };
  const fetcher = options.fetch || globalThis.fetch;
  const timeoutMs = Math.min(options.requestTimeoutMs ?? REQUEST_TIMEOUT_MS, REQUEST_TIMEOUT_MS);
  const time = options.now ? options.now() : Date.now();
  let requests = 0;

  async function request(path, token, { method = "GET", json = false, key } = {}) {
    if (requests >= MAX_REQUESTS) throw new Error("request_budget");
    requests += 1;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const headers = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (key) headers["Idempotency-Key"] = key;
    try {
      const response = await fetcher(`${config.origin}${path}`, {
        method, headers, redirect: "manual", signal: controller.signal,
      });
      const status = response.status;
      if (!response.ok) {
        await response.body?.cancel();
        return { status, ok: false };
      }
      if (json) return { status, ok: true, data: await boundedJson(response) };
      await response.body?.cancel();
      return { status, ok: true };
    } finally {
      clearTimeout(timeout);
    }
  }

  let health;
  try {
    health = await request("/health");
  } catch {
    return { status: "wake_pending", code: "render_health_unavailable", requests };
  }
  if (!health.ok) {
    return { status: "wake_pending", code: "render_health_unavailable", http_status: health.status, requests };
  }

  let pressure;
  try {
    const response = await request("/dashboard/backpressure", env.DASHBOARD_API_TOKEN, { json: true });
    if (!response.ok) {
      return { status: "upstream_unavailable", code: "backpressure_failed", http_status: response.status, requests };
    }
    pressure = parseBackpressure(response.data);
  } catch {
    return { status: "upstream_unavailable", code: "backpressure_failed", requests };
  }
  // Pause is conservative: an operator can still request a standalone cleanup on Render.
  if (pressure.mode === "paused") return { status: "paused", ...pressure, requests };

  // Render independently rechecks pressure/flags/deadline under its durable executor lock.
  // In storage_pressure mode its tick requests cleanup only. Cloudflare never chooses sources.
  try {
    const result = await request("/jobs/tick", env.PROCESSOR_TRIGGER_TOKEN, {
      method: "POST",
      key: `cloudflare:${env.ENVIRONMENT}:${Math.floor(time / 300_000)}`,
    });
    if (result.status !== 202) {
      return { status: "upstream_unavailable", code: "tick_failed", http_status: result.status, ...pressure, requests };
    }
    return { status: "tick_requested", ...pressure, requests };
  } catch {
    // A timeout after POST may still have enqueued work; the next tick is deduplicated by Render.
    return { status: "upstream_unavailable", code: "tick_unconfirmed", ...pressure, requests };
  }
}

function response(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/health" && request.method === "GET") {
      return response({ status: "ok", enabled: env.COORDINATOR_ENABLED === "true" });
    }
    if (path !== "/tick") return response({ status: "not_found" }, 404);
    if (request.method !== "POST") return response({ status: "method_not_allowed" }, 405);
    if (!authorized(request, env.COORDINATOR_TRIGGER_TOKEN)) return response({ status: "unauthorized" }, 401);
    const result = await runCoordinator(env);
    const status = result.status === "tick_requested" ? 202 :
      ["upstream_unavailable", "wake_pending"].includes(result.status) ? 503 :
        result.status === "misconfigured" ? 409 : 200;
    return response(result, status);
  },

  async scheduled(controller, env, context) {
    context.waitUntil(runCoordinator(env, { now: () => controller.scheduledTime }).then((result) => {
      // Only an allowlisted local result is logged. No HTTP bodies, URLs, headers or exceptions.
      console.log(JSON.stringify({ event: "coordinator_tick", ...result }));
    }));
  },
};
