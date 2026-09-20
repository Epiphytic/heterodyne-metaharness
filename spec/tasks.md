# Task admission and addendums

Authority: [entrypoint](README.md), [ownership](maintenance.md),
[session lifecycle](sessions.md), [queue order](queue-order.md), and [context receipts](brain.md).
Beads remains authoritative for tasks, claim ownership and approval dependencies.
Local revision journals and checkpoints are projections, never an alternate queue.

Independent requests create separate Beads through generic task admission. Maintenance
only resolves its existing persistent binding and delegates to that same flow.
Admission queues one durable manager inbox message; it does not claim, replace an
active binding, resume pickup or spawn another worker. Original create keys and
fingerprints remain immutable. Existing maintenance request keys keep their namespace.

Addendums append notes on the same open Bead using a distinct stable update key and
caller-supplied evidence of actual authorization. This evidence is a record, not an
authenticated approval or permission expansion. Changing content under an existing
key fails. Persist intent before the remote write; reconcile an uncertain result by
its exact marker and body. If absent after uncertainty, do not blindly append again:
operator must inspect native history and resolve the outcome. No retry-create occurs.
Original descriptions, prior notes, create fingerprints and approval metadata remain.
Workers must assess whether changed scope remains covered by existing authorization;
Claude's design/dependency requirements still apply to implementation.

Claim/bind/reconcile and addendum operations persist full immutable revisions and
refresh the bound checkpoint after claim persistence, avoiding stale run overwrite.
Read-only task show/context uses SQLite mode=ro with no writable Store initialization.
Show can inspect an admitted unbound task only after exact route validation; it never
binds or claims. Output explicitly labels the snapshot as a projection. Native claim
checks remain required even when the snapshot says in_progress. Checkpoints contain
full task data under beads.task_snapshot and use durable atomic publication.

An observed active revision queues a deduplicated worker inbox notice for its verified
stable owner and is available through the shared native context hooks. Closed/unclaimed
tasks are not dispatched to a worker; their current data is delivered after a valid
claim. Recheck owner and current revision before dispatch. Superseded queued notices
are retired; immutable history and appended notes preserve prior changes. Missing
context receipt retries after restart/compaction; receipt of an old revision cannot
acknowledge a newer revision or another owner. Latest-state notices may coalesce
intermediate changes, whose full revisions remain retained in the journal.

Automatic task inbox delivery waits for native turn completion, with no approval or
recovery pending. Never type into a running tool or an approval prompt. Unknown turn
state defers delivery; natural context hooks can still supply the notice. Context
receipt proves delivery, not semantic acceptance. Direct steering has priority over
deferred task notices; a confirmed context receipt retires its queued duplicate.
Close fetches current native scope and compares it with the original claim baseline
or latest worker receipt, including external notes changes. Failed close/reconcile
cannot establish a new baseline. Closed Beads skip this fresh-receipt gate so repeated
close retains prior evidence; native owner/recovery checks still apply. Status,
timestamps and comment counts alone do not change the scope fingerprint. Scope includes
acceptance criteria, design, spec ID, metadata, route labels, dependency records,
assignee, priority/type, scheduling/estimate fields and external reference. A nonzero
dependency count without dependency records fails closed. Native claim-time routing
and Claude approval/dependency checks remain authoritative.

Direct external bd edits have no push subscription here. The external writer/operator
must invoke `workstream task RUN reconcile ID` after its edit or ownership change;
this refreshes the projection and queues the appropriate owner notice. Reconciliation
is also required before relying on external edits for completion. No continuous queue
pickup polling is introduced. Pause/recovery and original atomic claim/approval guards
remain unchanged; notification does not authorize new task execution.
