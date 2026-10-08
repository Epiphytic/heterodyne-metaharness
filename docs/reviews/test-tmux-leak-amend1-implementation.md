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
