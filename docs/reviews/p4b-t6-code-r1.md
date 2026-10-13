Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 6b2b1a80163812842aab13cc630242ec92860d58 (range 543c9e3b5af9b4597c78caa6400e35e0370ce465..6b2b1a80163812842aab13cc630242ec92860d58). Verdict: REVISE.

Note: the blocking finding was fixed in 74df7a0 without an interface change or a wider lock scope; see p4b-t6-code-r2.md.

[BLOCKING] `src/heterodyne/sandbox/runtime.py:487` — The lock is released before `_start` resolves the login paths and creates the bind (`:532`). Repointing a named account’s login between these steps lets it bind another account’s credentials without `KEY_CHANGED`. Replacing the file with a shorter-lived token similarly bypasses freshness validation; the deadline still uses the old expiry.

Confirmed with an offline `/tmp` test: immediately after `_pinned` returned, I replaced the named account’s fake `auth.json` with a symlink to the default account’s fake login. Launch succeeded, bound the default file, and the current key differed from the pinned key. Existing tests repoint before the check and miss this window.

Pin the checked file identity through mounting, and add a regression covering replacement after validation. Task 6 explicitly permits the short lock scope, so this also exposes a design gap.

The scheduler fixture change matches production’s shared `s.accounts`; it does not hide a production mismatch. The other deviations are acceptable. Capabilities remain empty, and no credential logging was introduced.

Validation: 394 tests passed; Ruff and Pyright passed. Repository files were left unchanged.

Verdict: REVISE