I ran the second full HIGH review of PR 157 using the built-in `/code-review` skill with args `high 157`. It reviewed frozen HEAD `fd84621` on model `claude-opus-5-5`. **Verdict: not clear.** Two issues are critical security/correctness defects inside the finite no-effect admission component, and three more are correctness defects. Nothing was posted, pushed or edited, and the worktree is still clean at `fd84621`. One caveat: for finding 1, the review says it confirmed the bug by running a small reproduction with a hidden trigger. That falls outside "no tests." It left no files in the worktree, but I can't see what scratch database it used.

Below are the findings exactly as the skill reported them, most severe first.

**Critical**

1. **The schema check can be bypassed** (`src/heterodyne/actions/deployment_admission_journal.py:85`). The check excludes objects with `name NOT LIKE 'sqlite_%'`, but in `LIKE` the `_` matches any single character. So a trigger named something like `sqlitextrg` is left out of the comparison. If `admission.db` already has `CREATE TRIGGER sqlitextrg AFTER INSERT ON admissions BEGIN DELETE FROM admissions; END`, the journal opens normally, every admitted row is deleted right after insert, and the same consumed packet or use_key can be admitted again. The review reproduced the duplicate insert. Fix: `substr(name,1,7) != 'sqlite_'` or `LIKE 'sqlite\_%' ESCAPE '\'`.
2. **The native nonce A is no longer tied to anything** (`src/heterodyne/actions/deployment_admission_binding.py:150`). The check that bound the Consumed frame's nonce to the sealed nonce was replaced with "64-char hex and different from B". So a fresh handoff (nonce B) paired with a Consumed frame whose nonce is `'0'*64` now passes correlation. A is also not stored or made unique: the journal's `nonce` column holds `packet.release.nonce`. The test that used `'0'*64` was rewritten to test only A==B, so this case is no longer covered. The README's "unique … nonce" claim doesn't hold for the native nonce.

**Correctness**

3. **A late `OSError` skips settlement** (`deployment_admission_journal.py:165`). After commit, only `InvalidAction` from `channel.check()` triggers `fail_unissued`. A bare `OSError` (EBADF or EIO from `fstat` or poll) leaves the row at `ready` permanently. `admission_active_target` then refuses every later admission for that target until someone edits the database by hand.
4. **Failed settlement leaves the target locked** (`deployment_admission_journal.py:167`). If `fail_unissued` itself fails (still busy after the 5 s timeout, or an I/O error), the row stays `ready`. The entry doesn't retry because `admitted` was never set, and no code path recovers it.
5. **The deadline uses a stale time** (`deployment_admission_journal.py:145`). `in_window` is read from the clock before `check()` and before `BEGIN IMMEDIATE`, which can wait up to 5 s. A packet whose deadline passes during that wait is still inserted as `ready` and reported as successfully admitted.

**Test coverage**

6. **One test mode never reaches the journal** (`tests/test_deployment_admission_entry.py:33`). In `late_parent_loss` mode, replays 2 and 3 are refused in `_ParentChannel.__init__` before `admit` runs. So "two new-process replays are refused" isn't actually shown for that mode, and a broken uniqueness or settlement rule would still pass.

**Lower severity / cleanup**

7. **A rejected database gets modified first** (`deployment_admission_journal.py:77`). `executescript(_SCHEMA)` runs before the schema comparison, so a weakened or foreign database has missing tables or indexes created (and fsynced) before it's refused.
8. **`admit` repeats `_correlate`'s work** (`:140`). It reads FD13 again, re-hashes it, and decodes the packet a second time. Returning `(consumed, producer, packet)` from `_correlate` would remove both the extra work and the risk of the two validation copies drifting apart, which R1 finding 7 already noted.
9. **The SQLite setup is copied from the outage journal** (`:107`). `_transaction` and the open/custody/PRAGMA sequence duplicate `_OutageJournal` in `deployment_outage_journal.py`, and the copies already differ (timeout, owner check, schema check). This matches the gating duplication debt you've already acknowledged.
10. **`fail_unissued` takes a channel it doesn't use** (`:172`). The `_ParentChannel` argument is only type-checked, yet any future recovery path that has no live parent couldn't call the method.

Findings 1 and 2 go directly against R1's stated goals of "enforce complete source schema" and nonce-domain separation. Findings 3–5 are new gaps in the post-commit settlement path that the R1 repairs introduced. None of these depend on installer, effect or deployment-success scope.
