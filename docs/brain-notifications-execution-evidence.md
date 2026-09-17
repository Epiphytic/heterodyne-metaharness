# Brain notification execution evidence

Operator-reported deployment and verification, recorded 2026-09-17 UTC.
Contract: [shared brain specification](../spec/brain.md).
Operations: [deployment runbook](brain-notifications.md).
Implementation deployed at `08ef9fb`; initial deployment was `d1e9a5f`.

## Installation and dependencies

The reviewed installer changed five existing files and created one runtime config.
Reapplying the same installation plan changed zero files. Exact installed Codex
hook hashes were trusted; unrelated hook configuration and native approvals were
preserved. The generic session-sync plugin uses an explicit canonical harness path;
the Hermes maintenance guard remains separate.

The post-enable MEMORY plan changed one entry, preserving the prior preference for
visible session-chat notifications. Reapplication changed zero files. Scoped brain
mirror update committed as `9e6af89`. Private plans/backups were retained outside
published brain paths; their configuration contents are intentionally omitted here.

No new package or dependency installation was required. The implementation uses
Python standard-library SQLite/filesystem tooling, existing YAML installer support,
native provider hooks, and the existing acknowledged Marmot outbox. The repaired
Hermes Python/SQLite dependency status is recorded in
[the preceding runtime evidence](sqlite-runtime-execution-evidence.md); this task
performed no further runtime upgrade.

## Actual delivery evidence

The original Hermes manager and Codex worker both received and acknowledged their
initial inventory notices. Operator inspection confirmed two stable role checkpoint
keys and no remaining context pending notices after exact Codex resume. Native
session identities were preserved. Enrollment acknowledged notification inventory,
not reading or knowing the contents of every enrolled file.

A later natural manager turn received automatic notice
`57e8e9735acb43c38285cfec85120f3e`, listing changes to `memories/MEMORY.md` and
`spec/brain.md`, with zero additional changes. Its context checkpoint acknowledged.
The corresponding visible receipt was delivered through the existing outbox:

- Event: `brain-visible:72c2d17c8c2abc0f79d35bd71c7fadf53e02f51691a4a8153a820401e1b42cf0`.
- Recipient: the exact bound maintenance Marmot group.
- `delivered_at`: `1789613175.9041057`; attempts: `1`; error: `NULL`.
- Unresolved visible routing count at operator verification: `0`.

The worker directly observed automatic incremental notice
`a6a4294528794aa592a37504925afe14` in developer context while recording this document,
listing the same two paths and zero remaining changes. Operator post-turn inspection
confirmed its durable acknowledgment: `brain_pending` count was `0`, with two
`brain_visible` receipts. Both visible outbox rows were delivered with one attempt
each and `error=NULL`.

## Verification and limits

Operator full suite: 176 tests, successful with one native-only test skipped.
Separate native verification: four tests passed. Native PluginManager hook invocation
was tested using the repaired Hermes venv from an unrelated working directory.
Services were active after deployment; original manager and worker identities remained.

Focused fixtures cover inventory enrollment, bounded additions/changes/deletions,
independent checkpoints, pending delivery across restart/native-ID change,
transaction serialization, failed scan and failed receipt, exact compression lineage,
provider transcript schemas, installer repetition, visible routing ambiguity,
transport retry/deduplication, and fair routing after more than 100 unresolved receipts.
Visible-routing failure does not prevent an attempt to deliver existing progress.

Claude hook configuration and transcript fixtures were verified. No live Claude
model turn is claimed. No physical reboot was performed for this task. Unknown
routing and uncertain transcript receipt remain pending rather than being guessed
or marked delivered. Task-addendum/admission work remains the separate dependent
Bead; it is not included in this completion evidence.
