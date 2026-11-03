I ran one full HIGH review of frozen head `c4e420f` against baseline `7caddc2`, using the built-in /code-review recipe for all 8 angles in this context.

- **Inputs:** the cumulative diff (5 files, +315/−1), the touched runtime callees (`_end`, `_cause`, `_reaped_live`, `_teardown`, `_land`, `_settle_wip`, `_running`, `_held`, `stop`, `expire`, `preflight_resume`), the tmux helpers, `reaper.mark_live`, `qualification.md`, and the approved daf3 design.
- **Tests:** the safe isolated command at the frozen head gave `10 passed, 324 deselected`. I did not run the full suite or pyright/ruff.
- **Model and effort:** the exposed model ID is `claude-opus-5-5`, and this agent was given the recipe at "high". I can't see the CLI, settings or environment effort configuration, and internal effort is not exposed.
- **Scope:** the PR160/161 preflight history does not cover this code. No CLAUDE.md applies to the changed files (there is no repo CLAUDE.md, and the user-level ones have no code rules), so the conventions angle found nothing.

**Verdict:** No critical safety blockers in the bounded source. I found no path that ends a live executor, creates an operation ID, launches, or writes before the owner/snapshot check.
- **Exact identity:** checked over the full record and the exact receipt bytes.
- **Death proof:** the marker is a strict three-key regular file with `deadline <= at <= now`, plus an explicitly dead pane.
- **Ordering:** the receipt is written durably before the STOPPING write.
- **Continuation:** the end gate and the late-create/reaper `_running` gate hold.

**One major finding I'd fix before Hermes wiring:** `OSError` breaks the LIVE/ENDED/`RuntimeUnavailable` contract (finding 1). Findings 2–4 are moderate. Findings 5–10 are noncritical advisories: compatibility, liveness, tests/docs and cleanup.

