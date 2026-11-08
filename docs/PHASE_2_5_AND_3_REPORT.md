# Phase 2.5 hardening and Phase 3 canary report

Updated: 2026-11-08

## Verdict

```text
PHASE_2_5_STATUS=PASS
PHASE_3_STATUS=PASS_WITH_FINDING
VERCEL_STATUS=PASS
```

Every Phase 2.5 gate has now passed live, including the Vercel worker (validated separately on
2026-11-08; see "Vercel validation"). Infrastructure readiness is separate from real-lead
yield, which remains the open Phase 3 finding below. Phase 3 ran real-source
acquisition through deterministic processing on live data and proved every downstream stage
live, including Google delivery, ACK, cleanup, Google-outage recovery and autonomous cloud operation.

**Key finding:** the configured public sources yield real insurance intent but **no public
email addresses**, so no real final lead was produced. The delivery leg was therefore proven
with clearly labelled synthetic records on the reserved `.invalid` TLD. Treat this as a
source-strategy problem to solve before Phase 4, not a pipeline defect.

No outbound email, MWEB sending or production campaign was enabled. `ENVIRONMENT=test`.

## Infrastructure

| Item | State |
|---|---|
| PostgreSQL | Phase 2 free database had expired. New free Render PostgreSQL 17 (`RENDER_DATABASE_NAME`, same region as the service) at migration `0006_sample_store`, drift-clean. External access limited to one operator IP. **Expires 2026-12-08: Phase 4 must finish before then or move databases.** `RENDER_DATABASE_NAME1` refers to the expired instance. |
| Render | Live; internal database URL; `ENVIRONMENT=test`. The router hardening from the Vercel validation is deployed after it merges to `main`. |
| Cloudflare | Coordinator deployed via API with `.env` credentials; 5-minute Cron; `COORDINATOR_EXPIRES_AT=2026-11-11T12:00:00Z`. Owns acquisition/processing ticks. |
| Apps Script | Owns delivery (5 min), dashboard (15 min) and backup (hourly) triggers. Script Properties verified equal to Render by hash. Expiring operator endpoint (`Ops.gs`) deployed for headless control. Processor tick disabled (one scheduler owner). |
| Netlify | Stateless worker (contract v1); daily call cap 60 during the canary. |
| Vercel | Isolated project `leadgen-stateless-worker` in the token owner's default team (Hobby plan, role OWNER); project ID pinned in the ignored `.env`; root `services/stateless_worker`; one production deployment; default Vercel Authentication left on. Render has the matching secret configured but `VERCEL_ENABLED=false` (restored gate). |
| Tokens | Render, Apps Script and Cloudflare share the Render token pair (`*_TOKEN1` in `.env`); the unsuffixed `.env` token values are stale. |

## Changes since Phase 2

- Two GroqCloud Free-tier organizations with independent persisted quota state, sequential (A then B) use,
  fallthrough and earliest-time deferral; per-org telemetry.
- Stateless `match_terms` worker contract (Netlify/Vercel), shadow verification, durable circuit
  breaker, local fallback.
- Vocabulary `rules-v3`: short-term **and long-term** personal insurance (life, funeral,
  disability/income protection), SA long-term insurers, more SA places and slang, Afrikaans.
  Medical aid, health/gap cover and investment/retirement products remain non-target.
  `target_products` moved to `config/scoring.yaml`.
- Targeted retrieval: Bluesky `searchPosts` over 38 configured phrases (recency-bounded,
  incremental passes, bulk public-bio fetch with profile-URL provenance); YouTube recent
  ZA-region search storing only recent keyword-matching comments.
- Bounded sample store (`sample_records`, 20 per platform, operator-authorized) with JSON/CSV
  export, explicit delete endpoint and a `SAMPLES` Sheet tab.
- Guarded `adopt-destination` CLI to continue row allocation after existing Sheet rows.

## Regression (final)

| Check | Result |
|---|---|
| Python (disposable local PostgreSQL) | 143 passed (after the Vercel-validation hardening) |
| Ruff / `git diff --check` | Passed |
| Alembic `0006` upgrade → downgrade → upgrade, drift check | Passed (re-checked on a fresh isolated database) |
| Apps Script harness / bounded delivery / ops endpoint | 15 / 4 / 7 passed |
| Cloudflare Worker tests | 21 passed |
| Stateless worker tests + Python/JS parity | 10 passed, exact parity |
| Quality fixture (86 labelled rows) | Precision 1.0, false-positive rate 0.0, precision@10 1.0 |

