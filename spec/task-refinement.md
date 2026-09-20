# Outcome refinement

Authority: [tasks](tasks.md), [ownership](maintenance.md), [formulas](formulas.md),
[gates](gates.md), [retained handoffs](handoffs.md).

An addendum clarifies one existing delivery outcome: its acceptance test still
answers the same question. Independently shippable results, different acceptance
criteria or independently owned delivery stages belong in separate linked Beads.
A common subsystem or conversation is not sufficient reason to bundle outcomes.
Semantic classification is a reviewed admission decision, not an LLM polling job
or a keyword heuristic. Existing deterministic facade operations execute it.

Before refinement, read native routed tasks including assigned and closed records,
not only ready output. Bound the inventory and fail on overflow. Check completed
history and current owners; reuse an existing outcome rather than create another.
Record a dated, evidence-pinned mapping: original ID/scope, canonical execution ID,
objective, owner route, acceptance, disposition and actual authorization provenance.
A snapshot is historical evidence, never a second queue or current execution policy.

For same-outcome clarification use `task RUN update ID --file TEXT --key KEY
--authorization-file AUTH`. For an independent outcome use `task RUN create` with
an immutable key/body and metadata linking its objective, source ID and refinement
record. Use existing formula admission for independent delivery roles where suitable.
Append a mapping to the open source; never erase its original description or notes.
Use `task RUN dep add CHILD PREREQUISITE --issuer ACTOR` only for genuine blockers.
Informational source links are not blocking edges. Exact replay is idempotent;
changed content under a key fails. Reconcile uncertain writes before retrying.

Never edit closed records merely to tidy history, reopen completed work, duplicate
existing formulas, transfer claims or silently broaden approvals. Active scope must
be receipted and any handoff happen at an authorized safe boundary. Ownership,
recovery, pause and Claude design checks remain authoritative. A split records
existing authority, not approval for implementation, merge, deployment or publishing.

One canonical execution path per outcome is a review invariant. Before accepting a
refinement, verify each remaining outcome has exactly one ID, explicit acceptance
and a designated route (unclaimed means queued, not owned by an invented worker).
Separate audit/config publication from applying permissions; separate on-demand
status reads from automatic delivery; reuse closed fixes without re-executing them.
Verification of dependency-unblocking is not proof of a full formula lifecycle.
