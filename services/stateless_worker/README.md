# Stateless worker (Vercel / Netlify)

Optional compute lane for the deterministic term-matching step. Contract v1, one operation:

```json
{"contract": 1, "job_id": "...", "candidate_id": "<batch key>", "operation": "match_terms",
 "payload": {"groups": {"product:MOTOR": ["car insurance"]}, "items": [{"id": "r1", "text": "..."}]}}
```

Response: `{"contract": 1, "job_id", "candidate_id", "status": "ok", "result": {"items": [{"id", "matched": [...]}]}}`.

- Bearer auth with `WORKER_SHARED_SECRET` (min 32 chars), constant-time comparison.
- Strict schema (exact keys), bounded body/items/text/groups, no state, no outbound calls.
- Errors are `{"contract": 1, "status": "error", "code": "..."}` and never echo input.
- Word boundaries mirror Python's Unicode `\w`; `services/render_api/tests/test_remote_workers.py`
  checks exact Python/JavaScript parity on the quality corpus.

Entry points: `api/worker.mjs` (Vercel, `/api/worker`) and `netlify/functions/worker.js`
(Netlify, `/.netlify/functions/worker`, deployed as a zip with `src/core.cjs` beside it).

```bash
npm test
```
