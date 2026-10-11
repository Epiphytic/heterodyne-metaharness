# Test suite efficiency review

This review covers the offline suite (`uv run pytest`, which CI runs), as of `origin/main` e07e0a2: 3213 tests. `tests/live` (HZ_LIVE=1) is out of scope. Every number here comes from the shared Linux development host (32 CPUs), which was busy and had a nearly full nvme disk. The serial numbers come from a single run each, and the parallel ones were steady over repeated runs. Expect serial numbers to move by tens of percent from run to run.

## Which command when

| Command | Runs | Time (parallel, the default) | Time (serial, `-- -n 0`) | Use it |
|---|---|---|---|---|
| `scripts/test-full` | the whole suite (CI runs the same tests, serially) | about 25 s | about 7 min | **by default**: while you iterate, before you hand over a review range, before merging |
| `scripts/test-fast` | every test except the 14 tmux and wsd functions in `tests/slow_tests.txt`; every safety test | about 24 s | about 6.5 min (estimated) | serially, or on a busy machine with a few workers |
| `scripts/test-changed [--base REF]` | `test-fast`, plus the slow tests of the files your change reaches; the full suite when the change can't be mapped | about 25 s | 6.5 to 7 min | the same |
| `uv run pytest tests/test_x.py` | one file, serially | seconds | | while you debug that file |

- **Use `scripts/test-full`.** In parallel it takes 25 s, and the abbreviated tiers save almost nothing on top of that: safety tests always run, and they are 83% of the suite's time (section 2a). The abbreviated tiers save about 10% in a serial run.
- The scripts run with pytest-xdist (`-n auto --dist worksteal`, one worker per CPU). The machine is shared, so use `-- -n 8` when other suites are running, and still run only one full suite at a time.
- All three scripts accept pytest arguments after `--` (for example, `scripts/test-fast -- -x`).
- When a run fails, the scripts run the failing files again in full, serially and with verbose output (`-rfE -v`), slow tests included. A broad failure then comes back with the narrower tests of the same area next to it. `--no-diagnose` turns this off. The rerun never changes the exit code.
- **CI still runs the full suite on every push and pull request.** The tiers are a way to iterate locally. They don't replace CI.
- After a large change to test times, regenerate the slow list from a timed full run:

  ```sh
  uv run pytest -q --junitxml=/tmp/hz-full.xml        # serial: parallel times include contention
  uv run python scripts/run_tiers.py slow-list /tmp/hz-full.xml
  ```

  The run sorts by time, but the slow list stays data in the repository. A stale entry only means that a test runs in the full tier alone. A new slow test stays in the fast tier until the list is regenerated.

## 1. Where the time went

Baseline, serial, `--durations=0`: 3176 passed, 36 skipped, 1 failed (see section 5), in **28 min 03 s of wall time**. CPU was only 97 s user and 46 s sys. **The suite was waiting, not computing.** Collection took 0.5 s, setup 7 s and teardown 12 s in total. 437 tests took 2 s or more each, 1405 s between them.

The time went to the admind Harness tests (a real tmux server, a fake claude running in a pane, and a `FakeWnAgent` on a Unix socket) and to the wsd tests that commit SQLite often:

| File | Baseline (s) | After the fixes below (s) |
|---|---|---|
| test_admind_reactions.py | 306.5 | 64.3 |
| test_admind_approvals.py | 218.4 | 51.7 |
| test_admind_asks_bump.py | 199.2 | 39.4 |
| test_admind_r13_replies.py | 134.4 | 30.1 |
| test_admind_daemon.py | 123.1 | 48.7 |
| test_admind_r13_membership.py | 105.8 | 25.9 |
| test_admind_r13_details.py | 99.7 | 12.7 |
| test_wsd_defer.py | 98.5 | 20.7 |
| test_admind_asks.py | 53.6 | 10.8 |
| test_sandbox_runtime.py | 40.1 | 37.0 |

To find out what the slow tests were waiting on, I sampled thread and asyncio-task stacks during runs of single files. Two causes accounted for most of the time.