10 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:639`: reconcile_session promises 'LIVE, ENDED, or a RuntimeUnavailable hold' but raw OSError escapes from the direct _phase STOPPING write (639), the receipt write_private (618), the ENDED-path _settle_wip whose _phase record write is unguarded (636), and read_bounded's read/fstat; the test at tests/test_sandbox_runtime.py:3038 locks this in with pytest.raises((RuntimeUnavailable, OSError)).

   Failure scenario: A Hermes caller that maps RuntimeUnavailable to a typed HOLD (as the docstring and design say) hits ENOSPC/EIO on the receipt or STOPPING write and gets an uncaught OSError instead of HOLD. That can crash the daemon drain or leave the recovery intent in an unclassified state. launch() wraps the same _settle_wip OSError into RuntimeUnavailable (runtime.py:715-718), but reconcile does not.

2. `src/heterodyne/sandbox/runtime.py:633`: reconcile_session lands private Git, makes the lifetime WIP commit on btq/<bead> and returns ENDED without the _held(ws, key) resolve-intent gate or any check for an unlanded/non-ended sibling record on the same bead/repo. stop(), sessions() and preflight_resume all consult those gates.

   Failure scenario: Another session of the same workstream/bead is mid `wsctl resolve` (an intent exists) or is unlanded. Reconcile ends this generation, commits a WIP onto btq/<bead> over the unresolved commits (the divergence _settle_wip's docstring warns about) and reports ENDED as the 'no unlanded/WIP obligation' clearance, though the workstream is held.

3. `src/heterodyne/sandbox/runtime.py:627`: On continuation with a durable receipt and the record still RUNNING, the code requires the tmux pane to still exist and be explicitly dead. It never compares the observed %pane|pid with the pane identity stored in the receipt. A missing pane is a permanent hold even though the receipt already proves death.

   Failure scenario: A receipt-only crash (receipt written, STOPPING write lost) is followed by a tmux server restart or host reboot. The dead pane is gone, dead_pane_evidence raises TmuxError, and every retry with the same original record HOLDs forever with no supported continuation. Separately, the re-check does not tie the re-observed pane to the receipt's %N/PID, so the 'same operation' evidence is weaker than it looks.

4. `src/heterodyne/sandbox/runtime.py:593`: If Product's own expire()/sessions()/launch() ends the expired generation with a lifetime cause before Hermes calls reconcile, there is no receipt, current != expected, and the end consumed the marker and pane. reconcile can then never return ENDED for that legitimately lifetime-ended generation.

   Failure scenario: wsd's periodic expire(ws) reaches the deadline-reaped RUNNING record first (cause LIFETIME via _reaped_live, WIP settled). Hermes then calls reconcile_session(original) and gets a permanent 'changed without a receipt' HOLD. No Product API turns this already-clean ended generation into the ENDED receipt the design's launch step needs.

5. `src/heterodyne/sandbox/recovery_evidence.py:71`: The receipt binds the original record by exact msgspec serialization, and validate_marker re-checks `at <= now` against the current clock on every continuation. Either a SessionRecord schema change or a clock step back makes a valid retained receipt permanently mismatch.

   Failure scenario: The paired postlaunch installer deploys a Product with a new defaulted SessionRecord field while a reconcile is in STOPPING. encode(expected) now includes the new field, the receipt's `original` does not, and the same operation can never finish. Likewise an NTP step back after the receipt write makes `now < at` and HOLDs until the clock catches up.

6. `src/heterodyne/sandbox/runtime.py:596`: LIVE is returned as soon as _alive is true, without considering the deadline or an already-written reaper marker, and without the final owner/record re-read the ENDED path performs.

   Failure scenario: Between the reaper's mark_live and its kill (at/after the deadline), sandbox and pane are both still live, so reconcile reports LIVE for a generation that dies seconds later. Hermes records a live executor and needs another full request cycle. The owner could also have been lost between check_owner and the LIVE return without detection.

7. `tests/test_sandbox_runtime.py:3065`: The tests leave several holds the design names untested: a marker with the wrong sandbox or extra keys, a valid marker with a live pane but no listed sandbox, a tampered/oversized receipt, an expected record that differs from disk, a STOPPING record with a non-lifetime cause, owner loss mid-operation, and a landing failure (unlanded) that must fail the ENDED gate.

   Failure scenario: A regression that accepts a foreign-sandbox marker, kills a live pane listed without its sandbox, or reports ENDED with unlanded=True would pass all 10 current cases. qualification.md still says full-suite and static results 'will be appended', but they are absent at the frozen head.

8. `src/heterodyne/sandbox/recovery_evidence.py:74`: The dead-pane regex is duplicated verbatim in tmux.dead_pane_evidence and recovery_evidence.receipt. The marker open/fstat/read/JSON logic and its 4096 bound (MARKER_LIMIT) re-implement runtime._reaped_live/MARKER_BYTES.

   Failure scenario: Changing the tmux format (e.g. adding pane_dead_time) or the marker bound in one place silently makes new receipts fail validation or diverge from _cause's marker reading. Share one pane-pattern constant and one bounded marker reader.

9. `src/heterodyne/sandbox/runtime.py:616`: `self.manager_owner is not owner` is re-tested right after every check_owner() call, which already raises on that exact condition (lines 616, 631, 651). Building the Evidence struct (hashlib, hex, tail truncation) inline in the runtime adds to the 93-line, complexity-40 method instead of living in recovery_evidence.

   Failure scenario: Redundant conditions and inline evidence assembly add to the SandboxRuntime verbosity that ripwire already flags. Use a single check_owner() plus a recovery_evidence.build(expected, marker, pane, tail) helper and drop the hashlib import from runtime.

10. `src/heterodyne/sandbox/runtime.py:638`: The direct _phase(STOPPING, stop_reason, end_cause=LIFETIME, wip_mark) duplicates the owed-fields write _end makes itself, adding a second record write and a reconcile-only special case that bypasses _end's handling of a failed STOPPING write.

    Failure scenario: Every deadline end writes the STOPPING record twice. Lifetime-journal semantics now live in two places that can drift, e.g. _end returns False on a failed healthy-cause write while this path raises OSError. Passing the receipt-derived cause into _end (or having _cause accept retained evidence) would keep one journal writer.

I ran one full HIGH review of frozen head `c4e420f` against baseline `7caddc2`, using the built-in /code-review recipe for all 8 angles in this context.

- **Inputs:** the cumulative diff (5 files, +315/−1), the touched runtime callees (`_end`, `_cause`, `_reaped_live`, `_teardown`, `_land`, `_settle_wip`, `_running`, `_held`, `stop`, `expire`, `preflight_resume`), the tmux helpers, `reaper.mark_live`, `qualification.md`, and the approved daf3 design.
- **Tests:** the safe isolated command at the frozen head gave `10 passed, 324 deselected`. I did not run the full suite or pyright/ruff.
- **Model and effort:** the exposed model ID is `claude-opus-5-5`, and this agent was given the recipe at "high". I can't see the CLI, settings or environment effort configuration, and internal effort is not exposed.
- **Scope:** the PR160/161 preflight history does not cover this code. No CLAUDE.md applies to the changed files (there is no repo CLAUDE.md, and the user-level ones have no code rules), so the conventions angle found nothing.

**Verdict:** No critical safety blockers in the bounded source. I found no path that ends a live executor, creates an operation ID, launches, or writes before the owner/snapshot check.
- **Exact identity:** checked over the full record and the exact receipt bytes.
- **Death proof:** the marker is a strict three-key regular file with `deadline <= at <= now`, plus an explicitly dead pane.
- **Ordering:** the receipt is written durably before the STOPPING write.
- **Continuation:** the end gate and the late-create/reaper `_running` gate hold.

**One major finding I'd fix before Hermes wiring:** `OSError` breaks the LIVE/ENDED/`RuntimeUnavailable` contract (finding 1). Findings 2–4 are moderate. Findings 5–10 are noncritical advisories: compatibility, liveness, tests/docs and cleanup.

10 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:639`: reconcile_session promises 'LIVE, ENDED, or a RuntimeUnavailable hold' but raw OSError escapes from the direct _phase STOPPING write (639), the receipt write_private (618), the ENDED-path _settle_wip whose _phase record write is unguarded (636), and read_bounded's read/fstat; the test at tests/test_sandbox_runtime.py:3038 locks this in with pytest.raises((RuntimeUnavailable, OSError)).

   Failure scenario: A Hermes caller that maps RuntimeUnavailable to a typed HOLD (as the docstring and design say) hits ENOSPC/EIO on the receipt or STOPPING write and gets an uncaught OSError instead of HOLD. That can crash the daemon drain or leave the recovery intent in an unclassified state. launch() wraps the same _settle_wip OSError into RuntimeUnavailable (runtime.py:715-718), but reconcile does not.

