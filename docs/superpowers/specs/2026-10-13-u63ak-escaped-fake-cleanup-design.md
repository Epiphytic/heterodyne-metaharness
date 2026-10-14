# btq-u63ak: escaped test fakes end themselves, and each test proves it

Bead: btq-u63ak. Base: main 093ede5. Test-only: nothing under `src/` changes, and no ADR is touched.

Revision 2 (Codex design r1). Cleanup no longer finds survivors with `ps` or kills them. Each long-lived descendant a test starts runs one sleeper, which ends itself once the test's lifeline lock is released. A census lock then proves that none is left. Cleanup sends no signal, so PID reuse can't make it hit another process on any OS (r1 finding 2). There are no argv marks to match (findings 3 and 4) and no discovery subprocess to bound (finding 5).

## Problem

`test_a_descendant_holding_the_output_open_never_holds_the_read[escaped]` (tests/test_agents_usage.py) runs the fake app-server with `HZ_FAKE_RATE_FAULT=escaped`. The fake starts a `time.sleep(600)` descendant in a session of its own, then writes its PID to `HZ_FAKE_CHILD_PID_FILE`. The test's `finally` kills that PID only `if child.exists()`. On 2026-10-13, one such sleeper was found alive as PID 2248827 after its run had ended.

The leak has three causes, and each test that spawns an escaped or long-lived descendant shares at least one of them:

1. **The PID file is written after the spawn.** The fake runs `Popen` first and writes the file second (fake_agent_cli.py:117-119). The read's 1 s limit kills the app-server's group, and that kill can land between the two steps. The escaped child is then alive but has no PID file. Cleanup gated on `exists()` does nothing.
2. **The PID file isn't written atomically.** `write_text` truncates the file and then writes it, so a read in between sees it empty. `int("")` then raises in the `finally`, or a suppressed ValueError silently skips the kill.
3. **Cleanup covers only some paths.** In the `orphan` case, the `finally` kills nothing. Other tests rely only on the production code's group kill. If that cleanup regresses, the assertion fails and the sleeper lives on.

A PID file can only name a process that got as far as writing it. Any cleanup that starts from a PID file inherits cause 1, and any cleanup that signals a PID it found earlier has to defend against that PID being reused.

## Survey

These are the offline tests whose test-authored descendants can outlive the test. tests/live is out of scope because it isn't run offline.

| Site | Descendant | Life | Cleanup today | Causes |
|---|---|---|---|---|
| test_agents_usage.py:332 `[escaped]`, via fake_agent_cli.py:116 | own session | 600 s | kill if the PID file exists | 1, 2 |
| test_agents_usage.py:332 `[orphan]`, same fake | app-server's group | 600 s | none | 1, 2, 3 |
| test_sandbox_hostread.py:480 and :497, same fake | own session | 600 s | kill by PID file; missing or torn file suppressed | 1, 2 |
| test_admind_approvals.py:510 and :539, via fake_approve_bead.py `hold_stdout` | own session (`os.setsid`) | 30 s | `kill_quietly`: kill if the file exists | 1 (the fake can die between `os.read` and the write), 2 |
| test_admind_approvals.py:290, :314, :349, :2038, via fake_approve_bead.py `descendant` (forks) | fake's group | 60 s | `kill_quietly` | 1 (fork, then write) |
| test_admind_approvals.py:383, :407, :425, via the `DESCENDANT` script (forks) | script's group | 60 s | `kill_quietly` | 1 |
| test_admind_r13_summary.py `run_escaped` (`ESCAPED`) | own session (`os.setsid`, then `exec sleep 20`) | 20 s | kill if the file exists | 1, 2 (O_TRUNC, then write) |
| test_admind_r13_summary.py:160 `sleep 30 &` | script's group | 30 s | none: relies on summarize's group kill | 3 |
| test_admind_r13_summary.py:354 `(sleep 60) &` | script's group | 60 s | none: relies on summarize's group kill | 3 |
| test_sandbox_probes.py:642, a `Popen` grandchild of the captured program | program's group | 60 s | kill if the file is readable | 1 |

