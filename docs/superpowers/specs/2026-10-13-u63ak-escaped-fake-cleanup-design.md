# btq-u63ak: escaped test fakes end themselves, and each test proves it

Bead: btq-u63ak. Base: main 093ede5. Test-only: nothing under `src/` changes, and no ADR is touched.

## Revisions

- **r2 (after Codex design r1).**
  - Cleanup no longer runs `ps`, matches argv marks or sends signals.
  - Each long-lived descendant a test starts becomes one sleeper. The sleeper ends itself once the test releases a lifeline lock.
  - A census lock proves that none is left.
  - Because no signal is sent, PID reuse can't make cleanup hit another process on any OS.
- **r3 (after Codex design r2).**
  - **Admission:** every sleeper, including the shell-started ones, now holds the census from the instant it exists, through a lock its spawner takes and hands down. No sleeper has to admit itself.
  - **PID files:** a sleeper's own PID file is written after admission, and failing to write it is harmless.
  - **Spawn proof:** each spawn path has a stated harness boundary and an expected event sequence.
  - **Wording:** stopped survivors are stated as detected failures, the timing claims are narrowed, and readiness is synchronised by pipe.

## Problem

`test_a_descendant_holding_the_output_open_never_holds_the_read[escaped]` (tests/test_agents_usage.py) runs the fake app-server with `HZ_FAKE_RATE_FAULT=escaped`. The fake starts a `time.sleep(600)` descendant in a session of its own, then writes its PID to `HZ_FAKE_CHILD_PID_FILE`. The test's `finally` kills that PID only `if child.exists()`. On 2026-10-13, one such sleeper was found alive as PID 2248827 after its run had ended.

Three causes are shared, in some combination, by every test that spawns an escaped or long-lived descendant:

1. **The PID file is written after the spawn.** The fake runs `Popen` and then writes the file (fake_agent_cli.py:117-119). The read's 1 s limit kills the app-server's group, and that kill can land between the two steps. The escaped child then lives on with no PID file, and cleanup gated on `exists()` does nothing.
2. **The PID file isn't written atomically.** `write_text` truncates and then writes, so a read in between sees an empty file. `int("")` then raises in the `finally`, or a suppressed ValueError silently skips the kill.
3. **Cleanup misses some paths.** In the `orphan` case, the `finally` kills nothing. Other tests rely only on the production code's group kill. If that cleanup regresses, the assertion fails and the sleeper lives on.

A PID file can only name a process that got as far as writing it. Cleanup that starts from a PID file inherits cause 1, and cleanup that signals a PID found earlier has to defend against that PID being reused.

## Survey

These are the offline tests whose test-authored descendants can outlive the test. tests/live is out of scope because it isn't run offline.

| Site | Descendant | Spawned by | Life | Cleanup today | Causes |
|---|---|---|---|---|---|
| test_agents_usage.py:332 `[escaped]`, via fake_agent_cli.py:116 | own session | Popen | 600 s | kill if the PID file exists | 1, 2 |
| test_agents_usage.py:332 `[orphan]`, same fake | app-server's group | Popen | 600 s | none | 1, 2, 3 |
| test_sandbox_hostread.py:480 and :497, same fake | own session | Popen | 600 s | kill by PID file; missing or torn file suppressed | 1, 2 |
| test_admind_approvals.py:510 and :539, via fake_approve_bead.py `hold_stdout` | own session (`os.setsid`) | Popen | 30 s | `kill_quietly`: kill if the file exists | 1, 2 |
| test_admind_approvals.py:290, :314, :349, :2038, via fake_approve_bead.py `descendant` | fake's group | fork | 60 s | `kill_quietly` | 1 |
| test_admind_approvals.py:383, :407, :425, via the `DESCENDANT` script | script's group | fork | 60 s | `kill_quietly` | 1 |
| test_admind_r13_summary.py `run_escaped` (`ESCAPED`, :385) | own session (`os.setsid`, then `exec sleep 20`) | sh `&` | 20 s | kill if the file exists | 1, 2 |
| test_admind_r13_summary.py:160 `sleep 30 &` | script's group | sh `&` | 30 s | none: relies on summarize's group kill | 3 |
| test_admind_r13_summary.py:354 `(sleep 60) &` | script's group | sh `&` | 60 s | none: relies on summarize's group kill | 3 |
| test_sandbox_probes.py:642, a grandchild of the captured program | program's group | Popen | 60 s | kill if the file is readable | 1 |

