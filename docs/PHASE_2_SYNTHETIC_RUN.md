# Bounded Phase-2 synthetic integration run

Status: PASS. Executed October 8 and verified October 9. See PHASE_2_TEST_REPORT.md
for timestamps, receipt and cleanup evidence. The procedure below records the approved
scope; it is not an instruction to repeat the run. This was not Phase 3.

## Scope and hard limits

Inject three labelled synthetic raw records at the ingestion boundary: one strong
insurance quote request, another for the same synthetic identity, and one irrelevant
record. Let deployed processing produce at most one final lead. This tests downstream
integration, not a real source adapter or real lead quality.

Use a user-controlled test email supplied privately, an obvious synthetic profile,
and example.org provenance. No email will be sent. Do not invent a mailbox, bypass
DNS/MX validation, prevalidate a contact or pre-create a final lead.

Keep acquisition, Bluesky, YouTube, Groq/xAI and the Render-local scheduler disabled.
Only processing and final delivery may be temporarily enabled. Zero source API and
model calls are intended. Cloudflare and Google run every five minutes with an absolute
expiry at most 30 minutes after arming. No local processing runner or manual tick loop.

## Preconditions

- Verify exact configured Render owner/service, database binding, Google and Cloudflare
  resources from private .env through direct APIs before mutations.
- Require test environment, paused runtime, empty transient queues and no outstanding
  delivery claim. Check the test contact is not already deduplicated using its digest.
- Prepare arm and restoration scripts before deployment; save original flags and pin
  the verified live commit. Keep the existing Sheet allocation and shard size.
- Store contact-bearing fixtures only in ignored private files; seed transactionally
  with stable IDs and hashes so retries cannot create extra records.

## Execution and evidence

Enable only processing/final delivery, verify deployment, seed three records, then
arm the expiring cloud triggers and resume. Record cloud-created jobs and outcomes.
Require one final lead, duplicate/irrelevant rejection, exact Google readback, verified
Drive receipt and matching delivery ACK before temporary payload deletion. Verify a
repeat delivery creates no duplicate. Log counts, times and statuses, not contacts.
Stop if unexpected data, destinations or provider calls appear. If the deadline is
reached, preserve unacknowledged payloads and report the stalled stage.

## Restore and scope limits

Pause; disable processing/delivery; remove Cron; disable Worker and public route;
stop Google's delivery trigger; remove temporary helpers. Verify the restored
configuration after deployment. Expiry is a fallback, not proof of cleanup.

This flow does not prove selective Groq inference, live quota headers, semantic-drain
expiry, real acquisition or cross-shard database allocation. Keep those gates separate.
Credential review is deferred until before Phase 3 under the user's October 8 instruction.
