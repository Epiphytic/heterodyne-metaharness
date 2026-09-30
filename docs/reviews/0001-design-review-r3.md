> Imported copy: local paths were normalised (`<repos>/`, `~`). The canonical, digest-pinned record is the design repo at commit `e36f6d0` (bead `btq-96hm`).

REJECT

**Unresolved r2 blocking findings**

- **#2 — Unresolved.** A separate worker per bead avoids `Queue.claim()`’s one active claim per worker limit, but the ADR says this works with `btq` unchanged. Current [`btq`](<repos>/beads-task-queue/bin/btq:15) rejects `wsd` as an agent and requires credentials for it. The ADR must make the required `btq` and credential changes explicit.
- **#6 and #9 — Resolved.** The [approval bead is now the decision commit point](<repos>/hermes-workstreams-v2/docs/adr/0001-workstreams-v2.md:275); SQLite holds a pending inbox, with read-back and startup reconciliation.

The r2 automatic-push and `kind:confirm` routing findings are resolved.

**New issues**

- **[BLOCKING]** [Pause applies only to the workstream worker](<repos>/hermes-workstreams-v2/docs/adr/0001-workstreams-v2.md:199), while each claim uses a different worker. [`Queue.claim()` calls that worker’s `ready()`](<repos>/beads-task-queue/bin/btq:123), so the queue’s pause check does not protect the claim. Specify a shared pause gate and a check immediately before claiming.
- **[NON-BLOCKING]** The [ADR header](<repos>/hermes-workstreams-v2/docs/adr/0001-workstreams-v2.md:3) still identifies revision 2 and says it is awaiting r2 review; update it to revision 3.