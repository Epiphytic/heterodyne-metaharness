I ran the built-in `/code-review` skill on PR #157 with args `high 157`, model `claude-opus-5-5`, against frozen HEAD `f07838be05eea548d4a4e3cd1f5907fd37abc5e6`. It was read-only: no comments, pushes, edits, service changes or tests. The skill reported 9 findings and ran no verify pass at this effort level. I then read `deployment_admission_journal.py` lines 40–215 myself and confirmed findings 1, 2 and 4 against the code. The others are as the skill reported them.

## Findings (most severe first)

1. **A consumed admission can be rolled back and replayed** (`src/heterodyne/actions/deployment_admission_journal.py:137`, confirmed). The second `channel.check()` runs inside `with self._transaction()`, after the `INSERT`. If the parent dies or the ELF/lease check fails before the commit, `_transaction` rolls back the only record of a native consent that was already consumed. A later child replaying the same Consumed bytes and nonce memfd finds no conflict and is admitted as fresh authority. This breaks the rule that a lost child response must never erase a native consumption.

2. **`timeout=0` loses consumed admissions** (`deployment_admission_journal.py:54`, confirmed). Any other connection holding a lock, such as an overlapping child for another target or a reader during commit, makes `PRAGMA`, `CREATE` or `BEGIN IMMEDIATE` fail at once with SQLITE_BUSY. The consent is already consumed, so the child exits 2 with no row and no way to retry. A short busy timeout would avoid this.

3. **A failed `fail_unissued` locks the target permanently** (`deployment_admission_entry.py:205`). If `check()` fails, the database is busy or the rowcount is wrong after a successful admit, the row stays `ready` forever. The `admission_active_target` index then refuses every later admission for that target until a root administrator edits the database by hand.

4. **The schema is never checked** (`deployment_admission_journal.py:60`, confirmed). It uses `CREATE TABLE/INDEX IF NOT EXISTS` and never verifies the result. `_OutageJournal` does check its index SQL with `_index_sql()`. A pre-existing `admission.db` without `UNIQUE(use_key)`, or with a weaker or missing active-target index, is silently accepted.

5. **Window and profile refusals leave no trace** (`deployment_admission_journal.py:125`). The forward-window and profile-binding checks run after native consumption but before any row is written. If the clock is skewed, nothing is reserved, and once it is fixed the same bytes can be admitted as a first use. This contradicts the README's claim that the consumed packet is always durably recorded.

6. **The except clause is too narrow** (`deployment_admission_entry.py:208`). A `TypeError`, `KeyError` or `AttributeError` after `admitted=True` skips the `unknown` reply and the `owned-deployment-admission-held` marker. The process exits 1 with a traceback instead of the held exit code 2.

7. **Validation runs twice** (`deployment_admission_journal.py:120`). `admit` re-reads FD13, re-hashes and re-parses the profile, and decodes the packet again, all of which `_correlate` already did. The two copies of the logic could drift apart.

8. **The SQLite custody and transaction code is a copy** (`deployment_admission_journal.py:91`). `_transaction` and the `__init__` open/connect/recheck sequence repeat `_OutageJournal`'s with only the error strings renamed. The copies already differ (`O_NONBLOCK`, owner check, schema verification), and the PR's own `quality-delta.txt` flags this as gating duplication.

9. **The database is opened by path, not by descriptor** (`deployment_admission_journal.py:53`). Opening through `/proc/self/fd/N/admission.db` lets SQLite turn the descriptor-walked directory back into a text path for the database and its `-journal`. The inode recheck covers only `admission.db`, so the rollback journal's location isn't anchored to the walk.

## Notes from the skill
- It dropped one candidate: a `cancelled` row still holding the target lock matches how the outage journal treats targets, so it isn't a defect.
- No CLAUDE.md violations. There is no repo-level CLAUDE.md, and the global rules don't apply to this Python diff.
- **The PR's `quality-delta.txt` is out of date.** It was computed at `bddc4f3ad+dirty`, before the rebase, not at `f07838b`, and still reports three gating regressions.
- The hashes in `actual-native-qualification.json` match the current source and test files.

Within the scope you set (no effect runner required to merge), findings 1–4 are real security or correctness flaws and should be fixed before merge. Finding 1 is the most serious: it reopens the replay the slice exists to prevent.
