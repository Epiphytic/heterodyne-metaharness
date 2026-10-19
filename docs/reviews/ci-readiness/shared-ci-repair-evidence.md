# CI readiness and shutdown diagnostics: author evidence

Actual round14 MEDIUM review found no critical flaws and judged the seven-file patch good enough for a PoC. This satisfies the operator-authorized subsequent critical-review gate; it is not a general APPROVE or deployment authorization. Existing rounds1–13 and prior findings remain retained, with no ordinal reset.

Both PR128 and PR129 hosted test logs showed the same147 failure/error entries:140 descriptor-range ValueErrors, four unavailable historical Git-object failures, and three other failures/errors requiring their own disposition. The descriptor exceptions arose from select.select at descriptors beyond FD_SETSIZE. This shared pattern supports repairing those concrete sites; it does not establish that every failure or the process-exit hang is resolved.

The seven-file candidate contains:

- `.github/workflows/ci.yml` — SHA256 `49297c20a09241e3b435fa49fde3089d14b32f6e440dc05d843b9a4275f5697c`.
- `src/heterodyne/harness/fetch.py` — SHA256 `9c8bf2aa88c3e90446671b9ebf35b80c26c5eb25bbbc06e399291a1dcdebc4f6`.
- `tests/test_escapes.py` — SHA256 `37b00b14635334d37e4bb813cf59ccebf7429951725cad55a48d68226f955d1b`.
- `tests/test_sandbox_creates.py` — SHA256 `331369b23451807394fefb9c361e33c049b99400def080050c707737b40812d5`.
- `tests/tmux_guard.py` — SHA256 `d878aa42f2949fc266df0d8b5c78dc421f61452bc5243ffa0ef6bb6075c88ed7`.
- `tests/test_high_fd_waits.py` — SHA256 `c6a69a58947451a9f4eada7a0750d28a8d1db4fd92dc14c2833ef093bed7b659`.
- `scripts/pytest_shutdown_probe.py` — SHA256 `f516a67b6c8452481e6b476489138c711eb54437fb2bd52216c8f111b7125d66`.

Four readiness sites use the existing DefaultSelector context pattern while retaining read/EOF/deadline behavior: Fetcher._stream, tmux_guard._read_ack, the escape-test readiness helper, and sandbox-test child readiness. Test checkout fetch-depth0 supplies required historical source objects. Five new high-descriptor tests cover readable data, EOF and timeout behavior. Round13 follow-up adds the existing resource-limit skip pattern and explicit deterministic local test clocks; low-limit hosts are not falsely reported as executing high-descriptor coverage. Existing assertions were not relaxed.

The new test-process supervisor preserves actual pytest exit status, records session-finish and main-return phases, and captures all-thread stacks. Whole-process3600s and post-session60s deadlines return124 on timeout, never success. The workflow retains pytest-q/environment, uses a70-minute job bound and prints diagnostics in named console groups; it introduces no new action dependency or platform-specific GNUtimeout command.

Disposable controls against the adopted script returned0 for pass,1 for a real assertion failure, and124 for both blocked non-daemon thread shutdown and blocked unconfigure. Actual stacks distinguish the two stuck phases. The actual project-plugin smoke run passed7 tests and exited naturally0. The five high-descriptor tests separately passed normally and skipped under an explicitly reduced descriptor limit. Earlier53-test and later7-test batches overlap and are not summed as unique coverage. No full-suite or hosted CI clearance is claimed.

Configured Ruff0.16.9 passes. Full configured Pyright reports zero errors/warnings. An older Ruff0.15.2 invocation reported12F811 diagnostics in an unchanged historical fixture file; this is retained rather than represented as a current configured failure. Earlier import-path typing errors are retained as environment artifacts. Diff/install-agnostic checks pass. Formatting debt remains disclosed, including the new test; final quality measurement reports seven gating findings and lower signals, all unwaived. Prior reviewer findings are not silently cleared.

The hosted process remained alive after its test summary. No hosted exit-thread stack was captured, and the historical local slow-test stack does not establish the hosted post-summary cause. Neither polling repairs nor diagnostic instrumentation are claimed to fix that shutdown owner. The next hosted run may remain a genuine failure; the diagnostic must preserve it.

Adoption freeze SHA256: `fbde1fa2ebb0ade9b1e7187dddc2d36d62400413e43cf797437ccb9c74ee276c`.
Original unmodified prototype qualification freeze SHA256: `d9eac8e6ffbbba428e2aae5917f6b842d9223c35a31ccfcde4fba1d64dcbaefa`.
Original six-file author freeze SHA256: `a25f14258dc93578f9fc6997975d61afd505bc69d724a531c0979ff3285c7936`.

No deployment, queue/native operation, approval disposition, successful hosted run or merge authority is inferred from these checks.

Actual reviewer: Claude Opus using built-in `Skill(code-review)` at MEDIUM. Terminal exit0; seven frozen files unchanged. Reviewer log SHA256 `70ad30cb704a26f80f972e072f6412d043b862619e6ab8a9a4eeba0ec3f15f62`; stream SHA256 `7446d339797f61c04568fb44d0dc06c673c26e1cb5a4ccd118356b8d117f0fc2`. The skill also ran five tests and configured static checks and created an ignored environment despite the read-only/no-tests request; these effects are disclosed. Nine noncritical findings stay open, including signal timing, shutdown budget, child cleanup/cancellation, import path, macOS high-FD skips, usage and duplicate waits/dumps. The reviewer refuted its unused-history finding. No prior finding is silently cleared.

The exact patch also applies to PR128: all five original changed files are byte-identical between bases and both added files were absent. All seven adopted hashes match after application. A targeted check on PR128 passes7 tests with the correct source import path; an earlier wrong-environment collection attempt is retained as a local setup failure. Full new hosted checks remain required.
