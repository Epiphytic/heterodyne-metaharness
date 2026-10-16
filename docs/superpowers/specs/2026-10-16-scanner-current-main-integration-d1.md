# Scanner: current-main integration and execution addendum d1

Status: proposed design, planning task btq-18sfj. No implementation, new review
verdict or human approval is asserted. Exact inputs are in
[the integration manifest](../../reviews/scanner-boundary/current-main-integration-inputs.json).
The [historical reconciliation](2026-10-16-scanner-boundary-reconciliation.md)
and its frozen evidence remain unchanged.

## Separate decision and review boundary

This proposal decides how the retained scanner change can integrate with merged
guard/create-lifetime main and how its implementation will be verified there.
It does not reconsider scanner regexes, accepted residuals, redactor precedence,
or scanner/redactor atomicity from frozen design d3. Those design and old-plan
cycles remain exhausted at three each. Historical plan r4 was independently
approved; r5 subsequently changed execution mechanics without a retained
independent approval. This proposal does not extend that approval to r5.

The new review examines only the base/landing-order amendment, current consumers,
replacement execution protocol, and admission boundaries below. Its maximum is
three rounds. A finding requiring a different scanner contract stops this piece
for explicit operator disposition rather than becoming a fourth scanner review.

## Why the old prerequisite cannot be assumed

The approved target proposed here is source base
`3aa124cd4c6e11343d8222cc7ac17d10bec98c34`. Historical prerequisite
`50fc42fb95dde0cdf38410c54e4e89bc3227f37b` is not its ancestor. Against the
common base, that branch adds or changes 84 files (19,919 additions, 188 deletions),
including asks, notices, review, journaling, session and sandbox runtime. Its
daemon, CLI, sandbox build and runtime overlap current lifetime work. Importing
that branch is a separate substantial integration, not a scanner setup step.

The local queue snapshot's closed btq-bswl5 records a genuine Liam decision via
Marmot `marmot:id:f2b47f6813a9`, digest
`a05221b86d2155e2c036ed204bc36e7d211395b87b3f6f66becbad55270c8fae`.
It approves plan5i parts 1–4 implementation at the four plan refs retained in
that record. It explicitly excludes merging any part, live execution and host
changes. It neither approves scanner behavior nor authorizes importing 50fc.
The manifest pins this local evidence; no fresh service verification is claimed.

**Proposed amendment:** implement the unchanged d3 scanner/redactor contract on
exact 3aa main before plan5i integration. Replace the 50fc ancestry requirement
for this implementation only. Do not import, discard or rebase plan5i work here.
When plan5i later integrates, its owner must run the retained scanner regressions
and all newly present scanner consumers, including asks/notices/harness tests.
Those absent consumers are deferred integration coverage, never reported as
passing or silently substituted by an empty test selection.

## Current-main integration invariants

1. Land scanner widening and all-original-text span redaction atomically. Retain
   the existing hook/marker conditional rules; inspect actual code before deciding
   the stored-marker version step. No invented inbox/event scrub symbols.
2. Preserve guard fixture changes in test_admind_approvals.py and
   test_admind_r13_redact.py. Preserve provenance quarantine, fresh-policy gates,
   observational membership behavior and explicit system ownership.
3. Current sandbox/creates.py calls sensitive_kind on escalation text, then uses
   fixed unknown-create or close-out wording if sensitive. Wider matching must
   not suppress escalation or close-out, expose secret-bearing fields, alter
   submission/settlement/gone evidence, or grant settlement from observation.
   This is alert-text filtering, not sandbox-name validation. Add fake/private
   ledger regressions for embedded newly detected tokens in workstream/bead
   fields, ordinary fields, both alert kinds, and safe fixed fallback rendering
   through admind alerts. Check payloads contain no original sensitive token and
   that fallback alert IDs and lifecycle evidence remain correct.
4. Keep create watcher locking, durability and close-notice-before-cleanup
   ordering intact. Run their existing recovery/concurrency regressions alongside
   guard membership/migration coverage. No production ledger-format change.
5. No template/marker vocabulary change. Existing accepted false positives can
   make more user text withheld or use the fixed create alert. That availability
   cost is disclosed, not a reason to relax the scanner in this integration.

## Replacement execution protocol for this base

This section supersedes historical host paths, branch-wide expected counts,
r5 shell/checker mechanics and the old parallel full-suite command for this
implementation. Appendix C is retained as historical evidence, not executed as
an implicitly reviewed authority. No new production test-runner framework is
needed. Use explicit commands and ordinary pytest JUnit reports with the checks
below; any small verifier belongs in the implementation's independently reviewed
test evidence and must itself be probed against malformed/error reports.

