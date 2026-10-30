**Verdict: no critical flaws. PR150 is good enough to implement as a PoC.** This doesn't change the R2 APPROVE.

The builtin `/code-review 150` ran on the real PR150 diff this time (`runtime.py` medium gate plus `test_sandbox_runtime.py`), not the root-helper code. It reported 9 candidates and ran no verify pass. I confirmed the most serious one against the source at `61065df8`; the rest are its unverified reading.

**Worth fixing after the PoC (not blockers):**

1. **The recorded install can belong to a launch that never ran (confirmed).** At `src/heterodyne/sandbox/runtime.py:592-597`, `launch()` writes the new `install_name`/`install_digest` *before* `_registered`/`_start`. It also copies `ran`/`native_id` over from the previous generation. So if a generation without a typed effort fails on install B, the record says B while the native thread came from install A. A later medium resume on B would then pass the gate at `:628`. Reaching this takes a profile change plus a re-pinned install plus a failed launch, so it's an edge case. Fix: only record the install once a generation has actually run.
2. **The previous effort isn't tracked (`:625`).** Low-to-medium would pass, but the evidence only covers high-to-medium and medium-to-medium.
3. **The model isn't checked (`:627`).** The evidence is gpt-6.1-sol only, which you already disclosed, but the positive test expects `fixture-model` to pass.
4. **The integrity check now runs before cleanup (`:626`).** `_cli`/`verify_pin` runs before `_end`, so a tainted or missing install leaves a stale RUNNING record. This matches the docstring's "explicit stop/reconciliation owns retained cleanup", so it's by design, not a bug.

**Weak tests:**

- **Two "refuse before cleanup" tests can't fail (`tests/test_sandbox_runtime.py:2655`, `:2677`).** They call `runtime.stop()` first, which leaves the phase ENDED, so the patched `_end` never fires. To guard the ordering they need a dead-tmux, still-RUNNING previous generation, like the existing crashed-generation test.

**Cleanups:**

- `"0.160.0"` is hardcoded in shared runtime code; it would fit better as a declared capability on the Codex adapter.
- A qualified medium resume runs `verify_pin` and `_resume` twice.

As you noted, the full 290-test suite is still pending and I didn't run it, so it has no PASS from me. I made no edits to source, services, the queue or deployment.