1. **`FakeWnAgent.stop()` waited 2 s every time.** The `subscribe` handler blocks on `queue.get()` forever, so `server.wait_closed()` never finished, and `wait_for(..., 2)` always ran into its timeout. That cost 2 s of teardown in every Harness test, more than half the time of most admind files. **Fix:** `stop()` cancels its open connection handlers before waiting. Commit d789f3c, in `tests/fakes/fake_wn_agent.py`. test_admind_reactions.py alone went from 306 s to 102 s.
2. **fsync on a busy disk.** The wsd journal and the admind store use SQLite in WAL mode with the default `synchronous`, which fsyncs on every commit. pytest's base temp is under `/tmp`, which here sits on a busy, nearly full nvme disk. test_wsd_defer.py took 125 s on disk and 17.5 s with `--basetemp` on `/dev/shm`. **Fix:** on Linux, `tests/conftest.py` puts the base temp on a private directory under `/dev/shm` when that is a writable tmpfs. The directory is removed at exit. The fsyncs still happen; they are just cheap. `HZ_TEST_DISK_TMP=1` or an explicit `--basetemp` restores the old behaviour, for example to keep a failing test's files. macOS keeps its short `/tmp` base (for the 104-byte socket path limit). Commit e8bdc5f.

Neither fix changes what a test asserts, and neither changes production code.

**Full suite after both fixes: 3178 passed, 36 skipped, 0 failed, in 7 min 05 s of wall time (424 s of test time).** That is 4 times faster than before.

**With pytest-xdist (`-n auto --dist worksteal`, 32 workers): 3220 passed, 36 skipped in 25 s of wall time,** in three runs out of three. That is 67 times faster than the baseline. With the default `--dist load` it took 44 s (section 2d). Most of what remains is the slowest single tests (10.5 s, 6.6 s, 6.1 s, ...), each on its own worker.

### What is left

These are the remaining costs, largest first. None is fixed in this PR.

- **`Tmux.paste` sleeps 0.3 s** between `paste-buffer` and `Enter` (src/heterodyne/tmux.py, `time.sleep(0.3)`). 370 tests reach that line, many of them more than once, so it costs **at least 110 s of the 424 s**. This is a production constant: it gives the pane's application time to read the paste. Two ways to shorten it in tests: make the delay a `Tmux` attribute, which the Harness sets to something like 0.02 s against the fake claude (the fake reads its input synchronously); or have the fake signal that it has read the paste. Either way it needs an owner's decision, since it touches the code the live agent depends on. **Recommended as the next step.** Its effect is about the same as a 25% faster suite.
- **Agent start and the ready notice:** about 0.6 s per Harness test (tmux `new-session`, the fake starting up, then the hook round-trip). Sharing one Harness per module would remove most of it. But the tests rely on a fresh store, a fresh audit log and a fresh latch state, so that would weaken isolation. Not recommended.
- **Fixed `asyncio.sleep` calls in test_admind_daemon.py**, about 13 s in total (1.5, 1.5, 1.2, 1.0, 1.0, 0.8 and several 0.5). Most of them prove a negative ("nothing was posted while ..."), which a fixed wait is the honest way to test. Where a test then waits for a positive event, it could wait on the event instead. That is worth doing case by case, not as a sweep.
- **test_wsd_daemon.py::test_shutdown_closes_every_lane_before_any_starts_a_queued_job** takes 10.6 s, the slowest single test. It waits out real lane timeouts. An injectable clock in the lane worker would make it take milliseconds. That is a small change to production code, for its owner to decide.
- **test_sandbox_runtime.py** (37 s) launches real sandboxed processes and is a safety file. It stays in every tier. Speeding it up would mean sharing a prepared sandbox root between cases, which is possible but not attempted here.

## 2. Tiers (this PR)

### (a) The fast tier

`tests/slow_tests.txt` lists the test functions that `scripts/test-fast` leaves out. `tests/tier_marks.py`, loaded from `tests/conftest.py`, marks them `slow`, and `scripts/test-fast` runs `-m "not slow"`. The list is generated from a timed run: every function with a case of 0.5 s or more, except safety tests.

**Safety is the default.**

