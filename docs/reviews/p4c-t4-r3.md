Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: bd71fed32477cb2925547a360cefee3daf57d628 (range f2eb415f4619a3cfc74f20b59e58b16ea9465a14..bd71fed32477cb2925547a360cefee3daf57d628). Verdict: REVISE.

Note: code-review cycle 3 of 3; verifies the r2 fixes in bd71fed. The controller (team-lead) accepted the blocking finding as a documented residual, past the 3-cycle cap: rmdir removes only an empty directory, the race needs write access to store-owned directories, which could rmdir directly anyway, and Linux has no rmdir by fd. It is documented in the _remove and _remove_opened docstrings and the PR body.

- [BLOCKING] `src/heterodyne/harness/resources/store_core.py:1789` — The final identity check and `rmdir` remain separate operations. Replace the emptied root with a different empty directory after line 1787: `rmdir` deletes that unidentified replacement, and `remove` reports success and deletes the receipt. Reproduced in `/tmp`; the identified original survived elsewhere. Empty-only removal limits damage but still violates criterion (b). Recursive `_remove` has the same pathname removal boundary at line 1368; fd-relative `O_NOFOLLOW` traversal prevents symlink redirection but is not wholly race-free.

- [NON-BLOCKING] `src/heterodyne/harness/resources/store_core.py:2306` — An orphan tree without a naming receipt or intent now refuses `INSTALL_BROKEN`. This leaves operator cleanup necessary, but is acceptable fail-closed behavior.

The r2 metadata finding is resolved. The root-ctime exception retains root `(dev, ino)` and every descendant record value, so another install with a different root identity cannot match.

Validation: 292 offline harness tests passed; ruff, pyright and whitespace checks passed. Repository unchanged.

Verdict: REVISE