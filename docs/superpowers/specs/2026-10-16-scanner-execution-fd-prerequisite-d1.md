# Scanner execution amendment: exact hostread test prerequisite d1

Status: proposed design only, planning task btq-1a845. No implementation,
new review verdict or human approval is asserted. Exact provenance and failed-run
hashes are in [the input manifest](../../reviews/scanner-boundary/execution-fd-prerequisite-d1-inputs.json).

## Decision and scope

Admit the existing test-only commit
`9a14b3b2664e4ea0c5452c9eea013bf37aab658f` as an explicit prerequisite to
scanner implementation btq-1wu2i on source
`07637745fe75dc614109a0b8ba72d3489f262a97`. Preserve the retained scanner d3
contract, historical scanner/plan review counters, original genuine btq-pxtig
approval and its pins. This amendment changes only one test module and the
execution admission/collection accounting described below. It does not authorize
scanner implementation until its own genuine approval is linked natively.

This is a narrow subset of the historical hostread-flake discussion, prompted by
a repeatable execution blocker. It is distinct from btq-gtetp, which concerns
closing SQLite connections leaked by other tests. Do not reclaim, duplicate,
close or claim completion of that broader task here. Do not import the larger
hostread-flake design's host-wide scan/subreaper, channel, daemon or other fixes.
No sandbox recovery, production FD ownership or scanner policy changes.

The new design has its own maximum three review rounds. This is not another
review of the exhausted scanner regex/design or old execution-plan cycles.
The later scanner implementation code review must examine the complete final
diff, including this prerequisite; historical review is evidence, not a waiver.

## Concrete blocker and evidence

The original B1-only partial-state gate collected 3,372 nodes, including all four
expected scanner failures. It finished with those four plus
`tests/test_sandbox_hostread.py::test_no_readable_worker_settles` failing because
its global FD count fell from 43 to 25. The full unchanged rerun again collected
the identical 3,372 nodes and finished with the same five failures; the FD count
fell from 40 to 25. Both actual process statuses were 1. All home, child-alive,
no-kill and deadline observations preceding the count assertion remained correct.
These runs failed their strict verifier and remain failures, never retrospectively
accepted or edited. The original four expected scanner identities stay fixed.

An isolated complete hostread module passed all 91 current cases with original
assertions and recovery untouched. Private instrumentation recorded equal counts
at the failing node during that isolated run. A private six-connection SQLite WAL
reproduction, held only by cycles, lost exactly 18 FDs on explicit cyclic GC.
The historical diagnosis describes the same mechanism. This supports unrelated
connection collection as the cause; no FD-identity trace from either failing
full run exists, so the origin of every closed descriptor is inferred, not proven.
Neither an isolated pass nor a plausible explanation satisfies the full gate.

The scanner is stopped at B1-only state. B2 is untouched. Do not continue retrying
the old count-based gate or disable collection, drop/reorder the node, force a
pre-test GC as a workaround, change the failure oracle, or treat another failure
as an expected scanner result.

## Exact overlay and historical evidence

The fix's only changed path is tests/test_sandbox_hostread.py. Its parent is
2c0c5b5; its signature verifies G. The original and exact 076 test blobs are both
`306b1fcc632be1eaad921b532bd93e47428c8bda`. The fixed blob is
`53b1af47be9a4d71d7bd80fafe3719d7aa62b375`. Their complete SHA-256 hashes and byte
counts are pinned in the manifest. Production src/heterodyne/sandbox/hostread.py
is identical in all three commits, blob
`2864c87cef46443a039422eeb2f0f1d0fe85f082`. The exact historical patch passes
`git apply --check` on clean 076. No source or test patch was applied to prove it.
Do not merge unrelated historical branch ancestry.

The exact patch changes the process-wide count helper to a frozen set of
(fd number, file device, inode) identities and checks that the post-recovery set
is a subset of the baseline. Closing unrelated old descriptors is permitted;
new identities still fail. Existing home preservation/removal, child survival,
no-kill, deadline and diagnostic assertions remain. The added regression holds
a SQLite connection in a cycle, collects it during recovery, checks that the
count actually falls, then requires a newly opened pidfd to be detected.

The retained brief labels the historical cross-model code review **cycle 1**;
the actual report says **APPROVE**, Python 3.12.3, **92 serial passes**, **92 passes
with -n 8**, plus one instrumented regression pass. These are historical results,
not fresh current-base runs. Neither retained brief nor report identifies the
reviewer's model. Do not label it Opus or infer its identity from the commit's
Claude Opus co-author line. The provenance manifest records this evidence limit.
The broader hostread-flake design's reviews do not substitute for this amendment's
fresh design review and human decision.

## Accepted limits and safety argument

Identity comparison checks for newly retained descriptors, not exact conservation
of the runner's global count. This is the intended leak property when unrelated
connections may close. It does not prove that recovery never incorrectly closes
an existing unrelated descriptor; old count equality could also hide a close
balanced by an open. All existing recovery behavior assertions remain required.