- Only the files that `SLOW_ELIGIBLE` matches (`test_tmux*` and `test_wsd_*`: tmux and wsd mechanics) may have slow tests.
- Every other test file is a safety file, a new one included, and its tests run in every tier, whatever the list says. The safety files cover admind auth, operators, redaction, latches, digests, approvals, sandbox, policy and so on.
- Inside an eligible file, a test whose name matches `SAFETY_WORDS` (redact, secret, sandbox, latch, digest, polic, leak, npub, nsec, token, auth, isolat, guard, pin) is safety too.
- Collection marks every safety test `safety`. It fails with a usage error, rather than quietly dropping the test from the fast tier, if the list names a safety test or anything marks one `slow`.
- `tests/test_tiers.py` loads the real list and collects the real suite. It checks that `-m "not slow"` collects every safety test, by name the three that an earlier, name-based version of this guard missed:
  - `test_a_hex_value_in_a_reply_never_reaches_the_chat` (redaction);
  - `test_a_stranger_is_dropped` (authorisation);
  - `test_a_revoked_operators_queued_message_is_not_acted_on_after_a_rearm` (latch).

Under this rule the list holds 14 functions (44 cases), about 40 s of the 422 s of serial test time. **Safety tests take 351 s of it (83%),** so a fast tier that keeps them all can't be much faster than the full suite. In parallel, the fast tier took 24 s and the full suite 25 s; serially it saves about 10%.

The honest conclusion is that tiering doesn't pay in this suite. The speed came from making every test cheaper (section 1) and from running them in parallel (section 2d). The tiers stay, because they cost nothing, and they will matter if the eligible files grow.

### (b) Change-based selection

`scripts/select_tests.py` takes the files from `git diff --name-only --no-renames <base>...HEAD`, plus staged, unstaged and untracked files. It maps them to test files through an AST dependency graph of `src/heterodyne` and `tests/`. The graph includes:

- transitive imports, including package `__init__` files;
- heterodyne module names that appear in strings (`-m heterodyne.x`, `monkeypatch.setattr("heterodyne.x.y", ...)`);
- test-side helpers whose file name appears in a string (`fakes / "fake_claude.py"`, run as a subprocess).

`scripts/test-changed` runs the fast tier and the slow tests of the selected files in one pytest run. It passes the selected files in `HZ_TIER_KEEP`, and `tier_marks` leaves their tests unmarked.

**It falls back to the full suite whenever it can't be sure:**

- anything that `tests/conftest.py` loads for every test (its plugins and their imports, such as `heterodyne.tmux`);
- any fake, helper, data file or other conftest under tests/;
- `pyproject.toml`, `uv.lock` or CI config;
- a deleted file or a file it can't parse;
- a module that no test reaches;
- a script that no test mentions;
- any module that more than half of the test files reach ("shared core");
- a git failure.

Docs, `spikes/`, `examples/` and the HZ_LIVE-only files select nothing, because the fast tier already covers them.

What it selects today:

| A change to | Selects |
|---|---|
| `config/*`, `fsutil`, `tmux`, `platform`, the wsd core (`journal`, `gate`, `ids`, `runtime`, `states`, `workstream`, ...), `sandbox/__init__`, `settings`, `spec` | the full suite |
| an `admind/*` module | 33 to 40 test files |
| `wsd/park`, `scheduler`, `sweep` | 17 or 18 files |
| `agents/codex` | 4 files |
| `wsd/daemon`, `cli`, `ctl`; `sandbox/openshell` | 2 files |

For example, a one-line edit to `wsd/park.py` selected 18 files. With the earlier, name-based slow list (297 cases), that took 3 min 25 s serially, against 7 min 05 s for the full suite. Under the safety-first list it costs about the same as the full suite, whether serial or parallel (section 2a). Selection would only pay again if many more tests became eligible.

**Caveat:** the selection is only as good as the dependency graph. It sees imports and module or helper names in strings. It does not see a module that reaches another through a path built at run time, a config or data file read by a test, or behaviour that changes through the environment. This is why every unmappable change and every shared module falls back to the full suite, and why CI keeps running the full suite. A wrong subset can at worst delay a failure from `test-changed` to CI. It can't hide one from CI.

### (c) "General first, specific on failure"

I measured whether this pays and concluded that it doesn't, so it is not implemented. The scripts rerun failing files instead.

The idea relies on general tests being cheap relative to the specific tests they would stand in for. Here it is the other way round:

