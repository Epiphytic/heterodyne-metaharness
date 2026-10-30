**Verdict: CHANGES REQUESTED.** Nothing found makes this unsafe to keep working on. Before approval, fix the first finding and add the missing tests.

I ran the built-in `code-review` skill at high on `a053630...201d4ff` and checked its findings against the code myself. The eight findings are in the panel, most severe first. Two should block approval:

1. **The gate ignores which install made the thread** (`runtime.py:625`). It only checks the version of the install this launch is pinned to. It never compares against the install recorded on the previous generation, so a resume onto a different install counts as qualified. The evidence only covers resumes on the same binary.
2. **The new medium paths have no tests.** The refusal tests at `tests/test_sandbox_runtime.py:2523` were moved from medium to high, and nothing now tests the remaining refusals (low effort, or medium on another version). Medium can now reach the `_end()` cleanup of a crashed generation at `runtime.py:566`, but that path has no test either. That includes the comment's "verify pin before cleanup" order.

The other six are smaller:
- The "after high" test never checks that the first generation actually wrote high.
- Whether `gpt-6.1-sol` is the only tested model is your design call; you've disclosed it, so it only matters if the README stops saying other models are unverified.
- `"0.160.0"` is hardcoded in `runtime.py` instead of living in the adapter's version table (`agents/codex.py:117`). Since unlisted versions are already refused, that check is always true today.
- Each medium resume verifies the pinned install twice (lines 625 and 582).
- A low-to-medium resume is also allowed, and no evidence covers that transition. The case with no effort set skips the gate entirely, but that was already true before this diff.

**What I confirmed:**
- HEAD is `201d4ff53a00…`. The runtime change is 9 lines and the test change is 48.
- Six of the source hashes you listed match the committed bytes: `runtime.py`, the test file, the README, `focused.log`, and both high-to-medium `result.json` files. I didn't check the other 36.
- Both high-to-medium results show the claimed sequence: the same thread ID, `gpt-6.1-sol` with effort high on the first turn and medium on the second, and two `task_complete` answers.

**What I didn't do:** I didn't rerun the 14 focused tests, Ruff, Pyright or the full 285-test suite, so I'm not confirming any test result. I made no source edits and didn't touch the queue or any deployment.
