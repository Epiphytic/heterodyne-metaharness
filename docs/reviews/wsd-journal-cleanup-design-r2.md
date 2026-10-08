# Codex review r2: wsd journal cleanup errors design (btq-ekktm)

Reviewed draft r2 of docs/superpowers/specs/2026-10-08-wsd-journal-cleanup-errors-design.md (then at /tmp/design-ekktm.md). Home paths are scrubbed.

1. **[BLOCKING]** [/tmp/design-ekktm.md:67](/tmp/design-ekktm.md:67) — R1 finding 1 is partially resolved: close forwarding and attempt counting are covered, but `db: sqlite3.Connection | _FailOn` raises `NameError` during test collection because the module has no postponed annotations. Reproduced on Python 3.12.3. **Fix:** Quote the entire annotation as `"sqlite3.Connection | _FailOn"` or add `from __future__ import annotations`.

2. **[NON-BLOCKING]** [/tmp/design-ekktm.md:81](/tmp/design-ekktm.md:81) — R1 finding 2 resolved: injecting SQLITE_BUSY at COMMIT exercises `_busy`’s note copying. **Fix:** None.

3. **[NON-BLOCKING]** [/tmp/design-ekktm.md:82](/tmp/design-ekktm.md:82) — R1 finding 3 resolved: the bare re-raise preserves E’s chain, and test 4 checks it explicitly. The proposed exception path passed an executable check. **Fix:** None.

4. **[NON-BLOCKING]** [/tmp/design-ekktm.md:51](/tmp/design-ekktm.md:51) — R1 finding 4 resolved: close retains serialization, and test 9 checks that queued workers refuse access after poisoning. **Fix:** None.

5. **[NON-BLOCKING]** [/tmp/design-ekktm.md:49](/tmp/design-ekktm.md:49) — R1 finding 5 resolved: public-operation scope, private-helper assumptions, and poison persistence after close are explicit. **Fix:** None.

6. **[NON-BLOCKING]** [/tmp/design-ekktm.md:86](/tmp/design-ekktm.md:86) — R1 finding 6 resolved in the design: the SQLite test arms after writing, releases the second connection before reopening, and permits skipping only after a successful rollback. **Fix:** None.

REVISE