- the atomic tests (parsers, state machines, redaction, policy) take milliseconds each, and all of them together take well under a minute;
- the general tests are the Harness tests, which take 0.5 to 3 s each, and they are what makes the suite slow.

Skipping atomic tests until a general test fails would save seconds. It would also give up the precise failure that an atomic test produces, and many of the atomic tests are safety tests that must always run.

What the runner does instead: after a failure, it reruns the failing files in full and verbosely, slow tests included. That gives the "narrow it down" output of a troubleshooting run without anyone having to ask for it.

### (d) Parallelism

pytest-xdist 3.8 (and execnet, which it needs) is now in the `dev` dependency group, hash-pinned in `uv.lock`. CI still runs `uv sync --locked`, so CI hosts install nothing new by other means, and runs stay offline. The tier scripts pass `-n auto --dist worksteal`. A plain `uv run pytest`, which is what CI runs, is still serial.

`--dist worksteal` matters. With the default `--dist load`, the full suite took 44 s, because a worker that had been handed a run of slow Harness tests set the wall time. With `worksteal`, idle workers take queued tests from busy ones, and the suite takes 25 s.

**Parallel safety, checked with nine full parallel runs (32 workers: five with `load`, four with `worksteal`):**

- Each test gets its own tmux socket from `tests/tmux_guard.py`.
- Every file a test writes is under `tmp_path`, or under a `mkdtemp` directory with a unique name.
- The `/dev/shm` base temp is created by the controller. The workers inherit it and each gets its own `popen-gwN` directory, which is how pytest intends it.
- The fd-count test in `test_wsd_gate.py` counts the fds of its own process, and passed in every run.

Only the first run failed, once, in `test_admind_au11.py`. It was a race in two tests that has nothing to do with parallelism (section 5), and it is fixed. Every run since has passed. **No test needed a separate serial pass,** so there is no `serial` marker. If one ever needs it, the runner is the place to add a second, `-n 0` pass for it.

**Proposal (not made in this PR):** run CI with `-n auto --dist worksteal` as well, after a soak period of local runs. CI on the self-hosted runners took 22 min on PR #54, before the fixes in this PR. Expect about 7 min serially after them, and well under a minute in parallel, depending on the runner's CPUs.

### (e) One entry point

The entry point is `scripts/run_tiers.py {fast|changed|full|slow-list}`. `scripts/test-fast`, `scripts/test-changed` and `scripts/test-full` are thin wrappers around `uv run`.

## 3. Overlap and redundancy

To look for overlap, I recorded which lines of `src/heterodyne` each test executes in-process, using `sys.monitoring`. For each test, I then looked for another test whose line set contains it.

Two caveats limit what this shows:

- **It does not see subprocesses.** That covers the fake claude, `python -m heterodyne...` runs, and sandboxed children. 295 tests execute no in-process src lines at all.
- **A test whose lines are a subset of another's still asserts different things.**

So this is a list of candidates for a human to review, not a list of tests to delete.

| Relation | Tests | Of which identical line sets | Their time (s) |
|---|---|---|---|
| contained in another case of the same parametrized function | 1114 | 1018 | 63 |
| contained in a different test in the same file | 573 | 226 | 81 |
| contained only in a test in another file | 170 | 18 | 13 |

**Even deleting every contained test would save at most about 157 s of the 424 s.** And most of those tests are the refusal and latch variants: `test_wrong_digest_refused` sits inside `test_commands_with_mismatching_arguments_are_refused`, and `test_latched_drops_approve` inside a bump test. These are safety tests, whose point is the assertion, not the lines. The parametrized cases are cheap and test inputs, not paths.

Some of the larger ones are worth a look by their owners:

- `test_admind_daemon.py::test_long_turn_with_a_lost_prompt_hook_is_never_pasted_over` (6.6 s) runs a subset of the lines of `test_lost_prompt_hook_holds_the_queue_until_interrupt`. If its "long turn" assertion could be added to the other test, the suite would lose 6 s.
- `test_admind_approvals.py` and `test_admind_reactions.py` / `test_admind_asks_bump.py` each test command refusals (wrong digest, not on a card, stranger, latched) through their own Harness. The overlap is real but small (about 0.6 s per test). The files belong to different design revisions, and each guards its own revision's contract. I recommend keeping both.
- `test_admind_r13_summary.py::test_timeout_kills_the_whole_process_group` and `test_failures[sleep 5-timeout]` follow the same path. They are about 1 s each, and the first one checks the process group.

