# Operator asks

See [delivery and status](status.md), [approval evidence](permission-relay.md),
[approval policy](approvals.md), and [authority](README.md).

Explicit operator questions, native approval surfaces, delivery escalations and
babysitter escalations are durable asks. Pure status is not an ask. Producers
must use `workstream ask NAME enqueue --file PATH --summary TEXT --key KEY`
for questions requiring operator input, rather than an unclassified status.
An existing state report can instead carry the explicit `event --operator-ask` flag.
This is routing metadata, never native approval authority. Approval and permission
relay producers register their asks automatically; multipart evidence stays intact.

Each ask retains its stable identity, exact run/group, short summary, delivery and
resolution evidence. Reusing a key with changed text or routing fails. Open asks
survive restart and native session changes. Use `workstream ask NAME list` and
`workstream ask NAME resolve ID --evidence-file PATH` after verifying an answer.
Consent alone does not close an ask or accept a native prompt.

At the first send attempt, resolve the group's admins through the configured
`wn --home HOME --secret-store file --account ACCOUNT groups admins GROUP --json`.
Validate response account/group and each admin key/npub pair. Union current admins
with the designated operator public keys:
`3c9945ed7961a8a5b2ff8a43cb83ce214336fc873b47e770c2d7691fac7925bb` and
`04e4426415f4b047e7668ec08035b12dfadca6e8782fabbf95a34a5d86e92d80`.
Render their NIP-19 public references as plain `@npub…` text, without duplicate tags.
These operator recipients are policy; they are not substitutes for admin lookup.
Lookup failure leaves delivery pending. No operator-only or guessed-admin fallback.

The exact rendered text and sending account are committed before network I/O.
An uncertain retry reuses that text and idempotency key, even if membership changes.
Each subsequent distinct notice resolves membership anew when it contains an ask.
The outbox owns retries and restores the transport deadline after an attempt.

Later text in the same chat ends with a short labeled reminder of outstanding
delivered asks. It does not resend their original bodies. The reminder is the final
paragraph of that notice, so queued statuses cannot overtake a separate reminder.
Show at most eight summaries plus the remaining count and inspection command.
Existing status dedup runs before rendering; reminders add no timestamps/randomness
to defeat dedup. Reactions do not constitute new text. This ordering covers harness
outbox writers; unrelated chat senders are outside its ordering guarantee.

Hourly backstop questions must use `--hourly-backstop`, which resolves the existing
maintenance workstream and verifies its canonical `belthanior-maintenance` group
`1cead9a9921044b3236ddb271e9a4cac`. A changed binding fails closed.

## Deployment constraint

`marmot.group_admins.binary` and `.home` are explicit reviewed configuration.
The CLI opens the account database directly; prior live-home WAL damage is recorded
in the Marmot runbook. A successful CLI response alone does not establish concurrent
database safety. The operator must reconcile that hazard before enabling recurring
lookups on the running home. No silent CLI fallback is permitted from socket code.
Future socket admin lookup may replace this adapter without changing ask state.
