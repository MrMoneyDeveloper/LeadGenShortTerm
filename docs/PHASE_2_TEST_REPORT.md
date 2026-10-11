# Phase 2 controlled runtime validation report

Updated: 2026-10-11

## Verdict

```text
PHASE_2_STATUS=PASS
PHASE_3_STATUS=NOT_STARTED
```

The architecture, automated checks and bounded live cloud integration gates required
for Phase 2 have passed. The runtime has been returned to its safe stopped state:
Render is live and paused, all execution flags are disabled, transient queues are
empty, Cloudflare scheduling and its public route are disabled, and temporary Google
test helpers have been removed.

This verdict does not start Phase 3, authorize outbound email, or claim readiness for
the later seven-day campaign. Before Phase 3, review the credentials whose rotation
the user deferred and rotate only those that still require replacement.

## What the system does

The project finds and qualifies potential South African short-term-insurance leads.
Its production path is:

```text
bounded public-source collection
  -> temporary PostgreSQL queues and checkpoints
  -> deterministic filtering, contact checks and ranking
  -> selective GroqCloud classification for high-ranked ambiguous cases
  -> final-lead queue
  -> Google Apps Script delivery
  -> sharded VALIDATED_### Sheet tabs plus verified Drive receipt
  -> delivery acknowledgement
  -> deletion of temporary PostgreSQL payloads
```

Cloudflare provides lightweight scheduling and wake-up calls. Render performs the
processing. Apps Script performs final delivery, dashboard and backup work. PostgreSQL
is a restartable conveyor belt rather than a permanent raw-data archive.

## Final gate summary

| Gate | Result | Evidence |
|---|---|---|
| Core automated regression | PASS | 91 Python tests, Ruff, 15 Apps Script harness checks, 21 Worker tests, migrations through 0005 and a clean Alembic drift check |
| Real Google delivery | PASS | Exact Sheet write/readback, verified Drive copies, ACK and temporary PostgreSQL cleanup |
| Google shard rollover | PASS | One database-backed batch placed three synthetic final rows across two `VALIDATED_###` shards with a two-row shard limit |
| Delivery replay | PASS | The immediate second delivery returned IDLE and created no duplicate rows |
| Cloudflare orchestration | PASS | Scheduled Worker event woke Render and obtained the bounded HTTP 202 tick response |
| Laptop-off autonomy | PASS | Cloudflare and independent Apps Script cycles advanced during the user-confirmed 17:07–17:25 Africa/Johannesburg shutdown interval on 2026-10-06 |
| Bounded synthetic cloud flow | PASS | Three synthetic raw records produced one final, one identity duplicate and one irrelevant rejection; delivery and cleanup completed unattended |
| Groq Free-tier control | PASS | Exactly one deployed inference; persistent provider headers, reservation, cap deferral and restart-surviving cooldown state verified |
| Semantic drain deadline | PASS | An unfinished semantic candidate expired after the bounded deadline while completed final state was preserved |
| Restoration | PASS | Original database and Sheet bindings restored; Render live and paused; every execution flag off; queues empty |

The full automated regression was last run after the implementation changes. The
October 11 completion changed only documentation and exercised the already-deployed
bounded integration fixtures, so unaffected suites were not repeated. A focused
offline check covers the added Apps Script delivery-expiry guard.

## GroqCloud Free-tier evidence

The deployed client made exactly one semantic request in an isolated Phase 2 schema:

- 420 input tokens and 183 output tokens
- actual pipeline cost recorded as zero for the Free plan
- provider request limit 1,000 with 999 remaining
- provider active token-window limit 8,000 with 7,230 remaining
- provider reset timestamps persisted
- conservative reservation of 3,144 tokens persisted before the request

A local one-request cap deferred the second candidate to a later UTC day with zero
failed attempts. A persisted cooldown was then injected into isolated test state and
survived a Render process replacement; the candidate remained deferred and provider
usage stayed at one request. No real HTTP 429 or quota exhaustion was forced.

The semantic-deadline cleanup then expired one unfinished candidate after the
campaign entered draining state. It preserved the completed candidate/final and did
not make another provider call. This proves bounded finalization when model capacity
is unavailable without spending more quota solely to manufacture an error.

## Database-backed Google rollover evidence

The final isolated gate used a fresh schema, a separate synthetic Sheet in the
configured Drive folder, and a two-data-row shard size. Three synthetic final leads
were queued in one immutable batch.

At 2026-10-11 09:21:48 UTC, Apps Script reported:

```text
ISOLATED_DELIVERY_PASS {"passed":true,"rows":3,"shards":2,"replay":true}
ISOLATED_GOOGLE_ORIGINAL_DESTINATION_RESTORED
```

Database inspection confirmed one acknowledged receipt with count three, first
allocation position zero, shard size two, a verified Drive receipt and
`payload_removed=true`. The isolated source, candidate and validated-lead tables were
all empty after ACK. The immediate replay returned IDLE.

