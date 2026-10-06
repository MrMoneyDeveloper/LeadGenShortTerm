# Phase 2 controlled test report — updated 2026-10-06

## Verdict

PHASE_2_STATUS=INCOMPLETE

## October 6 targeted scheduler change

Following the user's request to minimize usage, changed schedule_tick to skip database
queue/permission work for processing and acquisition stages disabled by environment
flags. Cleanup and campaign/deadline advancement remain active. No dependency was added.
Ten targeted scheduler, backpressure and campaign tests passed; changed Python files
passed Ruff. Unrelated component suites were not repeated. This is a latency reduction,
not by itself proof that the deployed Worker timeout is resolved.

Published the Phase-2 branch and verified its repository matches the configured Render
test service. Verified the private database binding before applying additive migrations
0004/0005 from revision 0003. Render reports exact commit 1d989a4e live. Persistent Groq
quota state and semantic drain deadlines are therefore deployed; no Groq requests were
needed for this deployment check. All source/model/processing/delivery flags stayed off.
One direct bounded tick on the paused empty runtime returned HTTP 202 in 4.125 seconds,
within the Worker's ten-second request timeout.

### October 6 scheduled confirmation and restored state

Real Cron first respected pause, then returned tick_requested with requests=3 at
05:34:12 UTC after resuming the empty runtime. This confirms the HTTP-202 response
through the deployed Cloudflare-to-Render path. The first attempt lost its log
connection; one final bounded attempt captured success. A local DNS interruption
then prevented initial cleanup, so cleanup was explicitly retried and verified:
Render paused, all execution flags off, zero raw/candidate/final queues, two delivered,
Cron empty, coordinator disabled, public route disabled, and temporary tail removed.
The ten-minute Worker expiry remained the fallback during that interruption.

The direct smoke tick's cleanup completed in one attempt at 05:25:42 UTC. Subsequent
scheduled ticks did not create another cleanup job; total completed jobs remained five.
No Groq, YouTube or Bluesky requests were used for these October 6 checks.

### October 6 isolated Google rollover component proof

Ran a temporary verifier against a separate synthetic test Sheet in the configured
Drive folder. At 05:37:39 UTC it reported three rows over two VALIDATED shards, exact
readback, replay without duplicate rows, and a verified Drive JSON checksum. Repeating
the file write reused the same JSON file; a CSV copy was also written. Production
delivery helper functions performed these checks, including plain-text preservation.
The main Sheet property and existing delivery allocation were not changed. The
temporary verifier was removed after the run; synthetic result files were retained.

Scope: this proves Google-side shard creation/write/readback/Drive/replay with synthetic
placements. It did not claim or acknowledge a new PostgreSQL batch. Database allocation
has prior automated coverage; the earlier two-lead live ACK/deletion proof still stands.
Do not present this component check as a new complete autonomous pipeline run.

Remaining Phase-2 gates: one bounded autonomous synthetic pipeline/delivery run,
independent Apps Script scheduling and laptop-off proof, provider-credential replacement
and replacement smoke checks. For usage efficiency, combine autonomous pipeline and
laptop-off validation in one bounded test window when the user can disconnect the laptop.
Phase 3 is not authorized by this report. No full regression was repeated on October 6.

## Current evidence — October 5

The two-lead live Google delivery gate now passes. The approved diagnostic found
that only rank_score differed: Google coerced numeric-looking text despite the
rich-text write. Delivery now sets number format to plain text before writing,
preserving decimal precision and leading zeroes. Exact readback and formula guards
remain mandatory; comparison was not weakened to numeric equality.

The corrected Apps Script code was uploaded and read back for verification, and
the temporary diagnostic was removed. After clearing only the user-approved,
backed-up disposable range VALIDATED_001!A2:Q3, the immutable batch succeeded:

- Sheet write and exact readback passed.
- Drive JSON/CSV copies were written and JSON checksum readback passed.
- Render receipt became ACKNOWLEDGED at 2026-10-05 07:23:58 UTC.
- Runtime counters changed from two queued final leads and zero delivered to
  zero queued final leads and two delivered. Both transient lead payloads were
  deleted under the authorized ACK cleanup. Final copies remain in Google.
- A repeat delivery returned IDLE with no final leads awaiting delivery.
- Backup of the completed Sheet succeeded at 07:26:31 UTC with one part.
- Repeating that backup succeeded at 14:28:47 UTC with the same one-part snapshot.
- Final Render verification returned HTTP 200: paused=true, all execution flags
  false, zero raw/candidate/final queued records, and two delivered. The local
  disposable regression database was stopped after testing.

Regression on October 5: 91 Python tests passed (40.58 seconds, two existing
dependency deprecation warnings); 15 Apps Script mock tests passed, including
numeric-text coercion; 21 Worker tests passed after the bounded-expiration change; Ruff passed; Alembic reported no
schema drift against the isolated local test database; Git whitespace check passed.
The deployed Render code remains its earlier commit; local Groq quota changes and
migrations 0004/0005 still need their separately verified deployment.

### October 5 live Cloudflare Cron evidence

Deployed the updated Worker to the configured test account with an optional
COORDINATOR_EXPIRES_AT guard and a 20-minute expiry. The guard uses actual execution
time, fails closed for invalid expiry, and checks the deadline before every request.
Three added unit tests cover delayed Cron events and expiry between requests.

Installed a temporary every-minute Cron with its public HTTP route disabled. Live
scheduled-event logs proved pause handling first: two upstream requests, paused mode,
and zero raw/candidate/export queues. Briefly resumed the empty Render test runtime;
all acquisition, processing, source, model, delivery and local scheduler flags stayed off.
Three subsequent scheduled events reached the third request but returned
tick_unconfirmed after the Worker's bounded timeout. No manual job tick was sent.

Render's durable job history independently proves execution: one new cleanup job was
created at 14:52:05 UTC and completed at 14:52:21 UTC in one attempt. The completed-job
count rose from three to four and stayed at four after the next scheduled request.
Thus Cron-to-executor execution and observed deduplication pass, but a confirmed
HTTP-202 tick response within the ten-second request budget remains unresolved.
Do not widen the timeout or claim full orchestration success without investigating
the deployed synchronous schedule_tick path and verifying the response latency.

Ended the bounded test and explicitly verified cleanup: Render paused=true, zero
raw/candidate/final queues, two delivered, no Cron schedules, coordinator disabled,
and workers.dev/preview routes disabled. The temporary live tail was removed.
Sanitized execution/job evidence is saved in the ignored local test-runtime folder.

Remaining live gates: isolated shard rollover, confirmed Cloudflare tick response,
bounded autonomous source-to-output campaign, laptop-off proof, and final credential
replacement. The used delivery state locks its destination/shard size, so the rollover
test requires isolated state; do not reset the successful delivery's allocation.
Cloudflare schedules and routes are disabled after the October 5 test. No production acquisition or
outbound email has been enabled.

The dated sections below retain the earlier investigation and are historical where
they describe missing credentials, pending Google consent, or the resolved row conflict.

## September 30 live delivery and Worker test

- Temporarily enabled final delivery on the verified Render test service with a
  two-record export batch. Acquisition, processing and scheduling stayed off.
- The real Apps Script delivery ran and stopped with DELIVERY_ROW_CONFLICT.
  One immutable batch remains CLAIMED with count=2 and no acknowledgment.
  Both final leads remain in PostgreSQL. Existing Sheet rows were protected.
- Google metadata verification succeeds, but direct Sheets read and Drive workbook
  export using the cached clasp authorization returned HTTP 403. The signed-in
  browser can open the current configured Sheet. Conflict contents remain unverified.
- Live dailyBackup succeeded twice at 18:12:49 and 18:13:29 UTC, with one part.
  This backs up the existing Sheet; it does not prove the pending batch was delivered.
