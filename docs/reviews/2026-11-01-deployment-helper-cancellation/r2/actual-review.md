I ran the second full HIGH review of PR #155 at the frozen head and found no correctness or security flaw, so the source PoC can merge. The guard is still dormant.

**How it was run:** the built-in `/code-review` skill with args `high 155` (forked execution, effort level high), driven by me (Opus 5.5, `claude-opus-5-5`). I confirmed HEAD is `233729ce313c4cfa93b7366f024d70d4062aa9e8`. It was read-only: no comments, pushes, edits, service changes or test runs. The 83-test and Ruff results are the ones you supplied and the bundle records; I did not re-run them. I also re-read `_issue`, `run()`, the namespace probe and the review bundle myself to check the skill's findings.

**What holds at 233729c:**
- `_issue` (`deployment_root_helper.py:270`) runs only after `run()` takes `LOCK_EX` on `target.lock`. It checks for `.issued` before `.cancelled`, so an existing issued UNKNOWN wins over a later cancellation. The `O_EXCL` create is still the final one-use gate.
- A stat error other than "file not found" fails closed with `root-helper-issue-check`.
- Nothing at runtime deletes a cancellation marker.
- The tests are causal: removing either check fails the probe. They cover:
  - regular, directory and dangling-symlink markers;
  - a cancellation that arrives while the helper holds the lock;
  - issued plus cancelled giving UNKNOWN;
  - a cancellation for another packet not blocking this one.
- The r1 finding (wrong precedence and untyped errors) is addressed.

**Out of scope, as you said:** the skill's top finding was that no cancellation publisher exists yet, so the shared-lock rule is only a comment. That is the future root producer's requirement (same flock, never delete), which the README already states. It does not block this PR.

**Non-blocking items, each with a concrete fix:**
1. **Lock is only assumed.** `_issue` relies on the caller holding the target lock but never checks. Fix: pass the held lock fd in, so no other caller can skip the lock.
2. **Cancelled packets are refused late.** One only fails at the final check, after hashing, taking the target lock and writing the installer copy. It holds the lock that whole time, so a concurrent run for another packet gets `target-busy`. That fails closed, so it isn't a correctness problem. Fix: add a lock-free early check in `_retained` and keep the locked final check. This is the same efficiency point r1 raised.
3. **Missing test.** Nothing tests the `root-helper-issue-check` branch (lines 278–279). Fix: patch `os.stat` to raise `EIO` and assert the typed refusal with no `.issued`.
4. **Error codes are hard to grep.** They are built as `"root-helper-" + reason`, so `root-helper-cancelled` appears only in tests, and the already-issued code is emitted from two places. Fix: two explicit checks that raise literal codes.
5. **Fragile test trigger.** The late-cancellation test fires on the second `_sudo_parent` call (`checks == 2`, test line 130). Fix: hook the second `_retained` call instead, so it doesn't depend on how many times `_sudo_parent` is called.
6. **Unreset fixture.** The other-packet marker `b*64.cancelled` (test line 141) is never reset, unlike every other labelled reset.
7. **Bundle wording.** The repair quality delta's two gating rows are short-horizon churn on `_RootInstaller` and `run` (exit 2). They come from repeated edits in this PR, not code that got worse, but the README's acknowledgement doesn't name them. Separately, the line numbers in `r1/actual-review.md` refer to `290cc61`, and the bundle should say so.

None of these change the merge decision. Items 1–3 are the ones worth doing before the real producer or installer qualification work builds on this code. Genuine producer and installed qualification remain separate implementation work.
