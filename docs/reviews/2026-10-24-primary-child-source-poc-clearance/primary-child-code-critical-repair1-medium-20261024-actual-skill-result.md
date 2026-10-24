Skill "code-review" completed (forked execution).

Result:
**Verdict:** I found no critical flaw in the two repair patches, so this looks good enough to implement as a source-only PoC. That covers source PoC clearance only. It doesn't clear deployment or qualification, or the inherited approval-authoring ancestor, which stays NOT CLEARED.

**Queue fix (`6f9f8c0`):** the fix is correct.
- **Native settlement:** `native_fence` now hands back the ownership FD it actually holds, and `settle_native` passes it to `compact` (line 1830).
- **Abandon:** `abandon` passes its own held FD (line 2010).
- **Why this works:** `retire_stage` compares the FD's inode against its probe and re-locks the already-locked FD. Re-locking the same open file is harmless, so the self-lock that caused the old HOLD is gone.
- **Terminal bypass:** when the fence skips taking the lock (`child=None`), the terminal branches call `compact` on a record that already has a terminal. That skips `retire_stage`, so a `None` FD there is safe.
- **Other callers:** generic settle (line 1677), `_resume_receipted` (line 2075) and lease finalize (line 4183) already pass their FD.

**Hermes fix (`a708371`):** narrowing the catch is correct and `KeyboardInterrupt` still propagates. Two exception types can still escape (finding 2).

I ran no tests and made no edits. All five findings below are non-critical: one fail-closed liveness gap, one containment gap in the Hermes catch, one test gap, and two design-debt items.

```json
[
  {
    "file": "bin/btq-maintenance.py",
    "line": 1200,
    "summary": "A primary-child operation with an unknown local effect at ordinal N and a leftover unpublished stage file for N+1 can never be settled: prepare_primary/settle_primary call terminal_projection, which calls no_stage() and HOLDs before compaction gets a chance to retire the stage.",
    "failure_scenario": "Owner records effect N as 'unknown' (prepare() at line 436 only rejects 'issuing'), then starts persisting intent N+1 and crashes, leaving .primary-<op>.<N+1>.stage. prepare_primary fails at terminal_projection -> no_stage with a staging-orphan HOLD every time. Generic settle needs terminal effects and abandon-notice only covers notices, so the operation stays stuck with no recovery path. The abandon and native paths now handle this same state (the new tests use .0002 after an unknown ordinal 1). It fails closed, so it is safe, but it is a liveness gap."
  },
  {
    "file": "harness/primary_inspection.py",
    "line": 308,
    "summary": "The narrowed marker catch still misses subprocess.TimeoutExpired (a SubprocessError but not a CalledProcessError) and SyntaxError from compile/exec of the queue executable in Beads._module, so the read-only CLI can still crash with a raw traceback.",
    "failure_scenario": "Queue construction or coordinator loading reaches btq's git helper, which uses subprocess.run(timeout=GIT_TIMEOUT); if git hangs, TimeoutExpired escapes inspect_pickup and cli.main prints a traceback containing the command and paths to stderr instead of the closed 'marker-unavailable' result. Likewise, a corrupted or edited btq executable raises SyntaxError from compile(). Catching subprocess.SubprocessError (and SyntaxError) would close both without catching BaseException."
  },
  {
    "file": "bin/btq-maintenance.py",
    "line": 1291,
    "summary": "settle_primary still calls ledger.compact(plan['after_operation']) without the ownership FD even though it runs inside native_fence, which holds an exclusive lock on that same lease. If retire_stage ever finds a stage file here, its fresh probe lock conflicts with the fence's own lock and it HOLDs on itself.",
    "failure_scenario": "Today this is unreachable only because the no_stage check in finding 1 HOLDs first. If that check is relaxed (the obvious fix for finding 1), settle_primary writes the primary receipt and the after_operation record, then compact -> retire_stage HOLDs with 'original ownership still held'. Every retry ends the same way, after the immutable receipt is already written. Binding `as child` at line 1236 and passing ownership_fd=child keeps it consistent with the repaired paths."
  },
  {
    "file": "tests/test_primary_child_settlement.py",
    "line": 524,
    "summary": "The new regressions cover only the first-pass native and abandon paths; nothing tests the native_fence terminal bypass (child=None) or _resume_receipted with a leftover stage file.",
    "failure_scenario": "A later edit that makes the terminal branch at line 1788 reach retire_stage, or that drops ownership_fd=child at line 2075, would bring back the self-lock HOLD with no failing test. The receipted-resume path is exactly where a crash between writing the receipt and compacting leaves a stage file behind."
  },
  {
    "file": "bin/btq-primary-child-recovery.py",
    "line": 311,
    "summary": "Design debt: retire_stage quietly falls back to opening and locking a new probe when ownership_fd is None. Any compaction caller that already holds the recovery fence and forgets the FD deadlocks on itself, which is how this PoC blocker happened.",
    "failure_scenario": "Six compaction call sites inside fences (1251, 1291, 1677, 1830, 2010, 2075) each have to remember to pass the FD by hand, and one of them (1291) still doesn't. A deeper fix would let the coordinator or ledger carry the held fence FD, or make retire_stage require an explicit FD (or an explicit 'not fenced' sentinel), so a forgotten FD fails loudly instead of self-deadlocking."
  },
  {
    "file": "bin/btq-maintenance.py",
    "line": 1884,
    "summary": "abandon, settle and _resume_receipted each re-implement the same turnstile/gate/marker/lease-flock fence that native_fence already wraps, which is why the ownership FD was threaded through inconsistently.",
    "failure_scenario": "There are four copies of the same lock-acquisition sequence, each with slightly different HOLD messages and lock order (abandon takes the lease lock after journal_lock, native_fence takes it before). A fix to one copy, like this FD repair, has to be repeated by hand in each. Routing these paths through native_fence(..., allow_terminal=...) and using the yielded FD would remove the duplication."
  }
]
```
