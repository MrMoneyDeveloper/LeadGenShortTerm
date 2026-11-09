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

## Experiment 1: broad Bluesky/YouTube run (2026-11-08 to 2026-11-09), concluded

Unattended collection ran about 10 hours, with every infrastructure check OK, then was paused
(source rows disabled, work drained to zero). Checkpoints, hashes, receipts, samples and Google rows
were preserved.

| Measure (Bluesky, both days) | Value |
|---|---|
| Posts scanned | 8,993 |
| Stored after keyword filter | 530 |
| Rule-qualified intent candidates | 62 |
| Public emails found / passing MX | 1 / 1 |
| Distinct qualified finals | **0** |

**Funnel audit** of the preserved same-cohort samples (20 strongest Bluesky intent candidates and 20
insurance-relevant YouTube comments):

| Stage | Bluesky | YouTube |
|---|---|---|
| Consumer-intent signal | 20 | 0 |
| In-scope product | 14 | 0 |
| SA evidence in post | 0 | 19 |
| SA evidence in author bio | 0 | n/a |
| Public email (post or bio) | 0 | 0 |
| Broker signal | 0 | 0 |

Manual review of the 20 selected Bluesky samples found each rejection correct:
- 9 anecdotes or complaints from the US, Netherlands, Canada or Norway;
- 3 product pitches;
- 2 insurer/broker adverts, rejected for low score rather than flagged as ads;
- 5 politics, legislation, job and clinic posts;
- 1 genuine consumer request, but for pet insurance in the US (out of scope).

No false rejections were found **in those reviewed samples**; this is not a measured live false-rejection
rate. The sources fail on complementary requirements: Bluesky has intent
but no SA evidence or emails; YouTube has SA context but no buying intent. YouTube `regionCode=ZA`
means videos viewable in South Africa, not that a commenter is South African.

**Qualification gaps fixed afterwards (regression-tested, acceptance unchanged):**
- same-author bio may supply explicit SA geography, with provenance `geography_evidence: profile_bio`;
- a consumer asking for a broker (`broker_request`) cancels only the broker-*mention* penalty. Explicit
  seller self-identification (`seller`: "I sell insurance", "I'm a broker", ...; typographic apostrophes
  folded for matching) is a hard negative that is never cancelled. Phrasing both sides use
  (`solicitation`: "dm me for a quote", "our brokerage", ...) and any `broker_request` are review-only:
  never `DIRECT_FINAL`, semantic review decides. Rules results from an older rules version also never
  route `DIRECT_FINAL` (review round 3);
- the profile-bio location excerpt is passed to Groq as a separate labelled field
  (`same_author_profile_location`) and is appended, with the profile URL, to the delivered
  `source_evidence` so it survives ACK cleanup. The bio is redacted before the window is cut, so no
  partial email/phone/handle can appear, and an SA domain inside an email or URL is not location
  evidence (review round 3);
- explicit consumer intent + in-scope product + SA evidence ranked below `semantic_at` now goes to
  semantic review (`review_route`) instead of being dropped as DEFERRED;
- the labelled fixture keeps precision 1.0 and 0 false positives. That is a regression check on a
  small hand-written fixture; it does not establish live-source precision.

## Source priorities

| Source | Role |
|---|---|
| Authorized first-party records or partner feed | First choice if available: verify intent, freshness, provenance and permitted use before import |
| Own insurance enquiry form | Strongest contact capture; needs visitors or partner referrals to generate submissions. A draft form and a *disabled* intake integration can be prepared in-repo; publishing it, choosing how visitors reach it, and any partner feed agreement are operator decisions |
| Approved SA communities/forums | Small feasibility experiment only after access permission and contact availability are established |
| Bluesky | Limited, targeted discovery experiment (below) |
| YouTube | Paused |

Opt-in or partner emails are consented contacts. They must be labelled separately from public emails
and explicitly added to the target definition before they count. Reddit is not an immediately
available commercial source: its Data API terms require approval for commercial access.

## Bounded canary (one source at a time)

Limits:
- at most 500 **scanned** records or 24 hours, whichever comes first; stopped automatically by a
  campaign started with `raw_target=500, duration_days=1` (drains when either is reached). Each
  collection is clamped to the campaign's remaining scans and an adapter that returns more than it was
  asked for commits nothing, so 500 is a hard ceiling (review round 3);
- at most 20 Groq requests in total: `GROQ_CAMPAIGN_REQUEST_CAP=20` is a persisted experiment-wide
  budget covering both organizations, every UTC day, retries and semantic draining. The per-org
  daily soft cap alone (5/org/day) did not guarantee this: acquisition plus draining can span three
  UTC dates (30 requests). Exhausting the budget defers semantic work; nothing resets it;
- existing qualification standards, no outbound messages.

Do not repeat collection until some number of records is *stored*: that could take an uncontrolled
number of scans. The scanned budget is the only acquisition bound.

Report: scanned, unique stored, intent candidates, SA evidence, usable emails, distinct qualified
finals (and no-email prospects separately), duplicates, provider usage and elapsed time. Manually
review every final.

**Expansion gate:** at least 5 distinct real qualified email contacts, each manually reviewed, before
considering a larger bounded experiment (5 from 500 scanned is 1%). None of the routes above
(form, partner feed, communities, Bluesky) is a guaranteed route to 100,000 contacts. Zero finals stops that source
experiment for investigation. Passing the gate permits further evaluation only; it does not establish
capacity for 100,000.

| Sustained unique-record yield | Records needed for 100,000 contacts |
|---|---|
| 1% | 10 million |
| 5% | 2 million |
| 10% | 1 million |

Source supply, freshness and duplicate rates must support those volumes; processing capacity alone
cannot.
