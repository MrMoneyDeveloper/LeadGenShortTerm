// Vercel entry point (Node.js runtime, Web Request/Response handlers).
import core from "../src/core.cjs";

async function respond(request) {
  const body = request.method === "POST" ? await request.text() : "";
  const result = core.handle(
    { method: request.method, headers: Object.fromEntries(request.headers), body },
    { secret: process.env.WORKER_SHARED_SECRET },
  );
  return new Response(result.body, { status: result.status, headers: result.headers });
}

export const GET = respond;
export const POST = respond;