The temporary Apps Script verifier was removed after the pass. Its guarded function
restored the original Sheet property in a `finally` block, and the repository-side
restore then returned Render to the original database and Sheet bindings.

## Other bounded live evidence

### Real delivery and cleanup

The earlier two-lead delivery proved exact Sheet readback, Drive JSON/CSV receipts,
ACK and PostgreSQL deletion. A numeric-looking score initially exposed Google type
coercion; delivery now applies plain-text formatting before the write so leading
zeroes and decimal precision survive exact readback.

### Cloudflare and laptop-off operation

The configured Cloudflare Worker ran as the lightweight coordinator. A real scheduled
event reached the deployed Render service and obtained the expected bounded 202 tick
response. During the user-confirmed laptop shutdown interval on 2026-10-06, a cloud
cleanup job completed and three independent Apps Script delivery checks ran. Local
Python, Node, clasp and Codex processes were therefore unnecessary for those cycles.

### Bounded synthetic cloud flow

On 2026-10-08, three labelled synthetic raw records entered the deployed processing
boundary with real source adapters and model providers disabled. Durable scheduled
jobs normalized three records, validated one candidate, classified one candidate,
acknowledged one delivered lead, and rejected the duplicate and irrelevant records.
The Google replay was IDLE. Provider usage did not increase, and temporary payloads
were removed after the verified Drive receipt.

This test covered orchestration and data movement. It did not claim real-source lead
quality; that is the small, separately authorized Phase 3 cycle.

## Runtime safeguards completed in Phase 2

- Migration 0004 adds persistent request/token windows, provider limits and resets,
  cooldowns, reservations, 429 counts and admission deferrals.
- Migration 0005 adds a persisted semantic-drain deadline so campaigns cannot wait
  indefinitely for model capacity.
- Groq Free-plan telemetry reports actual pipeline cost as zero. Any historical
  list-price arithmetic is explicitly labelled reference information.
- Provider admission reserves a conservative prompt and completion allowance before
  an HTTP request. Quota deferrals do not consume candidate failure attempts.
- Scheduler work skips disabled acquisition and processing stages while preserving
  cleanup and campaign/deadline advancement.
- Apps Script final delivery is independently scheduled, idempotent and guarded by
  an optional absolute expiry for bounded tests.
- Delivery ACK removes temporary payloads only after exact Sheet readback and a
  verified Drive receipt.

## Safe state after validation

Final read-only verification after the restoration deployment returned:

```text
deployment=live
environment=test
paused=true
acquisition_enabled=false
processing_enabled=false
bluesky_enabled=false
youtube_enabled=false
groq_enabled=false
grok_enabled=false
final_delivery_enabled=false
scheduler_enabled=false
raw_pending=0
candidates=0
validated=0
delivered=3
```

Render remains pinned to the previously verified Phase 2 deployment commit
`1d989a4e9570736e296d7efa22bb4b5000cc5d93`.

Cloudflare has no active Cron schedule, its coordinator and public route are disabled,
and Google has no temporary Phase 2 helper. No outbound-email function was enabled.

## Testing provenance

| Check | Result |
|---|---|
| Python regression with disposable PostgreSQL integration | 91 passed, 0 skipped, 0 failed |
| Ruff | Passed |
| Apps Script delivery/backup harness | 15 passed |
| Apps Script bounded-delivery guard | 4 passed, zero external calls |
| Cloudflare Worker tests | 21 passed |
| Alembic empty-database upgrade through 0005 | Passed |
| Alembic 0004/0005 downgrade and re-upgrade | Passed |
| Alembic schema drift check | No upgrade operations detected |
| Git whitespace check | Passed |

PostgreSQL integration and migration tests used disposable or explicitly isolated
schemas. Private `.env` values, credentials, resource identifiers, contact values and
personal details are intentionally omitted from this report.

## Boundary between phases

Phase 2 is the controlled proof that the cloud components and safety controls work
together without the laptop. It is complete.

Phase 3 remains stopped. It is one small real source-to-Sheet-to-Drive cycle with hard
source and model caps, isolated inspection, no outbound email, and an explicit stop
after lead-quality review. Before starting it:

1. Review the credentials whose replacement was deferred on 2026-10-08 and rotate any
   credential still considered exposed.
2. Smoke-test only the replacements that changed.
3. Obtain explicit authorization for the bounded real-source run.

Phase 4 is the later approximately 200,000-raw-record, seven-day campaign. Phase 2 does
not authorize it and does not guarantee lead yield.

## Historical material

The original September 13 report is preserved as
`docs/PHASE_2_TEST_REPORT_2026-09-13.md`. It describes the older 33-test, 13-table,
xAI-era state and its `PHASE_2_STATUS=FAIL` verdict. That file is historical and must
not be used as the current project status.
