# Implementation record: tmux leak design, Amendment 1 (btq-q1r4p, btq-u0aed)

The implementation of Amendment 1 (approved as ask 3kaf) on `tmux-leak-fix`. This record is input to code
review r5, which covers the branch against main. The spec file and its safety argument are unchanged.

## Documented deviation from the approved text

**Regression 3(b), "a launcher released after the final unlink starts nothing".** A2 says the guarded
command then exits non-zero. tmux 3.4 does not: `tmux -S <missing dir>/x new-session` prints
`error creating <path> (No such file or directory)` and exits 0. So `Tmux.new_session` returns normally.

The regression therefore runs its child under `LC_ALL=C` and accepts either outcome:

- `new_session` raises `TmuxError` (a tmux that exits non-zero on the failed bind), or
- the guarded `_exec` result exits 0 with `No such file or directory` (or the ENOENT strerror) on stderr.

Any other result fails the test. The mandatory checks hold in both cases: no server runs, no socket
exists, and the run directory is gone. Safety does not depend on the exit status, because the socket's
directory is already gone, so no server can bind.

That production `Tmux.new_session` reports success when tmux exits 0 after failing is outside this
task. It is tracked in a separate bead.

## Additions after code review r5

**Top-level `reason` in summary.json (finding 2).** The key is present exactly when `closed` is false.
In order of precedence it is the fault (`launch lock missing` or `launch lock replaced`), then
`appeared after final unlink`, then `final rmdir failed: <errno name>` (added after code review r6,
finding 3: the failure is recorded even when the inventory is empty), then `launch lock held`, then
`deadline passed`. Closed summaries are
unchanged. The two exact-equality tests of `closed: false` summaries were updated on purpose to expect
`deadline passed`: regression 1, and test 4 lock-held.

**The lsof branch of `held_fds` (finding 1).** The platform check is now `has_proc_fds()`. A test
forces the lsof branch by mocking it and `subprocess.run`, so it runs on Linux. The wrong-device record
must be rejected, and the same record on the lock's own device must match.