1. Admission pins the actual implementation parent, the new approved document
   digest, frozen d3/r5 input hashes and source manifest. Require exact 3aa base
   plus only these planning documents; if source changes, stop and record the
   concrete delta before relying on these baselines. Use a receipt-bound isolated
   implementation worktree. Do not overwrite historical scratch checkouts.
2. Use a task-specific locked dependency environment with Python 3.12 and tmux;
   record interpreter version/realpath and prove imported heterodyne comes from
   that worktree. Keep it stable for the run. Tests use fake backends/private
   processes only. Never enable live-test flags, inspect live credentials, or use
   a production queue/database. Setup/import failures are fatal, not expected red.
3. Extract Appendix A bytes from frozen r5 and verify its manifest hash before
   using its 88 tests. First run on unchanged exact source, then capture each
   assertion-only failing node, passing node, total, JUnit and process status.
   Retain private full identities; publication uses collision-checked hashes so
   embedded synthetic tokens do not leak into portable docs. Historical 53/88
   is a comparison, not an acceptance oracle. A discrepancy stops diagnosis;
   record a source/assertion explanation before proceeding, never automatically
   learn arbitrary failures as expected. The baseline is not permission to
   change an assertion or weaken scanner behavior.
4. Stage the historical Appendix B changes only after dry application and review
   of the resulting diff against current guard fixtures. Reproduce scanner-only
   partial-JWT red behavior in a disposable intermediate state, using the
   historical failing node identities checked against actual collected nodes.
   Never commit or merge that intermediate state. Finish the atomic scanner and
   redactor change, then require all 88 Appendix A tests green and targeted hook,
   redactor, config and alert consumers green. No historical full-suite totals
   are asserted for the changed base.
5. For each expected-red run require completed collection, exact node identities
   and totals, exit status 1, no skips/xfails/errors, and assertion failures only
   at the specified nodes. Missing/duplicate/unexpected cases, malformed JUnit,
   collection/setup/teardown errors, timeout, missing executable and non-assertion
   exceptions all fail the gate. Probe the verifier with those negative cases;
   a nonzero status alone never proves a useful red result. Green commands require
   exit 0 and nonempty expected collection. Preserve unmasked process exit codes
   separately from report parsing; a valid old report cannot cover a failed run.
6. Run current CI equivalents: ruff, pyright, install-agnostic check, and one full
   serial `python -m pytest -q --ignore=tests/live` with HZ_REQUIRE_TMUX=1 using the
   pinned environment. This deliberately replaces old `-n auto`: shared private
   tmux/process regressions and root-coordinated resource ownership favor serial
   evidence. No live backend invocation; retain normal explicitly justified skips.
   Capture exact command/environment, HEAD and clean state, duration, stdout/stderr,
   status and log hashes. If backgrounded, wait on that exact PID and require its
   actual status. Run full CI only in the controller's free broad-test slot.
7. Any failure is a hold with retained evidence. A suspected flaky run needs a
   documented diagnosis and a complete affected gate rerun; do not drop a test or
   present a partial rerun as the full gate. Freeze exact candidate for at most
   three cross-model code-review rounds before merge. Hosted CI remains required.

## Native admission and proposed next task

The controller should create a distinct scanner implementation task only after
checking that no equivalent task already exists. Link it to this completed
planning publication and a genuine approval blocker. The bounded existing
snapshot found no separately named implementation task; that is not universal
proof. Keep btq-18sfj planning-only until its real delivery/close gates are met.

The operator request must explicitly approve retained d3 behavior, this new
current-main landing order, accepted detection gaps/false positives, and the
replacement execution contract. Record a fresh authenticated decision with exact
artifact/source pins. Existing bswl5 metadata and historical r4 verdict cannot
be copied to produce new approval. If policy regards the bounded new review as
insufficient to admit the retained mechanics, escalate that exact conflict;
there is no automatic approval, retry loop or hidden exemption.

Implementation scope: scanner/redactor, required hook-marker adaptation, retained
regressions, current consumer compatibility tests and ordinary verification
evidence. Excludes plan5i merge, install-check fix btq-4lsqy, subsequent critical
chain tasks, live OpenShell/Podman/Marmot runs, service restarts, host changes and
deployment. PR publication/merge follows the controller's actual session authority
and review gates; this proposal grants none by itself.

After scanner delivery, btq-4lsqy must verify its install-check matcher remains
a semantic superset, with joint tests once both changes land. The requested
critical chain continues 4lsqy → b7oa3 → oaw8k → h6y9w → qdyj7 after the
controller verifies real task scopes and native edges. Plan5i branch integration
is a separate reviewed effort with an explicit current-main compatibility gate.