## Phase 2.5 live gates

| Gate | Result | Evidence |
|---|---|---|
| Dual Groq config / per-org persistence | PASS | Live calls served by org A and org B; per-org requests/tokens; provider headers persisted |
| Cooldown / defer | PASS | A cooling → B served; both cooling → deferred to earliest eligible time with no HTTP call |
| Semantic outage does not stop deterministic work | PASS | Canary normalized ~1,400 records with no semantic dependency |
| Netlify worker contract | PASS | Auth, malformed, oversize, idempotent replay, exact parity on live corpus |
| Netlify outage → Render fallback | PASS | Two 401s opened the circuit; records processed locally with no loss |
| Vercel worker | PASS | See "Vercel validation" |
| Cloudflare scheduling | PASS | Health 200, unauthenticated tick 401, authenticated tick 202 → Render tick; Cron installed |
| Apps Script live delivery, Sheet readback, Drive verification, ACK | PASS | Batch ACKNOWLEDGED at row position 3 (row 5 of `VALIDATED_001`), Drive file verified, payload removed, final queue empty |
| Replay idempotency | PASS | Immediate second delivery returned IDLE; no new row |
| Dashboard ×2, backup ×2 | PASS | Both succeeded twice; one backup history entry for the day |
| Backpressure | PASS | Drain at high water, state survived restart, hysteresis, resume at low water |
| Restart recovery | PASS | Repeated Render restarts without lost or duplicated work |
| Autonomous cloud operation | PASS | 13:52–14:04 UTC with no operator calls: the Apps Script trigger delivered a queued final on its own (ACK at 13:54:51, next row), and Cloudflare Cron invocations at 14:00 and 14:05 each made the 3 bounded subrequests with 0 errors and created Render work. The laptop stayed powered but uninvolved; a literal power-off test was not repeated (Phase 2 recorded one). |

## Vercel validation (2026-11-08)

Code under test: commit `9582265` (branch `codex/vercel-validation`). Live Render router tests ran on the deployed `7bc064d`, whose routing is identical for valid worker responses; the hardening changes only malformed-response handling and is covered offline.

Scope: Vercel worker only. No acquisition, Groq, Google or real-source calls were made for these
tests; no schedules were added or extended.

**Account preflight** (only `VERCEL_API_TOKEN` + `VERCEL_USER_ID`, re-verified before every
mutation): token valid and user-scoped with an expiry; token identity equals `VERCEL_USER_ID`;
intended scope is the owner's default team (role OWNER, plan Hobby). The earlier 403 came from
the previous token; the replacement token can create and deploy projects. Note: Vercel's Hobby
plan is intended for personal, non-commercial use, which is a terms question for the operator.

**Deployment:** project created in that team with `rootDirectory=services/stateless_worker`
and no framework; the only project variable is `WORKER_SHARED_SECRET` (sensitive, production),
equal to Render's `VERCEL_SHARED_SECRET`. One deployment, READY in 10 s; `/api/worker` packaged
and ran. No redeploy was needed.

**Router hardening (concrete defects, now with regression tests):**

- A malformed item ID that was an array or object raised `TypeError` (unhashable) inside
  response validation. It escaped `match_batch`, which would roll back normalization and skip
  the local fallback. IDs are now type-checked first.
- `contract: true` or `1.0` was accepted as version 1; envelope fields now require exact types.
- A deeply nested JSON body raised `RecursionError`; it is now `worker_invalid_json`.
- Any other unexpected shape now becomes `worker_schema` (fallback), never an exception.

**Live tests** (10 synthetic, non-personal records; 10 worker HTTP requests of a 15 budget):

