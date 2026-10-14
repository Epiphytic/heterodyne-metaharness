# btq-u63ak: escaped test fakes are always killed, and the run proves it

Bead: btq-u63ak. Base: main 093ede5. Test-only: nothing under `src/` changes, and no ADR is touched.

## Problem

`test_a_descendant_holding_the_output_open_never_holds_the_read[escaped]` (tests/test_agents_usage.py) runs the fake app-server with `HZ_FAKE_RATE_FAULT=escaped`. The fake starts a `time.sleep(600)` descendant in a session of its own and then writes its PID to `HZ_FAKE_CHILD_PID_FILE`. The test's `finally` kills that PID only `if child.exists()`. On 2026-10-13 one such sleeper was found alive as PID 2248827, after its run had ended.

The leak has three causes, and every test that spawns an escaped or long-lived descendant shares at least one of them:

1. **The PID file is written after the spawn.** The fake runs `Popen` first and writes the file second (fake_agent_cli.py:117-119). The test's read has a 1 s limit and kills the app-server's group when it runs out. If that kill lands between the two steps, the escaped child exists but no PID file names it. Cleanup gated on `exists()` then does nothing.
2. **The PID file isn't written atomically.** `write_text` truncates the file and then writes it. A read between those two steps sees an empty file: `int("")` raises ValueError in the `finally`, or a suppressed ValueError skips the kill.
3. **Cleanup runs on one path only.** In the `orphan` case, the `finally` doesn't kill anything. If the group kill fails and `wait_for` raises, the in-group `sleep(600)` survives too.

PR #75's hostread tests kill by PID file in a `finally`. They wait for the file before trusting it. But a PID file can only name a process that got as far as writing it. Any cleanup that starts from the file inherits cause 1.

## Survey

These are the offline tests that spawn a descendant meant to outlive, or to leave, its parent's process group. (tests/live is out of scope: it isn't run offline.)

| Site | Descendant | Its life | Current cleanup | Causes |
|---|---|---|---|---|
| test_agents_usage.py:332 `[escaped]` via fake_agent_cli.py:116 | own session | 600 s | kill if the PID file exists | 1, 2 |
| test_agents_usage.py:332 `[orphan]`, same fake | app-server's group | 600 s | none in `finally` | 1, 2, 3 |
| test_sandbox_hostread.py:480 and :497, same fake | own session | 600 s | kill by PID file; FileNotFound and ValueError suppressed | 1, 2 |
| test_admind_approvals.py:510 and :539 via fake_approve_bead.py `hold_stdout` | own session (`os.setsid`) | 30 s | `kill_quietly`: kill if the file exists | 1 (killed between `os.read` and the write), 2 |
| test_admind_approvals.py:290, :314, :349, :383, :407, :425 and :2038 via `DESCENDANT` and fake_approve_bead `descendant` | parent's group (fork) | 60 s | `kill_quietly` | 1 (fork then write); the write itself is atomic |
| test_admind_r13_summary.py `run_escaped` (`ESCAPED`) | own session (`os.setsid`, then `exec sleep 20`) | 20 s | kill if the file exists | 1, 2 (O_TRUNC then write) |

These don't need the helper:
- test_tmux_launch_lock.py:431. The PID comes back on stdout before the `try` starts, and the `finally` kills it unconditionally.
- Processes in tmux panes and the watchdog's child pytest runs (test_tmux_watchdog.py, tmux_guard.py). The tmux guard's watchdog marks and sweeps those, and asserts it did (`assert_cleaned`, `matching(mark)`).
- The fake app-server itself. The code under test reaps it (`gone(app-server.pid)`). If it ever leaked, the test would fail loudly instead of passing with a stray process. Its argv comes from the code under test, so it can't carry a mark.

## Design

### Find survivors by a mark, not by a PID file

Every long-lived descendant that a fake starts carries a mark in its argv. That is a single argument `hz-escape.<run>.<n>`:
- `<run>` is 12 hex digits from `secrets.token_hex(6)`, drawn once per pytest process. Each xdist worker draws its own.
- `<n>` is a counter per fixture use.

The argv is chosen because a forked child inherits it, `ps` shows it on Linux and macOS alike, and nothing in the code under test can scrub it. Environment variables can be scrubbed: the code under test builds the app-server's environment, so a mark there could be lost. The fake's sleeper becomes `[sys.executable, "-c", SLEEPER, mark]`. `ESCAPED` swaps `exec sleep 20` for the same Python sleeper. `DESCENDANT` takes the mark as an extra argument, and its forked child inherits it.

Cleanup then finds the process whether or not any PID file was written. The PID files stay, because tests use them for their own assertions (`alive`, `gone`), but cleanup no longer depends on them.

### The helper: `tests/escapes.py`, a fixture plus a run-end sweep

It goes in its own module, not tests/sandbox_env.py, because the admind tests use it too and don't import the sandbox rig. tests/conftest.py registers the module as a plugin.

```python
class Escapes:
    mark: str                       # hz-escape.<run>.<n>, unique to this fixture use
    def env(self) -> dict[str, str]: ...   # {"HZ_ESCAPE_MARK": mark}, for fakes that take settings by env
    def reap(self, bound: float = 10.0) -> None: ...

@pytest.fixture
def escapes() -> Iterator[Escapes]: ...    # teardown: reap()
```