(fd, device, inode) is not an open-file-description identity. Closing and reopening
the same inode at the same FD can escape detection. Some anonymous inode types
share identities. Historical pidfs probes distinguish different processes on this
host's kernel; this is not a guarantee for older kernels. Before Linux 6.9,
pidfds can share an inode. The new regression's negative check rejects a new
pidfd identity, but does not require reuse of the closed connection's FD number.
Its count-decrease assertion makes collection nonvacuous. These are the retained
review's disclosed limitations, not claims of a universal FD leak detector.

No production behavior or security boundary is relaxed. Do not extend this
identity helper to production code, other modules, or unrelated failures. Any
requested broader correction needs separate concrete scope and approval.

## Re-admission and execution requirements

1. Obtain independent approval of this exact signed design and manifest, publish
   direct GitHub artifact links, then obtain an authenticated human decision.
   Add a genuine blocking approval dependency to btq-1wu2i while preserving the
   existing btq-pxtig dependency, digest and history. Record this as an explicit
   execution amendment; never overwrite old approved metadata or failed evidence.
   Root alone operates the native queue and approvals.
2. Recheck exact canonical 076 source pins, unchanged retained scanner contract,
   B1/B2 extraction hashes, current task receipt and all existing native gates.
   The existing scanner worktree already contains the declared planning commits
   and new test commit 1995977b7e0fab4d6caa8ef5d50fea2d80886fa4, with only B1 source
   edits and an untracked test-environment alias. Verify those actual bytes and
   hashes before applying this single-path prerequisite. Do not rewrite receipt
   identity, rebind the task, silently rebase on newer main, or alter old artifacts.
3. Import only the exact 9a14b3b single-path patch, preserving current guard/create
   work and scanner edits. Require the resulting test blob to equal the fixed
   blob above, and production hostread to remain byte-identical to 076. Use a
   signed commit for the prerequisite without committing a scanner-only partial
   landing. Final scanner/redactor source changes still land atomically.
4. Use the existing locked private Python 3.12 environment and offline fake/private
   fixtures. First require the amended hostread module to collect and pass **92**
   cases serially, including its new regression. Record actual collection,
   interpreter/origin, status, JUnit, full logs, duration and hashes. Do not run
   historical -n 8 as a new shared-resource gate. Failure holds for disposition.
5. Renew the unchanged original-source red baselines in disposable copied source,
   never by reverting the real task checkout: restore the three B1-touched source
   files from exact 076 there, retain the new scanner/create tests and amended
   hostread fixture, and prove imported code paths plus restored hashes. Require
   exact t1 **53 failures/35 passes of 88**, and create baseline **four declared
   embedded-field assertion failures/two control passes of six**. Keep the exact
   existing expected node data. No source contract or expected failures change.
6. In the real B1-only task checkout, collect the same named 81 modules as before.
   The new collection must be the exact prior 3,372-node ordered list plus only
   `tests/test_sandbox_hostread.py::test_a_leaked_fd_that_another_test_left_closing_meanwhile_is_no_leak`
   at its normal module location: **3,373 total**. Verify raw/masked uniqueness,
   exact set delta and preserved order of all old nodes before running. Require
   precisely the original four scanner-related assertion failures and **3,369
   passes**, no skips/errors/xfails, actual exit 1. All 88 Appendix A tests, six
   create tests, 92 hostread tests and 19 metadata compatibility cases must pass.
   Use the retained strict JUnit/status/identity verifier and its negative probes.
   Do not retroactively rename either original failed run as successful.
7. Run the retained disposable widened-scanner/ORIGINAL-redactor overlap probe:
   exact **four declared failures/three passes of seven**, correct disposable
   imports and original-redactor hash. Only after renewed gates pass apply the
   four approved B2 fixture changes, preserving unrelated guard fixture hunks and
   pinning both resulting test files' hashes. Require all **3,373** targeted
   cases green, with exact collection and no skips/errors/xfails.
8. Retain repository Ruff, Pyright, install-agnostic and own-text gates. Freeze a
   signed clean complete candidate, then run one serial offline full suite with
   `timeout 3600 python -m pytest -q --ignore=tests/live`, HZ_REQUIRE_TMUX=1,
   locked environment and the controller's sole broad slot. Pin fresh complete
   collection; retain justified normal skips, actual status and log hashes.
   Preserve existing create recovery/concurrency and guard coverage. Require
   hosted secrets and unfiltered CI tests under existing live opt-in policy.
   Any further unexpected failure is a hold with concrete diagnosis and complete
   affected-gate rerun, not an automatic baseline update or retry lottery.
9. Root runs the scanner change's genuine cross-model code review, at most three
   rounds, on the complete final diff including this exact test prerequisite.
   Preserve all historical counters. Merge follows actual user authority and
   passing gates. This amendment authorizes no live backend, host/service change,
   deployment, dependency upgrade or unrelated cleanup.

## Result and exclusions

Success is a repeatable honest scanner verification path with the same detection
and redaction contract, strengthened attribution of test FD leaks, and complete
fresh red/green evidence. It does not fix leaked SQLite connections themselves,
make universal descriptor-identity guarantees, or mark historical reviews or
failed runs as current approval or passing evidence. All scanner retained
residuals and deferred plan5i consumer obligations remain unchanged.