| Test | Result |
|---|---|
| Health (cold) | 200 JSON, 830 ms |
| Authorized processing, 10 records | 200, strict contract valid, stable IDs, **exact Python parity 10/10** (8 with matches), 1,441 ms |
| Deterministic replay | Byte-identical body, 422 ms warm |
| Missing / wrong authentication | 401 / 401, no input echoed |
| Malformed JSON | 400 `invalid_json` |
| Oversized text | 413 `bounds`, no input echoed |
| Timeout | Classified `worker_timeout` by the production router code |
| Malformed worker output | Offline regression tests (array/object/int/null/bool IDs, wrong envelope types, nested/non-JSON bodies, redirects/protection pages): all reach fallback |
| Render router used Vercel | 4 records normalized on Render with `remote_vercel_matched=4`, one successful call, 0 shadow-verification mismatches |
| Vercel unreachable | Normalization completed locally; `worker_network` recorded; nothing credited to Vercel |
| Vercel rejecting (wrong secret) | Normalization completed locally; `worker_http_401` recorded; durable failure count persisted |

Temporary gates were restored afterwards (Render `VERCEL_ENABLED=false`, `NETLIFY_ENABLED=true`,
Bluesky source row re-enabled) and the synthetic records, candidates, hashes and samples were removed.

**Conflicts flagged, not resolved:**

- `AGENTS.md` still says the project is in Phase 2 and forbids starting Phase 3, and
  `RUNTIME_AGENT_CONTRACT.md` uses "until Phase 2 is complete" wording, while this report
  records operator-directed Phase 3 work.
- Product scope: `AGENTS.md` and config now cover short- **and** long-term personal insurance,
  but `README.md` and `docs/ARCHITECTURE.md` still describe a short-term-only engine.
- Resolved by the operator (2026-11-08): Groq organizations A and B have independent usage and
  are to be used one after the other. The router changed from per-call rotation to sequential
  use (A first; B only when A cannot take the call; back to A once eligible). Each organization
  keeps its own persisted quota state and caps.

## Phase 3 canary (real sources)

| Stage | Bluesky (targeted search) | YouTube (recent ZA videos) |
|---|---|---|
| Scanned | 736 posts (one full pass over 38 phrases) | 1,351 comments |
| Stored after keyword filter | 458 (32 duplicates removed) | 455 across all YouTube runs (2 duplicates) |
| Passed intent rules | 59 candidates (quote 39, recommendation 19) | 0 |
| Public contact found | 1 (post text); 48 bios fetched, 0 emails in bios | 0 |
| Final leads | 0 (the one contact ranked below the defer threshold) | 0 |

Also earlier the same day: Bluesky firehose sampling (8,149 posts → 5 matches, all rejected).

Failure tests (plan §10):

| Test | Result |
|---|---|
| Vercel unavailable | PASS (live: unreachable host and rejected secret both fell back to local processing) |
| Netlify unavailable | PASS (live circuit-breaker test) |
| One Groq organization unavailable | PASS |
| All semantic capacity unavailable | PASS |
| Google temporarily unavailable | PASS: a batch was claimed and never written/ACKed (simulated delivery failure). The final lead and batch payload stayed intact, a re-claim returned the same batch, backpressure reported `export_required`, and the Apps Script trigger completed that same batch on its own about 3.5 minutes later; payload removed only after ACK; no duplicate Sheet row. |

### Quality review

- Bluesky search finds real consumer intent, but the audience is mostly US/UK: only 2 of 59
  candidates carried a South African signal.
- Bluesky users do not publish email addresses in posts or bios (0 of 48 bios).
- YouTube comments on recent SA insurance videos are rarely about buying insurance and never
  contain emails.
- The rules behaved correctly; precision on the labelled fixture remains 1.0.

**Implication for Phase 4:** with these sources the pipeline will produce few or no emailable
leads. A consent-pool model needs either sources where people publish an email, or a first
contact step on the platform itself (which is outbound messaging and outside the current scope).

### Sample store

20 Bluesky samples (genuine intent candidates) and 20 YouTube samples (insurance-relevant
rejects) are retained with provenance in `sample_records` and the `SAMPLES` Sheet tab.

## Outstanding

1. Decide whether to enable the Vercel lane on Render (`VERCEL_ENABLED=true`); it is validated but left off.
2. Source strategy that yields contactable South African leads before Phase 4.
3. Delete the three synthetic `SYNTHETIC_TEST` rows (`VALIDATED_001` rows 5–7) if not wanted.
4. Delete the Apps Script ops web deployment when headless control is no longer needed
   (it self-expires 2026-11-15); the Cloudflare Cron self-expires 2026-11-11.
5. One-time credential rotation, then align `.env` unsuffixed token names.
6. Replace the free database before 2026-12-08.
