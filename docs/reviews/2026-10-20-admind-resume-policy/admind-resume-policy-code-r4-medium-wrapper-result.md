**Verdict: APPROVE as a source PoC**, on the condition you already set: the full test run has to come back green. Nothing here qualifies the change for activation or real-resume use.

I ran this through the built-in `code-review` skill at medium effort, under your read-only rules. Nothing was executed; everything comes from reading `report.md`, `candidate.patch`, `oracle-repair.patch` and the frozen source.

## Source PoC scope

**Critical flaws: none.**
- **The stub fix is correct.** `StubTmux.session_absent` returns `name not in self.sessions`. That matches the call in `AdminAgent.alive()` (`src/heterodyne/admind/agent.py:93`) and how the real `Tmux.session_absent` behaves.
- **The wording changes are correct.** The four assertions in `tests/test_admind_t9r1.py` match what `daemon.py` actually says: "Admin agent recovery is paused … `!new`" at line 2751, and "Not delivered: admin agent recovery is paused" at line 3813.

**Lower-severity issues** (none of them block the PoC):

| # | Severity | Location | Issue |
|---|---|---|---|
| 1 | Medium | `tests/live/stub_admind.py:36` | No test in the repo checks that `StubTmux` has every method `TmuxLike` requires; the only proof is `smoke.py` in the handoff directory. The test run in `tests-command.json` never loads `stub_admind.py`, and the live-harness paths (`test_live_decisions.py`, `test_live_asks_bump.py`) weren't run with the repaired stub. The next method added to `TmuxLike` would bring back the same AttributeError without failing anything. |
| 2 | Medium | `tests/test_admind_t9r1.py:97` | The one-notice check now counts only "recovery is paused". A duplicate "temporarily unavailable" notice (`daemon.py:2758`) would still pass. Matching on something every stuck-notice variant shares (e.g. `!tail`) would keep the guarantee. |
| 3 | Low | `tests/test_admind_t9r1.py:117` | The pane-dead test only counts notices. It doesn't check that the notice still tells the operator about `!new`. |
| 4 | Low (docs) | `docs/admind.md:264`, `:80` | The operator docs still describe the old behaviour: three relaunches, "did not start after 3 launches", and refused messages. The tests now assert zero relaunches, input kept as received, and a "paused … `!new`" notice. |
| 5 | Low | `daemon.py:203` | `AGENT_STUCK_NOTICE` ("not running …") is no longer used anywhere. If someone reconnects it, the new filters wouldn't count it. |
| 6 | Low | `tests/live/stub_admind.py:36` | The stub can't simulate a failed liveness check: `session_absent` never raises and `pane_dead` always returns False. So the live tests never exercise the daemon's `TmuxError` handling (`daemon.py:2778`). The report's description of live-harness coverage claims more than this. |

Every finding from the third (medium) review and both high-effort rounds carries forward. This review was an addition, not a reset, and I didn't re-check those findings.

## What the approval depends on
1. **The test run still has to go green.** The first full run (2845 passed, 2 failed on the old wording) is still on record. The repaired-wording run across the 102 publication modules is still going, and I don't have its result.
2. **Fix #1 before relying on the live harness.** Add a small test that checks `StubTmux` against `TmuxLike`'s methods, or at least run the live-harness tests once with the repaired stub.
3. I'd also fold #2 and #4 into the same change; neither is a blocker.

## Activation and deployment
Not covered by this verdict. Activating the change, testing a real resume, the concurrent export/observer bridge work and the initial-owner work all still need their own review.
