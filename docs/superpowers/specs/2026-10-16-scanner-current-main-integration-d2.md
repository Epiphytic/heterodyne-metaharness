# Scanner: current-main integration and execution addendum d2

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
The controller must record that deferred obligation natively on the actual
plan5i integration task before scanner admission; if no such task exists, create
the follow-up without pretending plan5i was integrated. Those absent consumers
are deferred integration coverage, never reported as
passing or silently substituted by an empty test selection.

## Current-main integration invariants

1. Land scanner widening and all-original-text span redaction atomically. Retain
   the existing hook/marker conditional rules; inspect actual code before deciding
   the stored-marker version step. At exact 3aa, no done:v1/event-scrub code exists,
   so no bump applies. No invented inbox/event scrub symbols.
2. Preserve guard fixture changes in test_admind_approvals.py and
   test_admind_r13_redact.py. Preserve provenance quarantine, fresh-policy gates,
   observational membership behavior and explicit system ownership.
3. Current sandbox/creates.py calls sensitive_kind on escalation text, then uses
   fixed unknown-create or close-out wording if sensitive. Wider matching must
   not suppress escalation or close-out, expose secret-bearing fields, alter
   submission/settlement/gone evidence, or grant settlement from observation.
   This is alert-text filtering, not sandbox-name validation. Add fake/private
   ledger regressions for embedded newly detected tokens in workstream/bead
   fields and ordinary fields, with safe fixed fallback rendering through admind
   alerts. Require newly detected embedded-token cases red on unchanged 3aa, then
   green after the combined patch. Close-out text contains no ws/bead fields:
   after a fallback unknown-create alert, require the normal close-out with ID
   create-<name>-settled before cleanup. Do not monkeypatch a sensitive close
   text. Check no original token escapes and lifecycle evidence remains correct.
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
an implicitly reviewed authority. Its expected-node DATA is adopted explicitly
below; that does not approve its checker code or old r5 shell mechanics. No new
production test-runner framework is
needed. Use explicit commands and ordinary pytest JUnit reports with the checks
below; any small verifier belongs in the implementation's independently reviewed
test evidence and must itself be probed against malformed/error reports.

1. Admission pins the actual implementation parent, the new approved document
   digest, frozen d3/r5 input hashes and source manifest. Require exact 3aa base
   plus only these planning documents: cherry-pick their signed docs commits onto
   3aa and verify the complete diff contains only the declared docs paths. If any
   pinned source or consumer set changes, hold for concrete delta review and
   re-admission before relying on these baselines. Use a receipt-bound isolated
   implementation worktree. Do not overwrite historical scratch checkouts.
2. Use a task-specific locked dependency environment with Python 3.12 and tmux;
   record interpreter version/realpath and prove imported heterodyne comes from
   that worktree in every intermediate state, including the disposable partial-JWT
   state. Put the Hypothesis database in task-private scratch, or report tracked
   cleanliness separately from generated files. Keep it stable for the run.
   Targeted and full runs are serial. Tests use fake backends/private
   processes only. Never enable live-test flags, inspect live credentials, or use
   a production queue/database. Setup/import failures are fatal, not expected red.
3. Extract Appendix A bytes from frozen r5 and verify its manifest hash before
   using its 88 tests. On unchanged exact source require t1: 53 failures and 35
   passes, with exactly the node set in
   [expected red data](../../reviews/scanner-boundary/expected-red-results.json).
   These expected data are copied from pinned Appendix C (hash in the historical
   manifest); checker code is not adopted. Appendix A dependencies are unchanged
   from its measured base. A mismatch holds for reviewer/operator disposition;
   an author's explanation alone cannot authorize continuation or new baselines.
   Keep full identities private; publication uses collision-checked masked IDs.
4. Add new test module test_sandbox_scanner_integration.py before source edits.
   The four declared embedded-field cases use runtime-built distinctive vendor
   tokens glued directly after a letter or digit in ws or bead. On unchanged 3aa
   require exactly those four assertion failures (token appears in file/rendered
   alert; safe fallback absent) and two ordinary-ID/lifecycle control passes.
   Separator-prefixed tokens do not satisfy the red requirement. Each embedded
   case also checks fallback alert ID, safe admind rendering, unchanged ledger
   evidence and subsequent normal settled close-out before cleanup. The two
   controls cover ordinary fields/IDs and real close-out ordering without needing
   fallback; both must pass before and after. Do not emit synthetic token bodies
   as test IDs or literals: construct them at runtime, as Appendix A does.
5. Preserve all three historical state probes, using the exact expected data:
   - t1 above: original source, 53 of 88 failures.
   - t2s2: apply Appendix B1 source changes, not B2 fixture changes. Run redaction,
     admind, units, config and scanner consumers plus new create tests and existing
     sandbox create/reaper/runtime tests. Exactly the four existing consumer nodes
     in expected data fail. All 88 Appendix A and six new create tests pass here.
     Record the exact complete collected set and total before the run; total must
     match it, not the old branch's count. Do not drop any expected node or silently
     select absent future consumer modules. Only then apply the four B2 changes,
     preserving unrelated current guard fixture hunks; require all targets green.
   - t2s5: in disposable source with widened scanner and ORIGINAL redactor, run
     the seven Appendix A overlap selections. Exactly the four declared nodes
     fail, three pass. Re-prove import origin. Never commit/merge this state.
   Finish with scanner and span-redactor changes atomic and all 88 Appendix A,
   six new create tests, all targeted hook/redactor/config/alert consumers green.
   No historical full-suite totals are asserted for this changed base.
   For every expected-red run require completed collection, exact declared node
   identities and totals, exit 1, no skips/xfails/errors, and failure messages
   beginning with pytest's assertion forms `assert `, `AssertionError`, or `Failed: `
   only. A pytest.raises DID NOT RAISE is an accepted Failed outcome. Missing,
   duplicate or unexpected cases, malformed JUnit, collection/setup/teardown
   errors, timeout, missing executable and other exception failures fail the gate.
   Probe any verifier against all these negative cases. A nonzero status alone
   never proves useful red. Green requires exit 0 and nonempty expected collection.
   Use a fresh report path per invocation and preserve unmasked process status;
   stale valid reports cannot cover failed runs. Never change assertions to learn
   arbitrary failures as expected. Check B1/B2 diff and extraction hashes first.
6. Run current CI equivalents: ruff, pyright, install-agnostic check, and one full
   serial `timeout 3600 python -m pytest -q --ignore=tests/live` with
   HZ_REQUIRE_TMUX=1 using the
   pinned environment. This deliberately replaces old `-n auto`: shared private
   tmux/process regressions and root-coordinated resource ownership favor serial
   evidence. Timeout is failure. No live backend invocation; retain normal explicitly
   justified skips. Require the hosted secrets job as well as tests; build new
   synthetic token fixtures at runtime so secret scanning remains enabled.
   Capture exact command/environment, HEAD and clean state, duration, stdout/stderr,
   status and log hashes. If backgrounded, wait on that exact PID and require its
   actual status. Run full CI only in the controller's free broad-test slot.
7. Keep fresh JUnit, collection, status and log files in controller-selected
   task-private handoff storage, retained through merge and project handoff (no
   automatic expiry). Publish hashes and summaries, not sensitive failure bodies.
   Any failure is a hold with retained evidence. A suspected flaky run needs a
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
