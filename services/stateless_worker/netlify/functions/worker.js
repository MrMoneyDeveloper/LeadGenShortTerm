"use strict";
// Netlify Functions entry point (classic handler). Deployed as a zip with core.cjs beside it;
// in the repository the shared core lives in ../../src.
let core;
try {
  core = require("./core.cjs");
} catch {
  core = require("../../src/core.cjs");
}

exports.handler = async (event) => {
  const body = event.body ? (event.isBase64Encoded ? Buffer.from(event.body, "base64").toString("utf8") : event.body) : "";
  const result = core.handle(
    { method: event.httpMethod, headers: event.headers || {}, body },
    { secret: process.env.WORKER_SHARED_SECRET },
  );
  return { statusCode: result.status, headers: result.headers, body: result.body };
};
