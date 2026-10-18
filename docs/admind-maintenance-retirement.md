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
Every invocation must return the exact closed exit-75 not-issued envelope or have
positively observed in-process spawn failure before any child may exist. The
latter advances the counter and covered proof atomically without pretending a
child returned HOLD. A missing or ambiguous launch record never gains coverage.
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
current selector; earlier tokens then refuse. One inspection publishes at most
one selector across the bounded pending snapshot. Delayed, quarantined and
suppressed transport intents retain their mapping and are never treated as failed.
A completed retirement has one final receipt. Notices and acknowledgements use ordinary durable outbox
history, not new ask slots. Unproven historical attempts can still occupy the
existing capacity; this feature does not abandon them or fabricate absence.

Integration overlaps are Store initialization/new-attempt/issue markers and the
daemon's command/maintenance-notice hooks. Existing fulfilled-question and
ordinary maintenance reconciliation behavior is retained. No sibling worker's
unreviewed implementation was imported.

Explicit `!asks` inspection repairs a failed or sent-without-message-ID notice
by publishing a new transport notice. Original outbox rows remain immutable;
there are no new per-retry history rows. The legacy notice-history table, if
present, is retained without backfill or append. Its old rows confer no current
selector authority. Previously delivered selectors are recognized from the
original structured outbox keys when reporting an existing final receipt. An older
selector reports that receipt only if the ask has a single historical attempt;
otherwise its attempt association is ambiguous and refuses. The exact selected
token remains usable. Redelivery of the same authenticated command returns its
receipt without another read or effect.

Two usability limits remain unwaived under the approved authority contract.
Retryable overlays change generation roughly every 30 seconds, so an operator's
reply can become stale in transit and require another `!asks` inspection. A
suspended attempt whose configured adapter has changed remains held; accepting
new adapter interpretation of the historical chain requires separately approved
source/recovery compatibility. Neither a timeout nor this implementation clears
those holds. These are deployment considerations, not newly granted recovery
powers.

Adapter hashing runs in worker threads during read preparation, direct legacy
calls, retirement and post-child verification. The final authority-to-launch
fence uses a cache keyed by device, inode, size, mtime_ns and ctime_ns. Hashes come
from a bounded opened regular file with matching before/after fstat stamps. A
cold or changed entry at that fence refuses before spawning; it never adds an
await or a synchronous content read. File stat and open operations still depend
on the trusted local filesystem; this is not kernel confinement or proof of the
transitive import closure.

Proof and overlay restoration commit in one transaction. Notice publication is a
separate transaction: a delivery/storage error cannot erase a successfully
committed HOLD observation. Failure to commit the proof/overlay still leaves
issuance conservatively held with no manufactured coverage or replay authority.
Only the adapter's dedicated pre-spawn exception (cache miss or Popen OSError)
can extend coverage without a child. A generic unavailable error cannot do so.

Ordinary terminal settlement removes only the exact attempt's active proof and
notice mapping; immutable attempt/outbox history and final receipts remain.
Exact same-command receipt replay checks persisted sender, thread and token
before current authorization, and creates no fresh effects even after revocation.
A different command still requires current operator authority. Newly generated
proofs derive HOLD count as invocations minus not_spawned; legacy hashed proofs
with the redundant consecutive field remain readable without rewriting their
bytes or granting missing coverage.
