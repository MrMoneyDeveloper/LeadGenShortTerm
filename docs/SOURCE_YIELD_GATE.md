# Source-yield gate

Scaling toward 100,000 distinct qualified South African personal-insurance leads with a usable
public email is blocked until a source demonstrates real yield. This document records the measured
evidence, the source requirements and a bounded canary plan. **Do not execute the canary or any
larger acquisition without explicit operator approval.**

## Measured evidence (live, 2026-11-08)

| Source | Scanned | Insurance intent | Usable email | SA evidence | Real finals |
|---|---|---|---|---|---|
| Bluesky firehose (random sampling) | ~8,150 posts | 5 keyword matches, all rejected | 0 | n/a | 0 |
| Bluesky search (38 phrases, 30 days) | 736 posts | 59 rule-qualified candidates | 0 (48 public bios fetched, 0 emails) | 2 of 59 | 0 |
| YouTube (recent ZA videos) | 1,351 comments | 0 qualifying | 0 | n/a | 0 |

Observed real yield is **0%**. Even at a 1% raw-to-final rate, 100,000 finals would need about
10 million raw records. No schedule can be promised until a source's yield is measured.

## What a qualifying source must provide

1. Consumer intent for South African personal insurance (short- or long-term per `target_products`).
2. A usable email published by the person (or supplied with consent), with the URL where it was
   found or the consent record, never inferred, guessed or invented.
3. South African evidence (`require_south_africa: true`).
4. Terms that permit automated access at the planned volume, and a lawful basis under the
   operator's policies.

Generic business, broker, info@ or role addresses are not substitutes for consumer intent and are
rejected by the existing broker/role/disposable rules.

## Source assessment

| Category | Intent | Email | Fit | Notes |
|---|---|---|---|---|
| Authorized opt-in inputs (own form/landing page with consent) | Explicit | Explicit, consented | Best | Needs a capture endpoint and consent wording; not built |
| Approved partner feeds (brokers, comparison sites, affiliates with consented leads) | Explicit | Explicit | Strong | Contractual; ingest through a new adapter into the same pipeline |
| Bluesky search | Yes | No (measured) | Poor for email | Keep only as intent signal research |
| YouTube comments | Rare | No (measured) | Poor | Keep disabled to save quota |
| Reddit / Mastodon / forums | Sometimes | Rare | Unproven | Check API terms first; measure before scaling |
| Classifieds and scraped sites | Sometimes | Sometimes | Excluded by default | Usually prohibited by terms of use |
| Business directories | No consumer intent | Business addresses | Not acceptable | Would violate the target definition |

## Bounded canary plan (prepared, not executed)

Pick **one** source at a time. Limits: at most 500 raw records, one day, existing per-source API
caps, at most 20 Groq requests, and no change to qualification thresholds.

Prerequisites: live database migrated to `0007`, Render deployed from `main`, Apps Script code
pushed, the source enabled in `config/sources.yaml`, `ACQUISITION_ENABLED`/`PROCESSING_ENABLED`
and the source's env flag set on Render, and the pipeline resumed.

Run through the existing API with the operator token (Render URL and tokens from the ignored `.env`):

1. `POST /admin/sources/<source>/enable`
2. Repeat until about 500 raw records are stored: `POST /jobs/collect` with
   `{"source": "<source>", "batch_size": 100}` and a fresh `Idempotency-Key`, then
   `POST /jobs/process-next`.
3. Let ticks drain normalize, validate and classify (or queue them with `POST /jobs/process`).
4. Read `GET /dashboard/source-stats`, `GET /dashboard/backpressure` (`accepted_finals`) and
   `GET /dashboard/samples` for the quality review.
5. `POST /admin/sources/<source>/disable`.

Report raw stored, keyword matches, intent candidates, usable emails, SA evidence and finals.

**Pass:** at least 5 real finals and at least 0.5% raw-to-final, with a clean quality review (no
broker, generic or invented contacts). **Otherwise stop** and evaluate the next source category.
Only a passing canary justifies a larger, still bounded run sized from the measured yield.
