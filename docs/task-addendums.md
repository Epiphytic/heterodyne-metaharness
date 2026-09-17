# Task handoff deployment and verification

Contract: [task specification](../spec/tasks.md). Modules:
[admission/projection](../harness/tasks.py), [receipt adapter](../harness/task_hooks.py).
The existing shared hooks load these modules; no new provider hook or package is
installed, and native approvals are unchanged.

After source review and integration, restart the supervisor to load dispatch guards.
The installed session-sync plugin imports canonical modules, so restart/reload affected
Hermes managers through exact native resume to refresh cached Python imports. Coding
hook subprocesses load current source on their next invocation. Preserve conversations.
Back up harness SQLite/checkpoints privately in a quiet window before schema creation;
tables are added lazily by the first task operation/hook. No Bead schema migration.

Use the operator workstream facade for initial reconciliation, retaining the claim:

```sh
workstream task hermes-maintenance reconcile btq-harness-a40956ff84a9873066149391
workstream task hermes-maintenance show btq-harness-a40956ff84a9873066149391
```

The second command should work inside the worker sandbox too. `show` now returns
`{snapshot: {revision, issue}, read_only: true, current_claim_must_be_verified: true}`.
`context` includes the same projection plus its existing `context` text property.
An unavailable projection fails explicitly; it never initializes a writable Store
or silently substitutes a remote queue call. Inspect-before-claim of routed admitted
Beads is supported without replacing the active binding.

For an authorized addendum, put exact text and the actual prior authorization
reference in separate private files. Do not invent user approvals to test this flow:

```sh
workstream task hermes-maintenance update btq-harness-a40956ff84a9873066149391 --file /private/authorized-addendum.txt --key STABLE_UPDATE_KEY --authorization-file /private/authorization-reference.txt
```

Repeat with the same key/files: native notes and inbox entry remain single. Altered
content under that key must fail. Inspect task_updates confirmation, task_current,
the worker checkpoint, and the exact current-owner inbox message. Observe the natural
worker turn and task_receipts entry for that revision. Native input is deferred while
working, unknown, approval-blocked or recovering; no interrupted tools or auto-approval.
A source/ownership update observed before dispatch supersedes the stale inbox message.

External direct bd changes require explicit `task reconcile ID` by their writer or
operator. Installed BTQ show delegates to full `bd show ID --json` and retains its
`dependencies` records; reconciliation does not substitute dependency counts. Scope
checks cover fields enumerated in the active spec, including acceptance/design/spec
and dependency changes. Close performs its own fresh native show and refuses changed
unreceived scope even if no facade update was recorded. Existing claims enrolled
after deployment need an initial context receipt before close; reconcile/failed close
never invent a claim baseline. Already-closed retries retain native owner/recovery
checks and prior evidence. No remote push subscription or continuous pickup polling is claimed. For an
uncertain append, repeat only to reconcile a now-visible exact marker/body. If still
absent, inspect native audit/history and pending backend operations; do not manufacture
a new key to replay the append. Resolve uncertain effects before further task work.

Validation must include existing Beads claim/recovery/approval tests, actual CLI
claim/update/read-only projection, unbound admission during an active task, duplicate
and failed writes/delivery, owner changes, native context receipts and no stale
checkpoint overwrite. Record live results separately before historical completion.
