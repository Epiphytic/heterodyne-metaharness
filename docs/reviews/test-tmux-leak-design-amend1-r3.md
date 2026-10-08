# Design review r3: tmux leak design, Amendment 1 (btq-q1r4p)

Reviewed commit `dfe68e6`. Reviewer: gpt-6.1-sol (Codex, read-only, reasoning high). Author: claude-opus-5-5. Mode: cross-model.

The non-blocking points below were folded in in the next commit.

---

1. [NON-BLOCKING] **R2 #1: yes, resolved.** A2, lines 175–178 preserves the Harness’s guarded server and targets it during teardown. **Fix:** None.

2. [NON-BLOCKING] **R2 #2: yes, resolved.** A2, lines 147–155 restricts launchers, requires inherited fd preservation, and forbids launcher-side `LOCK_UN`. **Fix:** None.

3. [NON-BLOCKING] **R2 #3: yes, resolved.** A2, lines 185–220 explicitly identifies the three trusted paths, isolates specimens in a non-Python file, defines all four rule ids, and tests exceptions through `scan(relpath, source)`. These choices are sound. One mutation claim needs stronger coverage: scanning specimens as `tests/test_x.py` cannot detect widening the specimen exemption to `tests/data/`. **Fix:** Add a flagged case under `tests/data/untrusted.py`.

4. [NON-BLOCKING] **R2 #4: yes, resolved.** A3, lines 228 and 240–242 makes replacement detection terminal and repeats identity validation under final EX. **Fix:** None.

5. [NON-BLOCKING] **R2 #5: yes, resolved.** A3, lines 253–257 distinguishes filesystem closure from delayed processes still holding the unlinked inode. **Fix:** None.

6. [NON-BLOCKING] **R2 #6: yes, resolved.** A2, lines 141–145 and 198 provides a sound public, non-starting seam for the parent/final-component gap. Regression 8 still describes direct private-helper calls at lines 319–321, contradicting the scan rule. **Fix:** Rewrite that sequence as `finish = begin_lock_open(socket_path)` before rename, then `finish()` in the hook.

7. [NON-BLOCKING] **R2 #7: partially resolved.** A6, lines 343–352 removes the numeric bound and adds high-fd controls. However, directly probing the known fd normally returns EBADF when the child correctly closed it; line 349 permits EBADF only for the enumeration directory fd. This is a test-recipe inconsistency, not a cleanup serialization defect. **Fix:** Accept EBADF specifically for the direct known-fd absence check; retain explicit failure for unexpected enumeration and inspection errors.

8. [NON-BLOCKING] **R2 #8: yes, resolved.** A3, lines 239–251 retains the original fd and EX through unlink/rmdir, makes failure terminal, and forbids further removals. Regression 12 and the early-release/recovery mutants cover the corrected protocol. Its contention assertion needs an explicit setup detail: after unlink, reopening the pathname either fails or opens the replacement inode. **Fix:** Before unlink, independently open a second descriptor on the original inode; use that separate open file description in the hook. Do not use `dup()`.

9. [NON-BLOCKING] **R2 #9: yes, resolved.** A6, lines 328–336 requires device/inode identity, numeric fds, successful inspection, a platform mapping control, and a live server. The wrong-device mutant is appropriate. Clarify that `D` and `i` must belong to the same `f` record, with parser state reset at every `f`/`p` boundary. Valid unrelated records can omit fields, as documented in [Apple’s lsof manual](https://raw.githubusercontent.com/apple-oss-distributions/lsof/main/lsof/lsof.8). **Fix:** Treat those records as non-candidates rather than malformed output; preserve failures for malformed supplied values and a missing expected lock match.

APPROVE