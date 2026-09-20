# Transition delivery rollout

Contract: [current specification](../spec/transitions.md). Implementation is a
projection of existing native task facts; no package or provider-hook installation.

Manager deployment after review:

1. Retain a coherent private harness SQLite backup plus checkpoints using the
   existing quiet-window procedure. Preserve pending outbox and ask bindings.
2. Merge the reviewed signed revision into canonical main. Run the exact merged
   suite with managed session environment variables unset and retain its hash.
3. Restart `hermes-workstreams.service` from that clean canonical checkout.
   Store initialization adds journal tables; native agent sessions need no restart.
4. Inspect `workstream notice hermes-maintenance inspect`. Observe a real facade
   stage or authorized scope mutation. After the fixed 120-second deadline,
   verify its transition rows, batch, stable outbox IDs and delivered receipts.
   A no-op observation must not add rows. Do not create fake task transitions.
5. Verify an existing bound task emits no periodic heartbeat. Check retirement
   audit: only untouched legacy heartbeat rows are removed from pending delivery;
   original events and uncertain sends remain. Verify permission asks retain
   their existing message/reaction bindings.

First claim through either facade or authorized boundary continuation produces
one full Bead per stable worker/task. Deployment does not replay old handoffs or
old lifecycle history. Explicit `task reconcile` is needed after external bare bd
changes; intermediate unobserved native states cannot be recovered from a final
snapshot. Known retained lifecycle entries can be recovered in recorded order.

Actual-applied approval evidence can be submitted with `workstream notice NAME
record --file PATH`. Fields are documented in the spec. The caller must retain
exact native inspection and resolution evidence; a consenting reaction alone is
not an applied native approval. This ingress is an audit assertion, not a native
approval executor or cryptographic authentication of an actor string.

Batches with no group stay inspectable and unqueued; repair requires explicit
routing reconciliation, never a latest-chat guess. Delivery failure uses the
existing retry transport identity. Large batches/handoffs use numbered chunks.
Existing outstanding-ask rendering remains authoritative; the separately queued
ask/reviewer tasks own their expanded hold/reminder/review policies.

Rollback requires manager review: old code would resume legacy status emission.
Do not discard journal/outbox tables or restore an old database over live receipts.
No deployment, physical reboot or live review/approval is asserted by this source
runbook. Actual deployment evidence must be retained after manager execution.

## Source verification notes

Focused transition, continuation, queue-hook, prerequisite-gate and interrupt
coverage passed 62 tests before commit. The first full external WIP run exposed
three abbreviated fixtures missing native Bead titles; those fixtures now include
realistic titles and production title validation was retained. Sandbox native
socket execution timed out; exact-commit full-suite evidence is recorded through
the lifecycle facade after the source commit, not asserted here in advance.

Ripwire working-tree quality scan reports 25 major gating findings, including
supervisor complexity/churn, dynamically invoked fixture methods marked dead,
and test setup similarity; new helper parameter counts are also reported.
The bounded integration edits retain existing supervisor behavior except for
bound-task poll/model status emission, and keep test fixtures independent.
A duplicated digest helper was removed in favor of the existing implementation.
No clean quality-gate claim. Its test-gate reports 46 affected test files and
92 statically untested symbols; the full native suite remains required.
