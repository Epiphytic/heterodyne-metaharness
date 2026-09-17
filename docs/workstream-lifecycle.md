# Workstream lifecycle rollout

Normative contracts: [status](../spec/status.md), [worktrees](../spec/worktrees.md),
[change lifecycle](../spec/changes.md), [approvals](../spec/approvals.md).
This is an operator review/runbook, not execution evidence or a completion claim.

The native-state/status unit is signed commit 796ce5c. Its full suite ran on that
commit: 201 tests, OK, one native-only skip, 26.076 seconds. The earlier reaction
units remain on this branch. No remote is configured; PR stage is recorded as
blocked-on-infrastructure in the assigned Bead. Operator will supply the private
remote. Do not create a public repository, claim a PR, merge, or close before the
actual review and post-merge full-suite stages.

## Reviewed deployment order

1. Inspect signed units and run the complete suite on the final source commit:
   `env -u HERMES_WORKSTREAM_RUN -u HERMES_WORKSTREAM_ROLE -u BTQ_SESSION_ID python3 -m unittest discover -s tests -q`.
   Run `python3 -m harness.governance validate` and `git diff --check`.
2. Preserve a coherent private backup of harness SQLite, checkpoints and current
   service/source revisions before deployment. Stop only the supervisor during
   SQLite backup/cutover; preserve live native panes. New approval tables are
   additive and initialize on first approval command; status tables on Store open.
   Restore backup only with the supervisor stopped and no concurrent CLI writer.
3. Operator reviews/merges the authorized source at the canonical checkout, then
   restarts `hermes-workstreams.service`. No new packages, global hooks, native
   permission changes, broad Hermes update or wn-agent rebuild are required.
   Existing wrappers import the canonical checkout. Existing manager launch
   guidance is refreshed only on fresh launch; for this exact resumed manager,
   supply the new spec pointers at its next natural turn via shared brain notices.
4. Inspect `workstream status hermes-maintenance` for native idle/working/turn key
   and unchanged worker/manager native IDs. Observe one natural completion and
   subsequent idle interval; verify no repeated unchanged idle outbox row. An
   ongoing turn must continue at the existing <=250-second production interval.
   Do not start extra model workloads just to exercise this check.
5. Existing bound task stays in the current checkout. Test the next independently
   authorized Bead boundary only with a clean, idle worker and no native approval.
   Verify its pinned workspace.json, branch/base, local dependency manifest,
   checkpoint workdir and native exact resume. No force migration or task replay.
   A failed handoff retains workspace_transition: inspect exact pane/path/identity
   before retry, never launch a replacement conversation or guess a checkout.
6. Record actual committed/tested/PR/merge/final-tested evidence on the Bead using
   the stage CLI. The checkout must be at the pinned signed SHA at each boundary.
   Retain full logs privately and reference SHA256s. Only then record close-ready
   and use the normal evidence-backed close. Governance completion separately
   waits for committed immutable deployment/dependency/verification artifacts.

Stage example (JSON file contains actual values, never these placeholders):
`workstream task RUN stage ISSUE --stage tested --evidence-file /private/test.json`.
Required keys: commit, evidence_ref, result=passed, full_suite=true, command,
log_sha256. PR and operator merge keys are described in the active contract.
Squash/rebase merges that do not retain PR ancestry require operator reconciliation;
this first implementation accepts a verified ancestor relationship only.

## Approval review and limitations

Use `workstream approvals RUN ask --file /private/ask.json` for a manager-side
request; exact schema is in the spec. Inspect policies with `... inspect`, record
operator decisions with `... override`, and retroactive incidents with `... incident`.
No native prompt is accepted, and no terminal approval key is sent. Manager routing
uses the current durable inbox/outbox, with a question ID and private evidence
locator. A Marmot arrow-key question widget is not exposed by the verified facade;
this implementation does not claim that UI integration.

A bounded read-only audit of this worker's native transcript at implementation time
found 241 tool-call records; 13 call bodies mention require_escalated. Overlapping
literal categories: unittest 7, git add 4, git commit 4, git push 1. These are source
mentions, not proof of native approval outcomes or 13 distinct commands. No raw
transcript or command output is published. The audited requests primarily execute
code, Git hooks or network operations, so they do not justify broad automatic
promotion. Exact safe read forms are the default harness policy; future verified
safe asks tune run/global tiers deterministically. Arbitrary test/module/git-commit
commands remain reviewed. A policy allow does not grant filesystem permission or
alter any existing Codex rule. This limitation is intentional and explicit.

Local cp --reflink=auto requires GNU cp. Unsupported reflinks use independent local
copies and may increase disk use. Dependency trees retain any internal symlinks,
absolute venv shebangs and editable paths: no promise of arbitrary relocatability.
Git object storage is shared; no agenticow dependency is installed. Full-suite
fixtures cover provider contracts; no physical reboot or live Claude turn is claimed.

Quality review: ripwire quality-delta remains nonzero. Refactoring removed added
CLI dispatch complexity; remaining findings include dynamic fixture methods marked
dead, repeated test setup/claim shapes, parser size/churn, and new classifier and
evidence-validator complexity. This is not a clean quality gate claim. Focused
behavioral tests cover these policy boundaries; operator review remains required.