These are left out, each for a reason:
- **fake_approve_bead.py:237 (`orphan`).** Its forked grandchild sleeps 1 s, writes one decision and exits by itself. Its bound is its own code, not a cleanup. Its `kill_quietly(orphan.pid)` at test_admind_approvals.py:576 stays. Every other `kill_quietly` call is replaced.
- **test_tmux_launch_lock.py:431.** The PID is read from stdout before the `try`, and the `finally` kills it unconditionally. It's the test's own process.
- **tmux-pane processes and the watchdog's child pytest runs.** The tmux guard's watchdog marks and sweeps these, and asserts that it did.
- **The fakes' own long waits** (fake_agent_cli `hang`; fake_approve_bead `hang` and the `time.sleep(600)` after `descendant`). These processes are the code under test's direct children, and their lifetime is the behaviour under test. The code under test's group kill ends them, and the tests assert they're gone.

## Design

The design has three files:
- **tests/fakes/sleeper.py:** a standalone stdlib module and script, with no imports from tests, safe under `python -I` and a scrubbed environment.
- **tests/escapes.py:** the fixture.
- **tests/conftest.py:** registers the fixture as a plugin.

### Locks and their files

Each use of the fixture gets a directory `<run-dir>/<n>` holding three things:
- **`lifeline`.** The fixture holds `LOCK_EX` on it for the whole test, through an O_CLOEXEC fd that only the fixture holds. A sleeper polls it with `LOCK_SH | LOCK_NB` and exits once that succeeds.
- **`census`.** Every sleeper holds `LOCK_SH` on it for its whole life, through an open file description it got from its spawner (see "Admission"). Teardown proves that none is left by taking `LOCK_EX`.
- **`pids/`.** Each sleeper creates `pids/<pid>` after it starts. This is for reports and positive controls only: the proof never reads it.

flock locks belong to the open file description, on Linux and macOS alike. Every fd that refers to the description, in any process, through fork or exec, shares the lock. The lock is released when the last such fd is closed. That is what makes handing a lock down possible. It also creates a duty: anything holding a census fd holds the census. That is the descriptor-ownership invariant set out under "Admission".

**Location.**
- The run directory is `tempfile.mkdtemp(prefix="hze")`, made once per pytest process. That makes it private (mode 0700), uniquely named and this process's alone, and each xdist worker gets its own.
- No lock file sits at a fixed or shared path, and nothing outside the run opens them.
- It deliberately isn't under `tmp_path`, which pytest may remove first: a sleeper must find its own census or none, never another one.

### The sleeper

The sleeper is run as `[sys.executable, "-I", SLEEPER, <use-dir>, "--census-fd", N, *opts]`. Its options:
- `--new-session`: call `os.setsid()` first.
- `--ready-fd M`: write one byte once admitted.
- `--pid-file P`: publish its own PID.

`--census-fd` is required, so a sleeper can't start unadmitted. Its steps:

1. **Check the census fd.** It checks with `fstat` that fd N is the `census` of `<use-dir>`, by comparing device and inode, then marks fd N close-on-exec. If the check fails, it exits 1.
2. **Start a session if asked.** If `--new-session` is given, it calls `os.setsid()`. setsid changes the session and the group, not the open files, so the census lock is untouched.
3. **Report.** It creates `pids/<pid>`, then writes its PID file if asked: a temp file, then `os.replace`. A failure in either is ignored, because the sleeper is already admitted and still ends on the lifeline. It then writes the ready byte, if asked.
4. **Poll the lifeline.** It opens `<use-dir>/lifeline` with O_RDONLY|O_CLOEXEC and, every 50 ms, tries `flock(LOCK_SH | LOCK_NB)`. Once that succeeds, or the open fails, it exits 0. It also exits 0 after 60 s regardless. That cap is a backstop for a runnable sleeper; no proof relies on it.

The sleeper starts nothing, and it never reads its stdin or writes its stdout or stderr. Its only writes are the report files and the one ready byte, to a fresh pipe of its own. So the code under test can't block it, except by stopping or freezing it (see "Survivors").

### Admission: the census is held before the sleeper exists

A sleeper never takes the census itself. Its spawner takes `LOCK_SH | LOCK_NB` on a census fd before the spawn, and the sleeper inherits that open file description. The sleeper therefore holds the census from the instant the process exists: before it is ever scheduled, before its interpreter starts, and through any stop. The spawner's checks also run before the spawn. If the use directory is unset, missing, or its census can't be locked shared (teardown has begun), the spawner raises or exits, and nothing is spawned. There are three spawners:

- **`spawn(use_dir, *, new_session=False, **popen) -> Popen`.** Used by fake_agent_cli, `hold_stdout` and the probe grandchild.
  1. It opens the census as an inheritable fd and takes `LOCK_SH | LOCK_NB`.
  2. It runs `Popen(sleeper argv with --census-fd fd, pass_fds=(fd, *the caller's))`.
  3. It closes its own copy in a `finally`.