These are left out, each for a reason:
- **fake_approve_bead.py:237 (`orphan`).** Its forked grandchild sleeps 1 s, writes one decision, and exits by itself. Its bound is its own code, not a cleanup, so it doesn't use the sleeper. Its `kill_quietly(orphan.pid)` at test_admind_approvals.py:576 stays as it is. Every other `kill_quietly` call is replaced.
- **test_tmux_launch_lock.py:431.** The PID comes back on stdout before the `try`, and the `finally` kills it unconditionally. The process is the test's own and needs no lifeline.
- **Processes in tmux panes, and the watchdog's child pytest runs** (test_tmux_watchdog.py, tmux_guard.py). The tmux guard's watchdog marks and sweeps these, and asserts that it did (`assert_cleaned`, `matching(mark)`).
- **The fakes' own long waits** (fake_agent_cli `hang`, fake_approve_bead `hang` and the `time.sleep(600)` after `descendant`). These processes are the code under test's direct children. Its group kill ends them, and the tests assert they're gone. Their lifetime is the behaviour under test, so a lifeline must not change it.

## Design

### One sleeper: `tests/fakes/sleeper.py`

This is a standalone stdlib script: no imports from tests, and safe under `python -I` and a scrubbed environment. Every descendant in the survey table becomes this sleeper, run as `[sys.executable, "-I", SLEEPER, <use-dir>, *opts]`. Its options:
- `--new-session`: call `os.setsid()` first.
- `--census-fd N`: the census lock is already held on fd N; see "Spawning".
- `--ready-fd N`: write one byte once the steps below are done.

Its steps:

1. If `--new-session` is given, it calls `os.setsid()`. setsid changes the session and the group, not the open files, so the census lock is untouched.
2. **Census.**
   - With `--census-fd N`, it keeps fd N, marks it close-on-exec, and checks with `fstat` that it is the use directory's `census`. If that check fails, it exits 0 at once.
   - Without it (only the two shell sites; see "Edge cases"), it opens `<use-dir>/census` with O_RDONLY|O_CLOEXEC and takes `flock(LOCK_SH | LOCK_NB)`. If either step fails, it exits 0 at once: a census that is missing or locked exclusively means the test has already been torn down.
   This is the first thing the sleeper does. Before it, it doesn't wait, block or write anything.
3. It creates `<use-dir>/pids/<pid>`, an empty file used only for reports, and writes the ready byte if asked.
4. **Lifeline.** It opens `<use-dir>/lifeline` with O_RDONLY|O_CLOEXEC, then every 50 ms tries `flock(LOCK_SH | LOCK_NB)` on it. Once that succeeds, or the open fails, the sleeper exits 0. It exits 0 after 60 s regardless. That cap is defence in depth only: no proof relies on it.

