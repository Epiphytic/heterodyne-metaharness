# Change lifecycle

Historical proposals are chronological append-only records, with explicit supersession
or revocation events. Proposal text and approval evidence never change after recording.
Lifecycle events may append; completed requires recorded approval, current spec updates,
implementation and infrastructure/dependency reconciliation, and verification evidence.
User authorization is evidence only for the actual requirements that user approved.
New proposals remain proposed pending real approval.

Run `python3 -m harness.governance validate` before review. The validator checks small
spec files, local links, runtime reference direction and historical lifecycle evidence.
Governance tooling may inspect historical metadata to validate it; runtime behavior,
SOUL and operating skills refer only to the active specification. Beads owns execution
tracking. A historical completed label is written only after deployment verification.
See [authority](README.md) and [maintenance admission](maintenance.md).

## Execution stages

For open/queued Beads use `workstream task RUN stage ID --stage STAGE
--evidence-file JSON`. Ordered stages are committed, tested, pr-open, merged,
final-tested, deployed, close-ready. Metadata retains each stage, timestamp, evidence and
content digest. Identical retries do not rewrite history. Existing closed records
remain historical facts; missing stages block new closure, not completed history.

Each stage pins the clean checkout's full HEAD SHA with verified G signature and
an evidence_ref. Test stages require result passed, full_suite true, command and
retained log_sha256. PR-open additionally records pr_url, remote and pushed_commit
matching the tested commit. No remote means blocked-on-infrastructure in Bead
notes; it is never an imaginary PR or permission to publish publicly. Worker may
push to an explicitly configured authorized private destination and open review.
Operator merges; merged evidence requires merge_authority operator and review_ref,
and the PR commit must be an ancestor of the merged result. Operator updates the
owned checkout to that result before recording/testing it. Final-tested must pin
that merge SHA. Deployed evidence additionally requires deployment_authority operator,
applicable true, target, deployed_revision equal to that SHA, result passed and a
retained live_verification_ref. Source installation instructions or test fixtures
are not live verification. Non-deployable work still records a deployed stage with
applicable false and an explicit operator-reviewed reason. Close-ready and close
require the same tested deployment. Existing open records without deployment cannot
close; retain their earlier evidence and reconcile explicitly, never rewrite history.

These are deterministic structural checks on trusted operator/worker evidence,
not cryptographic authentication of arbitrary review URLs or claimed test output.
The command does not execute tests or merge. Preserve actual test logs and review
records; fabricated evidence remains prohibited. Native ownership, dependencies,
approval, unread scope and recovery guards still apply. Lifecycle metadata itself
is not a scope addendum. No task is finished merely because edits were frozen.

Task checkout isolation: [worktrees](worktrees.md). Native state: [status](status.md).