Overall, the suite is not padded. Its cost is per-test overhead (sections 1 and "What is left"), not duplicated tests. Cutting that overhead helps every test; deleting tests would save little and weaken coverage.

## 4. Proposals not made in this PR

These need a decision first.

- **CI stays the full suite.** One option is to run `test-changed` on pushes to feature branches and keep the full suite on pull requests and `main`. At 7 minutes, the full suite is cheap enough that I don't recommend it yet. It becomes worth it if the suite grows back past about 15 minutes.
- **Make the paste delay configurable** (section 1, "What is left"). This is the largest remaining saving.
- **Inject a clock into the wsd lane worker** (the 10.6 s shutdown test).
- **`-n auto --dist worksteal` on CI** (section 2d).
- **One wait helper for the admind tests** that reports diagnostics on timeout (section 5).

## 5. Flakiness and wall-clock timeouts

The admind tests wait with real wall-clock bounds: `Harness.until` (15 s by default, up to 40 s), `asyncio.wait_for(..., N)`, `threading.Event.wait(N)`. Against them stand production timers that the tests shorten through settings (batch windows of 0.2 to 2 s, summarizer timeouts of 1 to 5 s).

**What failed:**

- **`test_admind_au11.py::test_login_dirs_never_leave_through_the_backstop`** (CI on PR #54, and this PR's first parallel run). This was a race, not a bound that was too tight.
  - `FakeWnAgent` lists a send before it replies, and the daemon records the outbox row as sent, with its message ID, only after the reply.
  - The test read the batch's message ID as soon as the batch text appeared, so it could read NULL. `!details` then replied to "None" and waited out its 20 s.
  - It failed alone, serially, in 1 run out of 15. With a 0.3 s delay before the fake's reply, it failed every time.
  - `test_admind_r13_details.py::test_details_on_a_batch_returns_every_reply_in_order` had the same race.
  - Both now wait for the recorded send (`batch_message_id` in test_admind_r13_replies.py): 0 failures in 40 runs, and they pass with the delay. Commit c4467b1.
- **`test_admind_t9r7.py::test_an_audit_failure_on_a_deadline_still_holds_dispatch`** failed once, in the baseline run, when every Harness test spent 2 s in teardown. Its `read.started.wait(10)` timed out. It has not failed since (two serial runs, five parallel runs).
- **`test_admind_asks_bump.py`** has flaked on CI the same way, according to the controller. I don't have the test ID or the log, and it has not failed here.

**How close do the waits come to their bounds?** I recorded every `asyncio.wait_for` called from a test file over three full `-n auto` runs: 74 083 waits. The test-side waits that ended in success used at most 20% of their bound:

| Wait | Bound | Slowest success |
|---|---|---|
| `Harness.until` | 15 s | 1.5 s |
| `Harness.until` | 20 s | 1.1 s |
| `Harness.until` | 30 s | 6.0 s |
| `Harness.until` | 40 s | 2.6 s |

The one exception is in `test_admind_socket_bounds.py`, whose waits time out on purpose.

**So larger bounds would not have helped.** The CI failures so far were a race (au11), or a bound that 32 parallel workers never come near. A bound only costs time when a test fails, so a generous one is cheap. The useful changes are:

1. **Route every wait through one helper that prints diagnostics on timeout.** `waited` in test_admind_r13_replies.py already does this. A bare `h.until` or `wait_for` fails with an empty `TimeoutError` (as au11's did on CI), and that says nothing about what was missing. This is cheap and needs no production change. I recommend it as a follow-up.
2. **Wait for the recorded state, not the fake's view of it.** `h.fake.sent` runs ahead of the store (au11). A test that then reads the store should wait on the store.
3. **An injected clock** for the production timers that tests shorten (batch window, backstop, summarizer and lane timeouts, the 10.5 s wsd shutdown test). This would make those tests both faster and deterministic. It is a production change for the owners to decide, and the largest structural fix for timing flakes.