- **`fork_held(use_dir) -> int`.** Used by fake_approve_bead `descendant` and `DESCENDANT`.
  1. It takes the same lock, then calls `os.fork()`.
  2. The child returns 0. The caller redirects the child's output and execs the sleeper argv with `--census-fd fd`.
  3. The parent closes its copy and returns the child's PID.
- **The shell prelude `escapes.shell_prelude(dir)`.** Used by `ESCAPED` and the r13 summary scripts at :160 and :354, which are run by summarize through `reap.spawn`. `reap.spawn` closes every inherited fd, so the script opens its own. It's text placed at the top of each script:
  ```sh
  exec 9<"<dir>/census"
  "<python>" -I "<SLEEPER>" --take 9 || exit 97     # LOCK_SH|LOCK_NB on fd 9, then exit 0
  ```
  `--take` locks the shell's own open file description and then exits. The lock stays, because sh still holds fd 9. Every later `&` child inherits fd 9, and so the lock, at fork. The script then closes fd 9 (`exec 9<&-`) right after its last sleeper spawn, so later commands don't hold the census. The sleeper line is `"<python>" -I "<SLEEPER>" "<dir>" --census-fd 9 [...] &`. This uses test code only: `reap.spawn` and summarize don't change. Checked on Linux: the lock is held while the background child lives after sh closes fd 9, and released when the child exits.

**Descriptor ownership is an invariant.** A census fd exists only in three places:
- in a spawner, from taking the lock until it closes its copy right after the one spawn;
- in the sleeper, which marks it close-on-exec and starts nothing;
- in the shell prelude's sh, until `exec 9<&-`.

Within those windows, the spawner starts no other process. The fakes are single-threaded, and Popen passes only the fds in `pass_fds`. A fork child does inherit every fd, O_CLOEXEC ones included, until it execs. That is why `fork_held`'s child execs at once and does nothing else first. The lifeline is never inherited by a sleeper: the fixture's fd is O_CLOEXEC and is held only by the fixture's process. The one way to inherit it is a fork of the pytest process without exec, and no test does that; the fixture's docstring says so.

### Teardown: `end(bound)`

1. Close the lifeline fd. This releases the lifeline.
2. Every 50 ms, try `flock(LOCK_EX | LOCK_NB)` on the census, until it is taken or `bound` seconds (default 10) of monotonic time have passed.
3. If it is taken, every census fd has been closed. Every sleeper held one from the instant it existed, so every sleeper of this use has exited, and so has every spawner window. A sleeper that has exited may still be a zombie until its parent, or init, reaps it. That is its parent's job: the census proves exit, not reaping. `end` then removes the use directory while still holding the lock, and closes it.
4. If the bound passes first, `end` raises AssertionError naming the PIDs under `pids/`. The test then errors in teardown.

`end` never signals anything. Each step is a non-blocking lock attempt or a file operation in a local private directory, so `end` can't block on another process. It returns when the census is free, or raises once the bound has passed. Scheduling and the filesystem can delay a step, so the bound is a budget, not a hard wall-clock guarantee.

`pytest_sessionfinish` runs `end` on every use directory still present (a use whose teardown never ran), reports each one, fails the session, and removes the run directory.

### Survivors

A sleeper that is stopped (SIGSTOP), frozen (cgroup freezer) or never scheduled keeps its census lock. The 60 s cap can't run while it is stopped. That is a detected failure, not completed cleanup:
- `end` raises at its bound, naming the PID;
- the run-end check fails the session;
- the sleeper is left exactly as it is.

Handling a survivor is a human's job. The operator reads the PID from the report, confirms it is still a sleeper (its command line names tests/fakes/sleeper.py and the reported use directory), and kills it. Nothing in the helper ever does that.

If the pytest process dies (SIGKILL, or an xdist worker crash), the kernel releases its lifeline lock, and each runnable sleeper exits at its next poll. No report is made in that case. A sleeper that is stopped when pytest dies survives, and nothing reports it. This is the one case the design can't detect.

### The fakes and scripts

