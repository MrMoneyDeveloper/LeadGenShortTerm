# Phase 2 controlled test report — 2026-09-15

## Verdict

PHASE_2_STATUS=INCOMPLETE

The local implementation and automated regression pass. Live cloud orchestration,
Google delivery, laptop-off operation and credential replacement remain unverified.
Do not infer production readiness from local test results.

## Evidence and provenance

This workspace contains an extracted project under two LeadGenShortTerm-main folders.
The original enclosing Git repository has no commits. A later read-only GitHub check
verified main at cc672ffd0b56558b9f158a4e96f4e11f8c36ce3f. A separate shallow checkout
at LeadGenShortTerm-review now holds the changes on codex/phase2-runtime-validation.
All expected major components were found: Render API, campaign lifecycle, ranking,
source adapters, Groq client, delivery/cleanup, Apps Script, Cloudflare Worker,
configuration, tests and migrations 0001–0003. This continuation adds migrations 0004 and 0005.

The supplied assessment reports an earlier 74-test pass, live Groq calls, deployed
Render and approximately 58–82 records/second. Those are historical supplied claims,
not live evidence collected in this session. No new throughput benchmark was run.
The September 13 report is preserved as PHASE_2_TEST_REPORT_2026-09-13.md.

## Changes made in this continuation

- Added provider_rate_state (15 application tables total) using explicit migration
  0004_provider_rate_state. It persists rolling 60-second request/token reservations,
  daily reservations, provider response limits/remaining/reset values, last response,
  cooldowns, 429 counts and admission deferral counts.
- Admission reserves a conservative UTF-8 prompt estimate, framing allowance and
  full completion ceiling before HTTP. Failed/ambiguous attempts keep reservations.
  Actual returned tokens remain separately accounted in api_usage.
- HTTP 429 persists a cooldown instead of sleeping briefly and retrying immediately.
  Provider request headers represent daily requests; token headers represent the
  active minute window, per https://console.groq.com/docs/rate-limits.
- Quota deferrals persist candidate availability without consuming failure attempts.
  Oversized prompts use bounded failure/review instead of waiting indefinitely.
- Free-plan calls record zero configured Groq cost. Reporting labels historical
  list-price arithmetic reference_list_price and reports actual_pipeline_cost=0.
  This describes Groq only, not total infrastructure cost.
- Added a persisted semantic drain deadline (0005). Quota waits cannot indefinitely
  hold a campaign open: after a configurable 24-hour grace period, bounded cleanup
  retires unfinished ranked semantic candidates. Tests prove completion and preserve
  contact-validation work and finals awaiting ACK. Progress/restarts do not extend it.
- Added configuration defaults and documentation. Single-candidate inference remains;
  batching is conditional on live evidence of efficiency and structured-output quality.
- Copied the existing private env file to the expected .env filename. Both filenames,
  the virtual environment and temporary database directory are verified Git-ignored.
  Secret values were not printed. ENVIRONMENT is test.

## Executed validation

| Check | Result |
|---|---|
| Python pytest, including disposable PostgreSQL integration | 91 passed, 0 skipped, 0 failed; 33.93 seconds |
| Ruff | Passed |
| Apps Script offline harness | 14 passed |
| Cloudflare Worker Node tests | 18 passed |
| Alembic empty database upgrade through 0004 | Passed |
| Alembic 0004 downgrade to 0003, then upgrade to head | Passed |
| Alembic 0005 downgrade to 0004, then upgrade to head | Passed |
| Alembic schema drift check | No new upgrade operations detected |

PostgreSQL 16 ran on loopback port 55439 with a disposable leadgen_phase2 database.
No operational database was used. Tests create isolated schemas; explicit Alembic
commands separately verified migrations rather than relying on ORM create_all.

New tests cover serialized rolling windows, token reservation boundaries, provider
header headroom, cooldown across midnight, duration parsing, separate database
transactions, durable candidate quota waits, oversized prompts and Free telemetry.
Two existing dependency deprecation warnings concern Starlette/httpx and AnyIO.

## Live Render verification

Using only the configured .env credentials, the read-only preflight verified the
configured test workspace and uniquely matched the configured runtime origin to the
isolated test service. /health and authenticated /dashboard/summary returned HTTP 200.
The runtime reports environment=test, paused=true, every execution flag=false,
raw_pending=0, candidates=0, validated=2, delivered=0. Those two existing final rows
may support a tiny Google delivery test after Google identity/resource verification.
No lead contents were read or printed, and no delivery was claimed.

Initial connections failed with the bundled CA defaults. Windows' trusted certificate
store resolved API access. The first runtime request timed out during the wake attempt;
a later bounded preflight succeeded. This does not prove Cron or laptop-off autonomy.
The reusable read-only preflight script prints only sanitized verification/status data.

The prepared checkout now supports a real Git diff against cc672ffd, and git diff
--check passes. Code/configuration changes were copied by an explicit source-file
allowlist; private configuration, credentials and local tooling were excluded.

## Remaining live gates and blockers

1. Cloudflare API token is empty in the private configuration. Supply it privately
   in .env, then verify account/resource identity before deployment.
2. clasp 3.4.1 is installed locally and its target is pinned from .env. The upload
   list contains exactly the six expected Apps Script files. Google authorization completed as the intended Google account. Identity and
   the configured Apps Script project returned HTTP 200. The configured Sheet and
   Drive folder both returned HTTP 404 under that account. Restore access or correct
   the private destination IDs before upload or delivery. No Google mutation occurred.
3. Apply migrations through 0005 explicitly to the verified isolated Render target
   before deploying this client. No remote deployment or migration was performed.
4. Prove real Sheet write/readback, Drive copy verification, ACK and PostgreSQL cleanup.
   Exercise a tiny shard boundary and dashboard/backup repeated runs.
5. Prove bounded Cloudflare Cron → Render wake → backpressure → jobs/tick and a tiny
   autonomous integration campaign. Validate Groq headers and conservative throughput
   live before tuning estimates or considering small batches.
6. Prove progress across several cloud cycles with the laptop/local processes off.
7. Exercise the tested semantic finalization policy in a bounded deployed campaign;
   the database-backed local regression now proves the quota-expiry completion path.
8. Rotate exposed active credentials once after integration debugging, update private
   platform values, smoke-test replacements and rerun regression after further fixes.

No outbound email, production schedule or seven-day campaign was started. Phase 3 is
one separately approved small real cycle after Phase 2 passes; Phase 4 is the later
approximately 200,000-raw-record/seven-day campaign. Lead yield is not guaranteed.