While the test runs, the fixture holds the lifeline with an exclusive lock, so the sleeper waits. Once the fixture releases the lock, or its process dies (the kernel drops a dead process's flock locks, even after SIGKILL or an xdist worker crash), each sleeper's next poll succeeds and it exits. The census lock is held for the sleeper's whole life and dropped by the kernel when it exits. So taking the census exclusively proves that no sleeper of this use is still alive.

### Spawning: the census lock is held before the child exists

`sleeper.py` also provides the two ways a Python spawner starts a sleeper. Every Python spawn path in the survey uses one of them.
- **`spawn(use_dir, *, new_session=False, **popen) -> Popen`**
  1. It opens `<use-dir>/census` O_RDONLY, as an inheritable fd, and takes `flock(LOCK_SH | LOCK_NB)`. If either step fails, it raises before any spawn: the test is being torn down, or the directory was never set.
  2. It runs `Popen(argv(use_dir, "--census-fd", fd, ...), pass_fds=(fd, ...), ...)`.
  3. It closes its own copy of the fd, in a `finally`.
- **`fork_held(use_dir) -> int`**
  1. It takes the same lock on an inheritable fd, then calls `os.fork()`.
  2. In the child, it returns 0. The caller redirects its output and execs `argv(use_dir, "--census-fd", fd)`.
  3. In the parent, it closes its copy and returns the child's PID.

flock locks belong to the open file description, not to the fd or the process. A child created by fork, or by Popen (fork plus exec, with the fd kept by `pass_fds`), shares the spawner's description, so it holds the same lock from the instant it exists. The spawner closing its own fd doesn't release the lock while the child still has it open. So for every Python spawn path, there is no moment when a sleeper exists without the census lock.

The spawner holds the lock only between taking it and closing its copy, so `end` also waits for a spawner caught in that window. The spawners are the fakes and the test scripts, all of them our own processes.

### The fixture: `tests/escapes.py`

It goes in its own module, because the admind tests use it too and don't import the sandbox rig. tests/conftest.py registers it as a plugin.

```python
class Escapes:
    dir: Path                               # <run-dir>/<n>: lifeline, census, pids/
    def env(self) -> dict[str, str]: ...    # {"HZ_ESCAPE_DIR": str(dir)}
    def argv(self, *opts: str) -> list[str]: ...   # [sys.executable, "-I", SLEEPER, str(dir), *opts]
    def started(self) -> list[int]: ...     # the PIDs under pids/: a test's positive control
    def end(self, bound: float = 10.0) -> None: ...

@pytest.fixture
def escapes() -> Iterator[Escapes]: ...     # teardown: end()
```

- **Setup.** The fixture creates a run directory once per pytest process, using `tempfile.mkdtemp(prefix="hze")`. That makes it private (mode 0700), uniquely named and created for this process alone, and each xdist worker gets its own. No lock file is ever at a fixed or shared path, and nothing outside this run opens them. The run directory is deliberately not under `tmp_path`: a test's `tmp_path` can be removed while a late sleeper is still starting, and that sleeper must then find its census missing and exit, not a census somewhere else. Inside it, each use gets `<run-dir>/<n>`, where `n` counts uses. The fixture creates `census` and `lifeline` in that directory and takes `LOCK_EX` on `lifeline`, through an O_CLOEXEC fd that the fixture keeps.
- **`end(bound)`.**
  1. It closes the lifeline fd, which releases the lifeline.
  2. Every 50 ms it tries `flock(LOCK_EX | LOCK_NB)` on the census, until the lock is taken or `bound` seconds have passed. Every step is a non-blocking call on a local file, so the bound holds without a subprocess or a timeout.
  3. Once it has the census lock, it removes the use directory while still holding the lock, then closes the census.
  4. If the lock isn't taken within the bound, `end` raises AssertionError naming the PIDs under `pids/`, so the test errors in teardown.
  `end` never sends a signal. A sleeper that something else has stopped or frozen can't exit; it is reported, and nothing touches it.
- **A sleeper that starts late.** This is a sleeper that runs only after `end`. If the census is gone or held exclusively, step 2 of the sleeper exits it. If it opened the census before the directory was removed, the lifeline is released (or its open fails), so its first poll exits it. Either way it ends as soon as its interpreter is up, without ever waiting.
- **The run-end check.** `pytest_sessionfinish` runs `end` on every use directory still present, meaning a use whose teardown never ran. It reports each one and fails the session. It then removes the run directory. This check needs a live pytest process; the sleepers' exit doesn't. A SIGKILLed pytest or a crashed xdist worker can't report, but its sleepers still exit, at their next poll after the kernel drops its lifeline locks.
- **Forked pytest.** If a test forks the pytest process itself without exec, the child would inherit the lifeline's open file and keep it held while it lives. No test in the suite does this. The fixture's docstring says so.

### The fakes and scripts

Each Python spawn path uses `spawn` or `fork_held`, both of which raise before any spawn if `use_dir` is unset. So no sleeper can start without a lifeline and a census.
- **fake_agent_cli.py (`orphan`, `escaped`).** It reads the use directory from `HZ_ESCAPE_DIR`, the way it reads its other `HZ_FAKE_*` settings (the tests already pass these through codex_usage and hostread). It calls `spawn(...)`, with `new_session` set for `escaped`. It writes the PID file atomically: a temp file, then `os.replace`.
- **fake_approve_bead.py.** `approve_bead_wrapper(tmp_path, escape_dir=...)` (tests/admind_asks_fixture.py) puts `HZ_ESCAPE_DIR=<dir>` in the wrapper beside `FAKE_BTQ_DB`. That is an environment setting, so the fake's argv, its argument parsing and the logged calls are unchanged.
  - `hold_stdout` calls `spawn(dir, new_session=True)` with `--ready-fd N`, in place of its inline `setsid` code.
  - `descendant` calls `fork_held(dir)` in place of `os.fork()`. The forked child redirects its output, then calls `os.execv` into `argv(dir, "--census-fd", fd)`. That gives the forked child the sleeper's own argv and lifeline, so it doesn't inherit and rely on the fake's argv (finding 3). It stays in the fake's group, and its PID is unchanged, so the parent's PID file still names it.
  - Both write their PID files atomically.
- **`DESCENDANT` (approvals), `ESCAPED` (r13 summary) and the probe's grandchild** are test-authored Python. Each puts tests/fakes on its `sys.path` and uses `fork_held` or `spawn`.
  - `DESCENDANT` and `ESCAPED` fork with `fork_held`.
  - `ESCAPED`'s child still calls `os.setsid` and writes its PID file before exec'ing the sleeper.
  - The probe's grandchild is started with `spawn`.
- **The r13 summary scripts at :160 and :354.** `sleep 30 &` and `(sleep 60) &` become `"$PY" -I "$SLEEPER" "$DIR" &` and `("$PY" -I "$SLEEPER" "$DIR") &`, so `$!` and `$$` still name the same processes the tests read back.

Each site takes `escapes`, passes its directory, and drops its own `finally` kill or `kill_quietly`. The exception is `orphan.pid`, as noted in the survey. Every existing assertion stays the same: the sleeper keeps the descendant's PID, group, session and open output, and stays alive until teardown, so `alive` and `gone` checks in the test body hold as before. The fixture's teardown runs after the test body, whether it passed, failed or errored.

## Edge cases

- **A sleeper that can't poll.** A sleeper can be stopped (SIGSTOP), frozen (cgroup freezer), or kept off the CPU. If so, it still holds the census, so `end` can't take it exclusively and raises at its bound, naming the PIDs under `pids/`. It never waits silently. The bound is a monotonic deadline over non-blocking lock attempts, so the wait itself can't hang.
  The code under test can't block a sleeper any other way: a sleeper never reads its stdin and never writes its stdout or stderr. Its one write is the single ready byte, to a fresh pipe of its own. (The code under test may close the output the sleeper holds open; the sleeper never touches it.)
  The run-end check reports the same way.
- **Inheritance across fork, exec and setsid.**
  - The census lock is shared through the inherited open file description: fork shares it, exec keeps the fd (it is inheritable until the sleeper marks it close-on-exec, after the exec), and setsid doesn't touch open files.
  - The lifeline is never inherited. The fixture's fd is O_CLOEXEC, and each sleeper opens the lifeline for itself, only ever trying a shared lock. So no child can hold the lifeline on the fixture's behalf.
  - The sleeper's own census fd is close-on-exec, and the sleeper starts nothing. So nothing beyond the sleeper can extend the census.
  - A Popen spawner passes only the census fd (`pass_fds`), so the code under test's other processes never get it. A fork spawner's child execs at once, so the fd isn't held by anything but the sleeper.
- **The census proof is complete.** Every Python-spawned sleeper holds the census from the moment it exists ("Spawning"). The two shell sites are the exception: the r13 summary at :160 and :354, where `sh` starts the sleeper as a background job and summarize's `reap.spawn` closes every inherited fd. Each of their sleepers takes the census as its first action. A sleeper there that hasn't yet done so when `end` runs is not counted, but it doesn't wait either: its first action finds the census held exclusively, or gone, and it exits. So `end` proves this: no sleeper of the use is alive, except one that hasn't yet run its first step, and that one exits as soon as it does. Both shell tests run their script for at least 1 s before teardown, so in practice the case doesn't arise. The design still doesn't rely on that.
- **Lock files** live only in the use directory, inside the private per-run directory.

## How it is proven

- **No sleeper outlives its test while pytest lives.** `end` takes the census exclusively, or raises. A free lock proves no process holds the census. Every Python-spawned sleeper holds it from before it exists until it exits. A shell-spawned sleeper holds it from its first step, and if it was late, it exits at that step (see "Edge cases").
- **No sleeper outlives its pytest process.** Its lifeline lock goes with the process, however that process ends, and the sleeper's next poll then exits it. The 60 s cap is a backstop and is never part of the proof.
- **Cleanup touches no other process.** It signals nothing. Only the sleepers themselves act, by exiting.
- **Every claim has a positive control.** Each test below first shows that a sleeper did start (`started()` names it, or the census is held), then shows that it ended. That way a sleeper that never started can't pass as cleaned up.

## Tests (new file: tests/test_escapes.py)

1. **Release ends a sleeper.** A sleeper is started in a session of its own (the test's own `Popen` handle). Before `end`, `started()` names it and the census can't be taken. After `end`, the handle's return code is 0 within the bound, and the use directory is gone.
2. **A sleeper with no PID file still ends** (the original bug), run on every spawn path:
   - fake_agent_cli `escaped` and `orphan`;
   - fake_approve_bead `hold_stdout` and `descendant`;
   - `DESCENDANT`, `ESCAPED` and the probe grandchild.
   For each, the PID publication is broken: its path is in a directory that doesn't exist, so the parent fails after the spawn or fork and before the write. The test shows `started()` names a PID, then that `end()` succeeds. For the fork paths, this is the regression for finding 3.
3. **A killed holder.** A small Python harness process, the test's own child, holds an `Escapes` use's lifeline and starts an escaped sleeper. The test SIGKILLs the harness by its handle. Within the bound, the test can take the census itself.
4. **A stuck sleeper is reported, never signalled.** The test starts a sleeper by its own handle and SIGSTOPs it. `end(bound=0.5)` raises within 1.5 s and names the sleeper's PID. The sleeper is still there, stopped. The test then sends SIGCONT, and the sleeper exits 0 on its own.
5a. **The census is held before the child runs.** Right after `spawn` returns, and right after `fork_held` returns in the parent, the test's attempt at `LOCK_EX | LOCK_NB` on the census fails. That holds before the child could have run, and with the child SIGSTOPped by the test's own handle the moment it is spawned. Covered for `new_session` too. After the spawner's copy is closed, the lock is still held. Once the child exits, it is free.
5. **A late sleeper never waits.** A sleeper started after `end`, on the removed directory, exits 0 within 2 s. So does one started while the census is held exclusively.
6. **Teardown after a failure.** A child pytest run, in the style of test_tmux_watchdog.py's `Child`, has one test that starts an escaped sleeper and then fails. Its report shows the test failed and teardown passed, and no census of its run directory is still held.
7. **A use left without teardown fails the run.** In a child pytest run, a test makes an `Escapes` use without the fixture, starts a sleeper, and passes. The run-end check ends the sleeper, reports the use, and the run exits non-zero.
8. **A missing directory spawns nothing.** Each spawn path in test 2 runs under a harness that installs a `sys.addaudithook` recording `subprocess.Popen`, `os.fork`, `os.posix_spawn` and `os.exec` events, and then runs the fake or script in-process.
   - With `HZ_ESCAPE_DIR` unset, the hook records no spawn, and the path exits with its stated error.
   - With the directory set, it records exactly the one spawn: the positive control.
   This observes that no spawn happens, rather than scanning for a process that would carry no mark (r1 finding 6).
9. **No signal is sent.** Tests 1 and 4 run `end` inside that audit harness, and it records no `os.kill` or `os.killpg` event. `signal.pidfd_send_signal` has no audit event (checked on 3.12), so the harness also wraps it, along with `os.kill` and `os.killpg`, and asserts that none of them is called.
10. **The existing sites still pass with their assertions unchanged.** These are test_agents_usage.py T5 (both faults), the hostread escape tests, the approvals descendant and held-stdout tests, r13 summary's `run_escaped` tests and its tests at :160 and :354, and the probe test at :642.

All of these tests are offline. The child pytest runs and the harnesses use `short_dir` bases. Each process a test starts by its own handle is waited for, or killed by that handle, before the test returns.

## Out of scope

- tests/live.
- tmux-pane and watchdog processes, which the tmux guard already marks and sweeps.
- The fakes' own processes and long waits, which the code under test reaps and the tests assert are gone.
- The self-bounded `orphan` writer in fake_approve_bead.
- Any change to `reap.py`, hostread, codex_usage, summarize or the probes.