**`reap(bound)`** finds and kills marked processes until none is left.
- **Find.** `ps -ax -ww -o pid=,stat=,command=` lists every process whose argv contains `mark` as a whole word. A word is split on whitespace and compared for equality, so `.1` never matches `.12`. Zombies (stat `Z`) are skipped: they're no longer running, and their argv is gone on Linux anyway.
- **Kill.** Each one gets SIGKILL.
  - On Linux the kill is identity-checked first. The helper opens a pidfd, re-reads `/proc/<pid>/cmdline`, and confirms the mark is there before `signal.pidfd_send_signal`. A PID reused between `ps` and the kill is therefore never signalled.
  - On macOS there are no pidfds, so the helper kills directly after the scan. That leaves a window of microseconds, the same window the existing `kill(int(read_text()))` calls already accept.
  - ProcessLookupError means the process is already gone.
- **Wait.** The helper waits, polling every 50 ms up to `bound` seconds, until a fresh scan finds no marked process left. If any remains, `reap` raises AssertionError naming the PIDs. The fixture's teardown then reports an error: a leak is never silent.
- **Run-end sweep.** `pytest_sessionfinish` in the plugin calls the same routine for the prefix `hz-escape.<run>.`, which covers every mark this process handed out. It kills anything it finds. If it found anything, it reports the PIDs and sets the session's exit status to failed. So even a site that forgets the fixture, or a teardown that never ran, is caught before the run returns.

**Safety.** The helper only signals processes whose argv carries a mark from this pytest process. Another worker, another run, or anything else on the host can't match, because `<run>` is random per process. It never kills by a bare PID file.

### The fakes

- **fake_agent_cli.py** (`orphan`, `escaped`) appends `HZ_ESCAPE_MARK` to the sleeper's argv, and writes the PID file atomically: a temp file, then `os.replace`. If the mark isn't set, it raises before spawning, so no unmarked sleeper is ever started.
- **fake_approve_bead.py** takes the mark the way it takes its database: `approve_bead_wrapper(tmp_path, mark=...)` (tests/admind_asks_fixture.py) writes it into the wrapper beside `FAKE_BTQ_DB`. It gets the same argv mark and atomic write in `hold_stdout` and `descendant`.
- **`ESCAPED` (r13 summary) and `DESCENDANT` (approvals)** are test-authored scripts. They take the mark as an argument and use the Python sleeper.
- **Sleepers' lifetimes.** The 600 s sleepers become 60 s, and the 20 s, 30 s and 60 s ones stay as they are. Each test needs its descendant alive for well under 10 s. This is defence in depth only: the proof is `reap`, not the timeout.

### Call sites

Each site in the table takes `escapes`, passes its mark to the fake, and drops its own `finally` kill and `kill_quietly`. Their assertions stay unchanged. The fixture's teardown runs whether the test passed, failed or errored, after the test body and before `tmp_path` cleanup.

## How it is proven

- **No marked process outlives a test.** The teardown's `reap` raises if one survives the bound, so a leak fails the test that caused it.
- **No marked process outlives the run.** The run-end sweep fails the session if it finds any mark from the run.
- **The original bug is a regression test.** One test runs the `escaped` fault with `HZ_FAKE_CHILD_PID_FILE` pointing into a directory that doesn't exist. The fake spawns the marked sleeper, then fails to write its PID file and dies. That is exactly the state cause 1 leaves behind. The test then calls `escapes.reap()` and asserts no marked process is left. Before this change, the same state leaks a 600 s sleeper.

## Tests (new file: tests/test_escapes.py)

1. **No PID file.** A marked sleeper in a session of its own, whose parent has exited and which has no PID file, is killed by `reap`. The scan afterwards is empty.
2. **The fixture tears down after a failure.** A child pytest run, in the style of test_tmux_watchdog.py's `Child`, has one test that starts a marked escaped sleeper and then fails. Its report shows the test failed and teardown passed, and once it exits no process carries its run's prefix.
3. **A forgotten fixture is swept.** In a child pytest run, a test starts a sleeper with a mark from the run but outside any fixture use, then passes. The run-end sweep kills it, and the run exits non-zero with its PID reported.
4. **Bystanders are never killed.**
   - A sleeper whose argv has `<mark>2`, another run's prefix, or the mark inside a longer word is still alive after `reap` and the sweep.
   - On Linux, a PID whose cmdline changes between the scan and the kill isn't signalled. This is simulated by monkeypatching the cmdline read.
   - The test kills these bystanders itself, by their `Popen` handles.
5. **The bound holds.** With the kill monkeypatched to do nothing, `reap(bound=0.5)` raises AssertionError naming the PID within 1.5 s. The test then kills the sleeper by its handle.
6. **Zombies don't count.** A marked child that has been killed but not yet reaped (it is the test's own child) doesn't make `reap` fail.
7. **The fakes refuse to run without a mark.** `escaped` and `orphan` with `HZ_ESCAPE_MARK` unset raise before any spawn, and the scan for this test's mark stays empty.
8. **The regression test from "How it is proven".** The escaped fault with an unwritable PID file path leaves a marked sleeper, and `reap` ends it.
9. **The existing sites still pass unchanged.** These are test_agents_usage.py T5 (both faults), the hostread escape tests, the approvals descendant and held-stdout tests, and r13 summary's `run_escaped` tests. Their assertions are not loosened. Only their cleanup moves to the fixture.

All of these tests are offline, and the child pytest runs use `short_dir` bases. Every process a test starts is killed by its handle or by `reap` before the test returns.

## Out of scope

- tests/live.
- tmux-pane and watchdog processes, which the tmux guard already marks and sweeps.
- The fake app-server process itself, which the code under test reaps and the tests assert gone.
- Any change to `reap.py`, hostread or codex_usage.
