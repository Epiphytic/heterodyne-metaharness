# btq-ekktm: wsd Journal.transaction() cleanup keeps the primary error and poisons on a failed rollback (r2)

Base: main b5a250d, `src/heterodyne/wsd/journal.py` (`Journal.transaction`, `_locked`, `_busy`, `close`).

**Facts checked on the project venv (Python 3.12.3, SQLite 3.45.1):**
- A ROLLBACK interrupted by a progress handler raises `OperationalError` (SQLITE_INTERRUPT). After it, `in_transaction` stays True and the write lock is still held: another writer gets SQLITE_BUSY.
- `close()` discards the open transaction.
- `in_transaction` on a closed connection raises `ProgrammingError`.

**r2 changes:**
- the bare-`raise` structure and a test for E's chain (finding 3);
- `close()` stays serialised (4);
- scope wording (5);
- the `_FailOn` changes (1);
- the `_busy` note test moved to COMMIT (2);
- test 5 hardened (6);
- a queued-worker test (4).

## Behaviour
This only changes what happens when E leaves the **outermost** block after BEGIN IMMEDIATE. E is the body's error, or COMMIT's if the body succeeded.

```python
except BaseException as exc:          # exc is E
    failed: BaseException | None = None
    try:
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")
    except BaseException as c:        # C: reading in_transaction, or the ROLLBACK
        self._broken = c
        if not isinstance(c, Exception):
            raise                     # KeyboardInterrupt/SystemExit win; Python sets C.__context__ = E
        failed = c
    if failed is not None:            # C's handler has exited
        exc.add_note(f"journal cleanup failed ({type(failed).__name__}: {failed}); "
                     "the journal is unusable; open a new Journal")
    raise                             # bare: E itself; its __cause__, __context__, __suppress_context__ untouched
finally:
    self._depth = 0
```

- When the ROLLBACK succeeds, or SQLite already rolled back itself (SQLITE_FULL), behaviour is unchanged: no note, no poison.
- C never becomes E's context; it stays on the journal as `_broken`.
- `_busy()` converts a transient E into `JournalBusy(name) from None`. It now copies E's `__notes__` onto that JournalBusy, so the cleanup note survives a SQLITE_BUSY at COMMIT.

## Poisoned state
New `class JournalUnusable(Exception)` with this docstring: "A transaction's cleanup failed, so the connection may still hold an open write transaction and SQLite's write lock. The file is not suspect. Every public operation except close() raises this, before and after close; open a new Journal (restart wsd)."

- **Error:** `JournalUnusable("the journal is unusable: a transaction's cleanup failed (<type(C).__name__>); open a new Journal") from C`.
- **Scope:** every public Journal operation except `close()`.
  - The check sits in the `_locked` wrapper **after** it takes `self.lock`, and at the top of `transaction()` inside the lock, before the `_depth` branch. A thread queued on the lock therefore sees it.
  - `close()` keeps `self.lock` (and `_busy`) through a lock-only variant of the decorator, so it stays serialised but skips the check. It closes the connection, which discards the open transaction and releases SQLite's lock.
  - The instance stays poisoned after `close()`; recovery is a new `Journal`.
- **Not covered:**
  - The private helpers `_op` and `_put` take no lock and do no check. They assume their caller holds `self.lock`, and every current caller is a `_locked` public method or runs inside `transaction()`, so they are only reached after the check. That assumption goes into their docstrings.
  - Direct `journal.db` use (tests only) is not guarded.
- **Pass-through:** `JournalUnusable` is not an `OperationalError`, so `_busy` passes it through. It is not a JournalCorrupt.
- **Out of scope:** how wsd reacts.

## _depth and nesting
- Nested blocks still only do `_depth += 1`, and `-= 1` in `finally`. They never clean up.
- An inner exception passes unchanged to the outermost exit, which tries ROLLBACK once.
- Poison is set only at that exit, so nothing inside the block sees it.
- `_depth` returns to 0 even when poisoned. The flag, not `_depth`, stops reuse: `_depth == 0` with `in_transaction` True is the state the flag covers.

## Tests (`tests/test_wsd_journal.py`)
**`_FailOn` changes:**
- `db: "sqlite3.Connection | _FailOn"` (quoted: the test module has no `from __future__ import annotations`, so an unquoted self-reference is a NameError at collection), so proxies can be stacked;
- `exc: BaseException`;
- `close()` is forwarded to the inner connection;
- ROLLBACK attempts are counted with `seen`, which includes the failing attempt, never with `ran`.

Each test raises a pre-built instance and checks `caught.value is` that instance. The required coverage is tests 1–4 and 6–9, all deterministic. Unless a test says otherwise, the ROLLBACK failure is `_error("SQLITE_INTERRUPT")`.