- **fake_agent_cli.py (`orphan`, `escaped`).** It reads the use directory from `HZ_ESCAPE_DIR`, as it reads its `HZ_FAKE_*` settings, which the tests already pass through codex_usage and hostread. It calls `spawn(dir, new_session=fault == "escaped")`, then writes `HZ_FAKE_CHILD_PID_FILE` atomically, as now (parent publication).
- **fake_approve_bead.py.** `approve_bead_wrapper(tmp_path, escape_dir=...)` (tests/admind_asks_fixture.py) puts `HZ_ESCAPE_DIR=<dir>` in the wrapper beside `FAKE_BTQ_DB`. That's an env setting, so the fake's argv, its argument parsing and the logged calls don't change.
  - `hold_stdout` calls `spawn(dir, new_session=True)` with `--ready-fd`, and the parent then writes `held.pid` atomically.
  - `descendant` calls `fork_held(dir)`. The child redirects its output and execs the sleeper. The parent writes `descendant.pid` atomically.
- **`DESCENDANT` (approvals).** It adds tests/fakes to `sys.path`, calls `fork_held(sys.argv[2])`, and the parent publishes the PID as now.
- **`ESCAPED` (r13 summary).** The prelude goes at the top. The background job is `"<python>" -I -c 'import os, sys; os.setsid(); os.execv(...)' &`, where the exec'd command is the sleeper with `--census-fd 9 --pid-file <pidfile>`. The existing shell loop, `while [ ! -s pidfile ]`, is kept. So the PID is now published by the admitted sleeper itself, after admission and after setsid (self-publication).
- **r13 summary :160 and :354.** These take the prelude too.
  - `sleep 30 &` becomes `"<python>" -I "<SLEEPER>" "<dir>" --census-fd 9 &`, and `(sleep 60) &` becomes the same line in a subshell.
  - `$!` and `$$` still name the processes the tests read back.
  - `exec 9<&-` follows the spawn, so `python3 -c "$FILL"` at :354 doesn't hold the census.
- **The probe grandchild (test_sandbox_probes.py:642).** The captured program adds tests/fakes to `sys.path`, calls `spawn(dir)`, and publishes the PID as now.

Each site takes `escapes`, passes its directory, and drops its own `finally` kill or `kill_quietly`. The exception is `orphan.pid`, as noted in the survey. The sleeper keeps the descendant's PID, group, session, open output and life until teardown, so every `alive` and `gone` assertion in the test bodies is left as it is. The implementation will show that, by the existing tests passing with unchanged assertions (test 11). The fixture's teardown runs after the test body, whether it passed, failed or errored.

## How it is proven

- **No sleeper outlives its test unreported while pytest lives.** Every sleeper holds the census from the instant it exists. So `end` takes the census exclusively only once every sleeper has exited. Otherwise it raises, naming them.
- **No runnable sleeper outlives its pytest process.** The lifeline lock goes with the process, however that process ends, and a runnable sleeper's next poll exits it. The 60 s cap is a backstop only. A stopped sleeper is the stated exception (see "Survivors").
- **Cleanup touches no process.** It signals nothing. Sleepers end by exiting themselves, and a survivor is left for the operator.
- **Every claim has a positive control.** Each test shows that the sleeper exists (`pids/` names it, or the census is held) before it shows the end. A sleeper that never started can't pass as one that was cleaned up.

## Tests (new file: tests/test_escapes.py)

The readiness waits use the ready pipe or the handle. Each wait has a hang guard, and that guard is the normal cleanup budget (10 s), not a timing claim.

1. **Release ends a sleeper.** A sleeper is started with `spawn(new_session=True)`, the test's own handle.
   - Before `end`, `pids/` names it and `LOCK_EX | LOCK_NB` on the census fails.
   - After `end`, the handle's return code is 0, and the use directory is gone.
2. **Admission happens before the child runs (Python paths).**
   - `spawn`: the test SIGSTOPs the child by its handle as soon as `spawn` returns, so before it can open or lock anything. The census `LOCK_EX | LOCK_NB` then fails, and `end(bound=0.5)` raises naming its PID (the stopped one: see "Survivors"). The test then SIGCONTs it by its handle, and `end()` succeeds.
   - `fork_held`: the same, with the stop sent as soon as the parent's `fork_held` returns, so before the child's exec.
   - These replace r2's "pause before open" and "pause between open and lock" cases. Those windows no longer exist, because the sleeper never opens or locks the census; the test shows the lock held while the child has run nothing.
