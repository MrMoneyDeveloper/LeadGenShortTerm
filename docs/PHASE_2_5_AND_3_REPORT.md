# Phase 2.5 hardening and Phase 3 canary report

Updated: 2026-11-08

## Verdict

```text
PHASE_2_5_STATUS=PARTIAL
PHASE_3_STATUS=PARTIAL
```

Code-side Phase 2.5 work is complete, deployed and regression-clean. Live proof is complete
for everything that does not depend on Google Apps Script execution, Vercel or Cloudflare.
Phase 3 proved real-source acquisition and deterministic processing on live data, and found
that the configured sources yield **no contactable leads** at canary scale. No outbound email,
MWEB sending or production campaign was enabled. `ENVIRONMENT=test` throughout.

## Infrastructure changes

| Item | Change |
|---|---|
| PostgreSQL | The Phase 2 free Render database had expired. A new free Render PostgreSQL 17 instance was created in the web service's region, migrated to `0005_semantic_drain_deadline` with a clean drift check. External access is restricted to one operator IP. **The free instance expires 2026-12-08; Phase 4 must finish before then or move to a paid/other database.** |
| Render | Deployed `b6b1951` then `fbe2f59`. Runtime uses the internal database URL. |
| Netlify | One function-only site hosting the stateless worker (contract v1). |
| Vercel | Not deployed: the configured API token can list projects but receives 403 for team details and project creation. |
| Cloudflare | Not deployed: `CLOUDFLARE_API_TOKEN` is empty. Per operator decision, Apps Script `processorTick` is the laptop-off scheduler for Phase 3. |
| Apps Script | Deployed code verified identical to the repository. Functions cannot be executed remotely (no API-executable deployment), so properties/triggers require the operator in the editor. |

## Phase 2.5 implementation

- **Two GroqCloud organizations.** Per-org persisted admission state (`groq_a`, `groq_b`), per-org
  `api_usage`, optional per-org limits, rotation, fallthrough on cooldown/cap/429/access/network
  failures, no second-org spend on content/schema errors, earliest-time deferral when no org is
  eligible. Dashboard: `semantic_orgs`, `semantic_deferred_queue`.
- **Stateless worker lane.** `services/stateless_worker` (`match_terms`, contract v1): bearer auth,
  strict schema, bounded payloads, no state, no input echo in errors. Render sends pre-normalized
  text and terms, validates responses by stable ID, shadow-verifies a configurable fraction
  locally, persists per-worker call/failure/circuit state, prefers Vercel and falls back to Netlify
  then local execution. Dashboard: `remote_workers`.
- **Scoring** refactored to pluggable pre-normalized term matching (same decisions).
- **Delivery adoption.** Guarded `adopt-destination` CLI starts row allocation after rows already
  in a Sheet, only while a database has never claimed a delivery.

## Regression

| Check | Result |
|---|---|
| Python (disposable local PostgreSQL) | 115 passed |
| Ruff | Passed |
| Stateless worker tests | 10 passed |
| Python/JavaScript matcher parity (80-row quality corpus + Unicode edge cases) | Exact |
| Cloudflare Worker tests | 21 passed |
| Apps Script harness | 15 passed |
| Alembic drift check (disposable and new Render DB) | No upgrade operations |
| `git diff --check` | Passed |

## Live Phase 2.5 evidence

| Gate | Result | Evidence |
|---|---|---|
| Dual Groq config / per-org persistence | PASS | Live calls served by org A then org B; tokens and requests recorded per org; org A provider headers persisted (1,000 RPD / 8,000 TPM limits with remaining values) |
| Cooldown fallthrough / defer | PASS | Org A cooling → org B served; both cooling → deferred to the earliest eligible time with no HTTP call |
| Netlify worker contract | PASS | Valid auth 200 with exact Python parity on 50 corpus rows; idempotent replay identical; bad/missing auth 401; malformed 400; oversize 413; unsupported op rejected; no input echo |
| Worker lane in deployed pipeline | PASS | 270 real records matched remotely with 100% shadow verification and zero mismatches |
| Netlify outage → Render fallback | PASS | Wrong secret: two 401s opened the circuit; later batches skipped Netlify; all records processed locally with no loss |
| Backpressure | PASS | 22 rows over a 20-row high-water mark → `drain`, collect refused (409); state survived a Render restart; hysteresis held `drain` at 16 and 10; `collect` resumed at 4 |
| Restart recovery | PASS | Multiple Render restarts during the run without lost or duplicated work |
| Vercel worker | BLOCKED | Token permissions |
| Cloudflare scheduling | DEFERRED | Token absent; Apps Script tick chosen |
| Apps Script delivery, readback, Drive, ACK, cleanup | PENDING | Requires operator-run property check and trigger installation |
| Laptop-off execution | PENDING | Requires the Apps Script processor trigger |

Groq usage for all live tests: 4 requests (2 per org). YouTube usage: 132 of 10,000 daily units.

## Phase 3 canary (real sources)

| Stage | Result |
|---|---|
| Bluesky acquisition | ~8,150 posts scanned in bounded 20-second windows; 5 keyword matches stored; cursor persisted |
| YouTube acquisition | 406 comments scanned via configured queries; 405 stored; 1 duplicate correctly deduped; cursor persisted |
| Normalization | All records normalized (remote worker lane plus local) |
| Deterministic outcome | All records rejected; rejected rows deleted (retention honoured) |
| Contacts / semantic / final / Google | Not reached: no candidate qualified |

### Quality review (150 YouTube comments retained temporarily, then deleted)

- 150/150 not short-term-insurance relevant (no product or relevance term).
- 136/150 older than the 30-day recency limit (median ~99 days).
- 0/150 contained an email address.
- Bluesky: 5 keyword hits in ~8,000 posts, none qualifying.

The rules behaved correctly. The finding is about **source fit**: the firehose and query-matched
video comments rarely contain South African consumer insurance intent and essentially never
contain a public email address. At this rate a 200,000-record Phase 4 would likely produce few or
no final leads. Source strategy should be revisited before Phase 4.

## Outstanding before PASS

1. Operator in the Apps Script editor: confirm Script properties match Render
   (`RENDER_BASE_URL`, both tokens, Sheet, Drive folder), set `DELIVERY_ENABLED`,
   `PROCESSOR_SCHEDULE_ENABLED`, `DASHBOARD_ENABLED`, `BACKUPS_ENABLED` to `true`, then run
   `installDeliveryTrigger`, `installProcessorTrigger` and `installReportingTriggers`.
2. A Vercel token that can create/deploy projects in the configured team.
3. A source strategy that yields contactable leads, then a real delivery to `VALIDATED_001`.
4. One-time credential rotation after integrations are stable.