1. **Body error, then ROLLBACK fails.** The body does `emit`, then raises ValueError. Assert:
   - the same ValueError, whose `__notes__` is exactly one note naming OperationalError;
   - `real.in_transaction` is True, `_depth == 0`, and the ROLLBACK proxy's `seen == 1`;
   - `emit`, `transaction()` and `backup()` each raise JournalUnusable with `__cause__ is` the injected error;
   - `close()` works twice, and `emit` still raises JournalUnusable after close;
   - a new Journal on the path has no events and works.
2. **COMMIT error (non-transient), then ROLLBACK fails.** Stacked proxies: e1 on COMMIT, e2 on ROLLBACK. Assert e1 is raised (not e2), with the note, and the journal is poisoned.
3. **SQLITE_BUSY at COMMIT, then ROLLBACK fails.** Stacked proxies. Assert a JournalBusy whose `__notes__` holds exactly the cleanup note (this is the `_busy` note-copy test), and the journal is poisoned.
4. **E's existing chain is kept.** Build E = ValueError with `__cause__ = cause`, `__context__ = ctx`, `__suppress_context__ = True` and `add_note("prior")`, and `raise E` in the block. Assert after the ROLLBACK failure:
   - `__cause__ is cause` and `__context__ is ctx`;
   - `__suppress_context__` is True;
   - `__notes__ == ["prior", <the cleanup note>]`.
5. **Real SQLite (supplementary).**
   - In the block: `emit` (the write), then arm `journal.db.set_progress_handler(lambda: 1, 1)`, then raise ValueError.
   - Assert the journal is poisoned with an SQLITE_INTERRUPT cause and `in_transaction` is True. A second connection (`timeout=0`) gets SQLITE_BUSY on `BEGIN IMMEDIATE`.
   - `journal.close()`, then the second connection runs `BEGIN IMMEDIATE`, `ROLLBACK` and `close()` before a new Journal is opened. Assert that Journal has no rows.
   - It runs in the normal suite, so on ubuntu and macOS CI.
   - The progress interval is approximate. Only if the ROLLBACK truly succeeded (not poisoned **and** `not in_transaction`) does it `pytest.skip` with the SQLite version. A missing poison while still `in_transaction` fails the test.
6. **Journal closed inside the block.**
   - (a) The body does `emit`, `journal.close()`, then raises ValueError. Assert the same ValueError with a note naming ProgrammingError, a later `emit` raises JournalUnusable with a ProgrammingError cause, and a new Journal has no rows.
   - (b) Close without raising. COMMIT's ProgrammingError carries the note.
7. **KeyboardInterrupt during cleanup.** The ROLLBACK proxy raises it. Assert the KeyboardInterrupt comes out with `__context__ is` the primary, and the journal is poisoned.
8. **Nested.** `_write_then_nested_method` with a non-transient failure on the second INSERT, plus a ROLLBACK failure. Assert:
   - ROLLBACK `seen == 1`;
   - the primary passes the inner block as the same object;
   - `_depth == 0`, and the journal is poisoned.
9. **Queued worker.** Reuse `_SignalBeforeAcquire` and the trace-callback pattern from `test_a_transaction_excludes_other_threads`, with the ROLLBACK proxy installed.
   - The owner writes, starts a worker that calls `emit`, waits on `lock.reached`, then raises.
   - Assert the worker records JournalUnusable, and the trace holds no SQL from the worker thread.
   - No sleeps; the thread joins with a timeout.
10. **Regression.** In `test_contention_inside_a_transaction_rolls_it_all_back` and `test_error_that_already_rolled_back_is_raised_as_it_is`, also assert there are no `__notes__`. Their use-afterwards checks prove there is no poison.

## Mutation targets (killing test)
| Mutant | Killed by |
|---|---|
| Raise C instead of E | 1, 2, 6a |
| Drop `self._broken = C` | 1, 6a, 9 |
| Check in `_locked` before the lock, or dropped | 9 (dropped: 1 too) |
| Drop the check in `transaction()` | 1 |
| Apply the check to `close()` | 1 |
| `close()` loses the lock | review only (no deterministic race test) |
| Clear the poison on close | 1 (emit after close) |
| Drop the note | 1 |
| `_busy` doesn't copy notes | 3 |
| Drop `from C` | 1 |
| `raise exc from failed`, or overwrite `__context__` | 4 |
| Swallow every BaseException | 7 |
| Poison after a successful or skipped ROLLBACK | 10 |
| ROLLBACK regardless of `in_transaction` | 10 (the SQLITE_FULL case) |
| Cleanup in nested blocks too | 8 (`seen`) |
| Skip `_depth = 0` when poisoned | 1, 8 |
