**Verdict: request changes.** This is round 2 of the HIGH cumulative review of PR146 (`67ce2df..8b4d788`). The repair does what it set out to do for fresh launches and live idempotent readback, but the new hold now runs at the wrong point in `launch` and causes problems. I didn't edit, commit or post anything.

**What needs fixing:**
1. **`src/heterodyne/sandbox/runtime.py:567`** — the hold runs before `_end(prev)`. If the previous generation crashed or its tmux is dead, relaunch now refuses without cleaning it up. The dead sandbox stays allocated, its commits and WIP aren't settled, and the record stays RUNNING until something else reconciles it. Refusing before touching anything is what the design asks for, but here it also skips cleanup of a generation that's already dead. Either clean up a dead prior generation before holding, or accept that behaviour explicitly in the docs and cover it with a test.
2. **`docs/adr/0001-workstreams-v2.md:182`** — the ADR's example profile still uses `effort = "medium"`. With the new unknown-key check, a config copied from it now fails to load, and wsd/admind exit 78. Update the ADR, and ideally have the error suggest `reasoning_effort`.
3. **`runtime.py:569`** — the hold is checked before the unlanded-commits and owed-WIP checks, so "reasoning not qualified" hides the more important integrity error.

**Likely problems, not yet confirmed:**
- `runtime.py:566` — the `ConfigError` raised there isn't converted into a launch refusal. `wsd/park.py:1407` would report it as an unexpected runtime state.
- `runtime.py:565` — adding `reasoning_effort` to a profile blocks every session already parked on it, even ones started under the default. That matches the hold policy, but the docs don't say so.
- `tests/test_sandbox_runtime.py:2519` — the hold tests only cover a cleanly ended prior generation. Nothing tests a crashed one, a handoff, missing state, or the unlanded/WIP order, so the suite passes despite points 1 and 3.

**Code quality (not bugs):** Codex-specific logic sits in the generic `launch` path, the effort value is validated in three places, the adapter interface was widened to pass the whole profile, and the session-state lookup runs twice per launch. These match the line-count, complexity and parameter-count increases the PR already discloses.

**What I checked:**
- HEAD is `8b4d788` and the worktree is clean.
- My hashes of `runtime.py`, `layers.py` and `codex.py` match yours.
- Findings 1–3 are confirmed by reading the code at those lines and the ADR.
- I didn't re-run any tests or independently re-hash the evidence logs and receipts. Your test results (274 runtime PASS, 312 focused PASS, 103 accounts PASS) are as you recorded them. As you said, there's no live host deployment and no proof yet that a resumed session actually uses the configured effort.