- Deployed leadgen-coordinator-test directly to the verified configured account,
  using the checked-in Worker and private secret bindings. No Cron was installed.
- Worker health returned 200 after initial route propagation; unauthenticated tick
  returned 401; authenticated tick returned 200 with status=paused, requests=2,
  raw=0, candidates=0, exports=2 and export_required=true. This proves cloud-to-cloud
  health/backpressure access and pause handling, not jobs/tick or autonomous progress.
- Disabled the temporary Worker HTTP route after verification. The Worker remains
  deployed without Cron. Requested restoration of Render's original disabled delivery
  gate and export batch size; final deployment verification is recorded separately.
- Further delivery needs resolution of occupied destination rows without data loss.
- The user confirmed existing destination rows are disposable. Cleared only the
  batch's reserved VALIDATED_001!A2:Q3 range after the successful Drive backup.
  Retrying the immutable batch again returned DELIVERY_ROW_CONFLICT. This means
  the conflict cannot yet be attributed solely to old data; write/readback behavior
  needs diagnosis. Neither attempt acknowledged the batch or deleted its leads.
- A temporary diagnostic is prepared privately to log field names, lengths and
  formula flags without lead values. Automatic approval review rejected its remote
  upload pending specific user authorization; no diagnostic code was uploaded.
- Dashboard refresh also succeeded at 18:14:50 UTC. Render's first restoration was
  verified live with all flags off. A second delivery-disable restoration was
  requested after the repeat failure.

## Latest access verification — September 30

The user supplied the Cloudflare token privately. Direct token verification returned
HTTP 200 with active status, and the configured account's Workers scripts lookup
returned HTTP 200. This proves read access to the target; deployment remains untested.
The existing Google OAuth authorization refreshed successfully. Identity, configured
Apps Script project, Sheet, Drive folder and script content checks all returned 200.
The editor log shows refreshDashboard completed successfully at 17:41 UTC on
September 30. The earlier missing-token and Google-consent blockers below are historical.
Render health and authenticated summary returned 200 after one wake timeout:
paused=true, every execution flag=false, validated=2, delivered=0.
Live delivery, ACK/cleanup and Cloudflare orchestration still require execution.

The local implementation, automated regression and two-lead Google delivery pass.
Autonomous cloud orchestration, laptop-off operation and credential replacement
remain unverified.
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
2. clasp 3.4.1 is installed locally and its target is pinned from .env. The six
   expected Apps Script files matched the remote project. The earlier Sheet/folder
   access errors were resolved, and nine Script Properties were saved and verified.
   On September 18, manual refreshDashboard stopped at Google's Authorization
   required prompt. No successful dashboard or delivery execution was verified.
   Google script execution consent remains distinct from clasp sign-in.
3. Apply migrations through 0005 explicitly to the verified isolated Render target
   before deploying this client. The new client and migrations remain local.
   On September 18, user-authorized Render destination corrections were applied
   from .env, with EXPORT_BATCH_SIZE=2 and final delivery disabled. The existing
   remote commit 97e95692 was redeployed and verified live; this did not deploy
   the local Groq changes. Runtime was paused, all gates off, two finals queued.
   On September 30, destination settings still matched and final delivery remained
   disabled; the initial runtime summary request timed out. A bounded follow-up
   returned HTTP 200 for health and summary, with all gates off and the same two
   queued finals, zero delivered. A fresh dashboard attempt again showed Google's
   Authorization required dialog; no script consent was granted.
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

## September 30 continuation checks

- Corrected the reusable Render preflight to require the configured workspace ID
  and check the display name only when explicitly configured.
- Ruff passed for that script; Git diff whitespace checks passed.
- Apps Script offline harness: 14 passed. Worker tests: 18 passed.
- The 91-test Python/database regression above remains the September 15 result;
  it was not rerun during this configuration/documentation continuation.
- Cloudflare token remains absent. Google execution consent remains pending.
- No source acquisition, delivery ACK, payload deletion, or new schedules occurred.