2. `src/heterodyne/sandbox/runtime.py:633`: reconcile_session lands private Git, makes the lifetime WIP commit on btq/<bead> and returns ENDED without the _held(ws, key) resolve-intent gate or any check for an unlanded/non-ended sibling record on the same bead/repo. stop(), sessions() and preflight_resume all consult those gates.

   Failure scenario: Another session of the same workstream/bead is mid `wsctl resolve` (an intent exists) or is unlanded. Reconcile ends this generation, commits a WIP onto btq/<bead> over the unresolved commits (the divergence _settle_wip's docstring warns about) and reports ENDED as the 'no unlanded/WIP obligation' clearance, though the workstream is held.

3. `src/heterodyne/sandbox/runtime.py:627`: On continuation with a durable receipt and the record still RUNNING, the code requires the tmux pane to still exist and be explicitly dead. It never compares the observed %pane|pid with the pane identity stored in the receipt. A missing pane is a permanent hold even though the receipt already proves death.

   Failure scenario: A receipt-only crash (receipt written, STOPPING write lost) is followed by a tmux server restart or host reboot. The dead pane is gone, dead_pane_evidence raises TmuxError, and every retry with the same original record HOLDs forever with no supported continuation. Separately, the re-check does not tie the re-observed pane to the receipt's %N/PID, so the 'same operation' evidence is weaker than it looks.

4. `src/heterodyne/sandbox/runtime.py:593`: If Product's own expire()/sessions()/launch() ends the expired generation with a lifetime cause before Hermes calls reconcile, there is no receipt, current != expected, and the end consumed the marker and pane. reconcile can then never return ENDED for that legitimately lifetime-ended generation.

   Failure scenario: wsd's periodic expire(ws) reaches the deadline-reaped RUNNING record first (cause LIFETIME via _reaped_live, WIP settled). Hermes then calls reconcile_session(original) and gets a permanent 'changed without a receipt' HOLD. No Product API turns this already-clean ended generation into the ENDED receipt the design's launch step needs.

5. `src/heterodyne/sandbox/recovery_evidence.py:71`: The receipt binds the original record by exact msgspec serialization, and validate_marker re-checks `at <= now` against the current clock on every continuation. Either a SessionRecord schema change or a clock step back makes a valid retained receipt permanently mismatch.

   Failure scenario: The paired postlaunch installer deploys a Product with a new defaulted SessionRecord field while a reconcile is in STOPPING. encode(expected) now includes the new field, the receipt's `original` does not, and the same operation can never finish. Likewise an NTP step back after the receipt write makes `now < at` and HOLDs until the clock catches up.

6. `src/heterodyne/sandbox/runtime.py:596`: LIVE is returned as soon as _alive is true, without considering the deadline or an already-written reaper marker, and without the final owner/record re-read the ENDED path performs.

   Failure scenario: Between the reaper's mark_live and its kill (at/after the deadline), sandbox and pane are both still live, so reconcile reports LIVE for a generation that dies seconds later. Hermes records a live executor and needs another full request cycle. The owner could also have been lost between check_owner and the LIVE return without detection.

7. `tests/test_sandbox_runtime.py:3065`: The tests leave several holds the design names untested: a marker with the wrong sandbox or extra keys, a valid marker with a live pane but no listed sandbox, a tampered/oversized receipt, an expected record that differs from disk, a STOPPING record with a non-lifetime cause, owner loss mid-operation, and a landing failure (unlanded) that must fail the ENDED gate.

   Failure scenario: A regression that accepts a foreign-sandbox marker, kills a live pane listed without its sandbox, or reports ENDED with unlanded=True would pass all 10 current cases. qualification.md still says full-suite and static results 'will be appended', but they are absent at the frozen head.

8. `src/heterodyne/sandbox/recovery_evidence.py:74`: The dead-pane regex is duplicated verbatim in tmux.dead_pane_evidence and recovery_evidence.receipt. The marker open/fstat/read/JSON logic and its 4096 bound (MARKER_LIMIT) re-implement runtime._reaped_live/MARKER_BYTES.

   Failure scenario: Changing the tmux format (e.g. adding pane_dead_time) or the marker bound in one place silently makes new receipts fail validation or diverge from _cause's marker reading. Share one pane-pattern constant and one bounded marker reader.

9. `src/heterodyne/sandbox/runtime.py:616`: `self.manager_owner is not owner` is re-tested right after every check_owner() call, which already raises on that exact condition (lines 616, 631, 651). Building the Evidence struct (hashlib, hex, tail truncation) inline in the runtime adds to the 93-line, complexity-40 method instead of living in recovery_evidence.

   Failure scenario: Redundant conditions and inline evidence assembly add to the SandboxRuntime verbosity that ripwire already flags. Use a single check_owner() plus a recovery_evidence.build(expected, marker, pane, tail) helper and drop the hashlib import from runtime.

10. `src/heterodyne/sandbox/runtime.py:638`: The direct _phase(STOPPING, stop_reason, end_cause=LIFETIME, wip_mark) duplicates the owed-fields write _end makes itself, adding a second record write and a reconcile-only special case that bypasses _end's handling of a failed STOPPING write.

    Failure scenario: Every deadline end writes the STOPPING record twice. Lifetime-journal semantics now live in two places that can drift, e.g. _end returns False on a failed healthy-cause write while this path raises OSError. Passing the receipt-derived cause into _end (or having _cause accept retained evidence) would keep one journal writer.