3. **Admission happens before the child runs (shell paths).** A prelude script is run with `sh` by the test's own handle, and its sleeper line starts `"<python>" -I -c 'import os, signal; os.kill(os.getpid(), signal.SIGSTOP); ...'`, which stops itself before running the sleeper. While it is stopped, the census `LOCK_EX | LOCK_NB` fails. The test then SIGCONTs it by its PID, which sh printed: the test's own grandchild, still stopped, so its PID can't have been reused. The sleeper then runs and `end()` succeeds. The case where the prelude's `--take` fails (census held exclusively by the test) is also covered: the script exits 97, and its `set -x` trace shows no line after the prelude.
4. **A missing PID file still ends the sleeper: parent publication.** This covers fake_agent_cli `escaped` and `orphan`, fake_approve_bead `hold_stdout` and `descendant`, `DESCENDANT` and the probe grandchild. In each case the PID file's path is in a directory that doesn't exist. The spawn or fork succeeds, then the parent's publication fails and the parent exits. Each case asserts that `pids/` names an admitted, live sleeper, that no PID file exists, and that `end()` succeeds.
5. **A missing PID file still ends the sleeper: self-publication (`ESCAPED`).** `--pid-file` points into a directory that doesn't exist. The admitted sleeper fails to publish, ignores the failure and keeps polling. `pids/` names it, the PID file is absent, and `end()` succeeds. The shell's loop is run with a hang guard and stopped by the test's own handle.
6. **Failing before the sleeper starts spawns nothing.** This is distinct from tests 4 and 5. The use directory is unset, its census doesn't exist, or the census is held exclusively. Each spawner must raise (or exit 97) before spawning. This is observed per path:

| Path | Harness boundary | With a valid directory | Refused |
|---|---|---|---|
| `spawn` (fake_agent_cli, `hold_stdout`, probe grandchild) | the fake's mode function, or the probe's code, run in-process under `sys.addaudithook` | exactly one `subprocess.Popen`, whose argv is the sleeper | no `subprocess.Popen`, `os.fork`, `os.posix_spawn` or `os.exec` event; the stated error |
| `fork_held` (`descendant`, `DESCENDANT`) | the same, with the hook appending `(pid, event)` to a file. Hooks survive fork, so the child's events are recorded too | the parent: `os.fork`; the child: `os.exec` of the sleeper | no event at all: `fork_held` checks and locks before `os.fork` |
| shell prelude (`ESCAPED`, :160, :354) | the script run by `sh -x` (the test's own handle), with its trace written to a file. A Python hook can't see sh's spawns | the trace shows the `--take`, then the sleeper's `&` line | the trace ends at `--take`, and the script exits 97. `ESCAPED`'s `-c` part is also run in-process under the hook: exactly one `os.exec` of the sleeper |

7. **Descriptor inheritance.**
   - A child forked from a process that holds the census, before it execs, keeps the census held after the parent closes its copy. It is released only when the child exits.
   - A Popen child of a spawner gets only the census fd (shown with `/proc/self/fd` on Linux, and by `fcntl(F_GETFD)` on each other fd up to the soft limit on macOS).
   - The fixture's lifeline isn't inherited by a Popen child of the test: the lifeline is released while that child is still alive, and a sleeper exits.
8. **A killed holder.** A harness process, the test's own child, makes an `Escapes` use and starts a runnable sleeper. The test SIGKILLs the harness by its handle. The sleeper then exits, which the test shows by taking the census `LOCK_EX` itself, polling within the cleanup budget.
9. **A late sleeper never waits.** A spawner called after `end` (the directory has been removed) raises before spawning. A sleeper exec'd by hand with a stale `--census-fd` that doesn't match exits 1, which the test sees through its handle. Neither writes the ready byte.
10. **The fixture's own life.**
    - **After a failure:** a child pytest run, in the style of test_tmux_watchdog.py's `Child`, has a test that starts an escaped sleeper and fails. Its report shows the test failed and teardown passed, and the run directory's census is free.
    - **Without teardown:** a test makes a use without the fixture, starts a sleeper and passes. The run-end check ends the sleeper, reports the use, and the run exits non-zero.
    - **No signal is sent:** `end` runs under the audit harness. It records no `os.kill` or `os.killpg` event. `signal.pidfd_send_signal` has no audit event (checked on 3.12), so the harness also wraps it, `os.kill` and `os.killpg`, and asserts that none is called.
11. **The existing sites pass with their assertions unchanged.** These are:
    - test_agents_usage.py T5 (both faults);
    - the hostread escape tests;
    - the approvals descendant and held-stdout tests;
    - r13 summary's `run_escaped` tests and its tests at :160 and :354;
    - the probe test at :642.

All of these tests are offline. The child pytest runs and harnesses use `short_dir` bases. A test that stops a process resumes it, and every process a test starts by its own handle is waited for before the test returns.

## Out of scope

- tests/live.
- The tmux-pane and watchdog processes.
- The fakes' own processes and long waits.
- The self-bounded `orphan` writer.
- Any change to `reap.py`, hostread, codex_usage, summarize or the probes.
- macOS: the design relies only on flock semantics both systems document. The implementation's CI run on laptop-ci is where macOS is checked.
