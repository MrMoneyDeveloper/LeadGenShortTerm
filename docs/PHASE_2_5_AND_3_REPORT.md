# Phase 2.5 hardening and Phase 3 canary report

Updated: 2026-11-08

## Verdict

```text
PHASE_2_5_STATUS=PASS_EXCEPT_VERCEL
PHASE_3_STATUS=PASS_WITH_FINDING
```

Every Phase 2.5 gate passed live except the optional Vercel worker, which is blocked by the
token's permissions (Netlify plus Render fallback cover that lane). Phase 3 ran real-source
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
| Render | Live on the current `main` commit; internal database URL; `ENVIRONMENT=test`. |
| Cloudflare | Coordinator deployed via API with `.env` credentials; 5-minute Cron; `COORDINATOR_EXPIRES_AT=2026-11-11T12:00:00Z`. Owns acquisition/processing ticks. |
| Apps Script | Owns delivery (5 min), dashboard (15 min) and backup (hourly) triggers. Script Properties verified equal to Render by hash. Expiring operator endpoint (`Ops.gs`) deployed for headless control. Processor tick disabled (one scheduler owner). |
| Netlify | Stateless worker (contract v1); daily call cap 60 during the canary. |
| Vercel | Not deployed: token returns 403 for project creation in the configured team. |
| Tokens | Render, Apps Script and Cloudflare share the Render token pair (`*_TOKEN1` in `.env`); the unsuffixed `.env` token values are stale. |

## Changes since Phase 2

- Two GroqCloud Free-tier organizations with independent persisted quota state, routing,
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
| Python (disposable local PostgreSQL) | 124 passed |
| Ruff / `git diff --check` | Passed |
| Alembic `0006` upgrade → downgrade → upgrade, drift check | Passed |
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
| Vercel worker | BLOCKED | Token permissions |
| Cloudflare scheduling | PASS | Health 200, unauthenticated tick 401, authenticated tick 202 → Render tick; Cron installed |
| Apps Script live delivery, Sheet readback, Drive verification, ACK | PASS | Batch ACKNOWLEDGED at row position 3 (row 5 of `VALIDATED_001`), Drive file verified, payload removed, final queue empty |
| Replay idempotency | PASS | Immediate second delivery returned IDLE; no new row |
| Dashboard ×2, backup ×2 | PASS | Both succeeded twice; one backup history entry for the day |
| Backpressure | PASS | Drain at high water, state survived restart, hysteresis, resume at low water |
| Restart recovery | PASS | Repeated Render restarts without lost or duplicated work |
| Autonomous cloud operation | PASS | 13:52–14:04 UTC with no operator calls: the Apps Script trigger delivered a queued final on its own (ACK at 13:54:51, next row), and Cloudflare Cron invocations at 14:00 and 14:05 each made the 3 bounded subrequests with 0 errors and created Render work. The laptop stayed powered but uninvolved; a literal power-off test was not repeated (Phase 2 recorded one). |

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
| Vercel unavailable | PASS (never deployed; every run used Netlify/Render) |
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

1. Vercel token with project-create permission for the configured team (optional lane).
2. Source strategy that yields contactable South African leads before Phase 4.
3. Delete the three synthetic `SYNTHETIC_TEST` rows (`VALIDATED_001` rows 5–7) if not wanted.
4. Delete the Apps Script ops web deployment when headless control is no longer needed
   (it self-expires 2026-11-15); the Cloudflare Cron self-expires 2026-11-11.
5. One-time credential rotation, then align `.env` unsuffixed token names.
6. Replace the free database before 2026-12-08.
