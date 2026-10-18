# Proven not-issued maintenance retirement

This additive implementation follows the approved maintenance-recovery design at
`aa860fe7b16cfbe452c7bae4ba18cf285c177a7c`,
`docs/adr/2026-10-18-admind-maintenance-recovery.md`.
It adds no native decision writer and does not authorize deployment.

A current configured, authenticated operator replies directly to a delivered
eligible held notice with `!asks retire <24-lowercase-hex-proof-token>`.
The original operator need not remain authorized. The token selects evidence;
it grants no authority. Authentication, current membership/policy and the final
attempt/proof/generation compare-and-swap remain required.

New attempts record a never-reset invocation count before child execution.
Every invocation must return the exact closed exit-75 not-issued envelope.
Historical attempts migrate with proof capability disabled permanently, and a
missing invocation proof never heals. Unknown native state, stale notices,
changed adapters and incomplete proof retain the original attempt. Existing
exact native decisions use ordinary reconciliation. Retirement preserves the
original attempt and exit status, records a distinct immutable receipt, and
queues one acknowledgement in the same transaction. Conflicting native state
uses the existing blocked settlement.

The adapter identity is the SHA256 of the configured approve-bead executable's
actual regular-file bytes, checked before and after the child and native read.
The current closed HOLD envelope does not carry a coordinator identity or the
transitive imported-source closure. This implementation therefore preserves the
existing trusted-host/configured-adapter admission boundary; it does **not**
claim authenticated coordinator provenance from a binary digest. Deployment
must retain that existing source-admission contract.

Each attempt has one active proof and one current notice mapping. Automatic
30-second retries update proof coverage without allocating per-generation
notices. `!asks` inspection or transition to a suspended hold can publish the
current selector; earlier tokens then refuse. A completed retirement has one
final receipt. Notices and acknowledgements use ordinary durable outbox
history, not new ask slots. Unproven historical attempts can still occupy the
existing capacity; this feature does not abandon them or fabricate absence.

Integration overlaps are Store initialization/new-attempt/issue markers and the
daemon's command/maintenance-notice hooks. Existing fulfilled-question and
ordinary maintenance reconciliation behavior is retained. No sibling worker's
unreviewed implementation was imported.
