# Test suite efficiency review

This review covers the offline suite (`uv run pytest`, which CI runs), as of `origin/main` e07e0a2: 3213 tests. `tests/live` (HZ_LIVE=1) is out of scope. Every number here comes from a single serial run on the shared Linux development host, which was busy and had a nearly full nvme disk. Expect the numbers to move by tens of percent from run to run.

## Which command when

| Command | Runs | Typical time (serial) | Use it |
|---|---|---|---|
| `scripts/test-fast` | every test except the ones in `tests/slow_tests.txt`; safety tests always run | about 3 min | while you iterate, and before you hand over every review range |
| `scripts/test-changed [--base REF]` | `test-fast`, plus the slow tests of the files your change reaches; the full suite when the change can't be mapped | 3 to 7 min | before you ask for a review of a change |
| `scripts/test-full` | the whole suite, the same as CI | about 7 min | before merging, after touching shared code, or whenever `test-changed` has a doubt (it falls back to this by itself) |
| `uv run pytest tests/test_x.py` | one file | seconds | while you debug that file |

- All three scripts accept pytest arguments after `--` (for example, `scripts/test-fast -- -x`).
- When a run fails, the scripts run the failing files again in full with verbose output (`-rfE -v`), slow tests included. A broad failure then comes back with the narrower tests of the same area next to it. `--no-diagnose` turns this off. The rerun never changes the exit code.
- **CI still runs the full suite on every push and pull request.** The tiers are a way to iterate locally. They don't replace CI.
- After a large change to test times, regenerate the slow list from a timed full run:

  ```sh
  uv run pytest -q --junitxml=/tmp/hz-full.xml
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

### What is left

These are the remaining costs, largest first. None is fixed in this PR.

- **`Tmux.paste` sleeps 0.3 s** between `paste-buffer` and `Enter` (src/heterodyne/tmux.py, `time.sleep(0.3)`). 370 tests reach that line, many of them more than once, so it costs **at least 110 s of the 424 s**. This is a production constant: it gives the pane's application time to read the paste. Two ways to shorten it in tests: make the delay a `Tmux` attribute, which the Harness sets to something like 0.02 s against the fake claude (the fake reads its input synchronously); or have the fake signal that it has read the paste. Either way it needs an owner's decision, since it touches the code the live agent depends on. **Recommended as the next step.** Its effect is about the same as a 25% faster suite.
- **Agent start and the ready notice:** about 0.6 s per Harness test (tmux `new-session`, the fake starting up, then the hook round-trip). Sharing one Harness per module would remove most of it. But the tests rely on a fresh store, a fresh audit log and a fresh latch state, so that would weaken isolation. Not recommended.
- **Fixed `asyncio.sleep` calls in test_admind_daemon.py**, about 13 s in total (1.5, 1.5, 1.2, 1.0, 1.0, 0.8 and several 0.5). Most of them prove a negative ("nothing was posted while ..."), which a fixed wait is the honest way to test. Where a test then waits for a positive event, it could wait on the event instead. That is worth doing case by case, not as a sweep.
- **test_wsd_daemon.py::test_shutdown_closes_every_lane_before_any_starts_a_queued_job** takes 10.6 s, the slowest single test. It waits out real lane timeouts. An injectable clock in the lane worker would make it take milliseconds. That is a small change to production code, for its owner to decide.
- **test_sandbox_runtime.py** (37 s) launches real sandboxed processes and is a safety file. It stays in every tier. Speeding it up would mean sharing a prepared sandbox root between cases, which is possible but not attempted here.

## 2. Tiers (this PR)

### (a) The fast tier

`tests/slow_tests.txt` lists 179 test functions (297 cases) that took 0.5 s or more in the run after the fixes. `tests/tier_marks.py`, loaded from `tests/conftest.py`, marks them `slow`, and `scripts/test-fast` runs `-m "not slow"`.

**No safety test is ever slow.** A test counts as safety if its file is in `SAFETY_FILES` (redaction, secret scanning, policy, every `test_sandbox_*`, r13 redaction, approvals, socket bounds, the install-agnostic checker and the offline isolation test) or its name contains one of `SAFETY_WORDS` (redact, secret, sandbox, latch, digest, polic, leak, npub, nsec, token, auth, isolat, guard, pin).

- If the slow list names a safety test, collection fails with a usage error rather than quietly dropping the test from the fast tier.
- `slow-list` never writes safety tests into the list.
- `tests/test_tiers.py` checks both.

Of the 408 cases that took 0.5 s or more, 111 stay in the fast tier for this reason.

The fast tier: **2905 passed, 36 skipped, 297 deselected in 2 min 56 s** (with HZ_REQUIRE_TMUX=1).

### (b) Change-based selection

`scripts/select_tests.py` takes the files from `git diff --name-only --no-renames <base>...HEAD`, plus staged, unstaged and untracked files. It maps them to test files through an AST dependency graph of `src/heterodyne` and `tests/`. The graph includes:

- transitive imports, including package `__init__` files;
- heterodyne module names that appear in strings (`-m heterodyne.x`, `monkeypatch.setattr("heterodyne.x.y", ...)`);
- test-side helpers whose file name appears in a string (`fakes / "fake_claude.py"`, run as a subprocess).

`scripts/test-changed` runs the fast tier, then the slow tests of the selected files.

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

For example, a one-line edit to `wsd/park.py` selected 18 files. The fast tier took 173 s and the slow tests of those files took 31 s (36 tests), so **3 min 25 s in total, against 7 min 05 s for the full suite**. An admind change saves less, because most admind test files reach most admind modules.

### (c) "General first, specific on failure"

I measured whether this pays and concluded that it doesn't, so it is not implemented. The scripts rerun failing files instead.

The idea relies on general tests being cheap relative to the specific tests they would stand in for. Here it is the other way round:

- the atomic tests (parsers, state machines, redaction, policy) take milliseconds each, and all of them together take well under a minute;
- the general tests are the Harness tests, which take 0.5 to 3 s each, and they are what makes the suite slow.

Skipping atomic tests until a general test fails would save seconds. It would also give up the precise failure that an atomic test produces, and many of the atomic tests are safety tests that must always run.

What the runner does instead: after a failure, it reruns the failing files in full and verbosely, slow tests included. That gives the "narrow it down" output of a troubleshooting run without anyone having to ask for it.

### (d) Parallelism

**pytest-xdist is not a dependency today and is not in the local uv cache.** Adding it means one `uv add --dev pytest-xdist` (pytest-xdist and execnet into `uv.lock`). After that, runs stay offline.

The suite looks safe to run in parallel, but this is **unverified until a full `-n` run passes**:

- each test gets its own tmux socket from `tests/tmux_guard.py`, which already expects xdist workers;
- every file a test writes is under `tmp_path`;
- the `/dev/shm` base temp is created by the controller and shared with the workers, as pytest intends.

Because the suite is mostly waiting, it should scale well with the number of workers, until the tmux servers and the fake agents start competing for CPU.

**Recommendation:** add it, run it with `-n auto` locally, and leave CI serial until it has had a week of local runs without flakes. Whether to add it is the controller's call. If it is approved, it lands as a separate commit in this PR, with measured numbers.

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
- **pytest-xdist** (section 2d).

## 5. Flakiness

The baseline run had one failure: `test_admind_t9r7.py::test_an_audit_failure_on_a_deadline_still_holds_dispatch`. Its `read.started.wait(10)` timed out. It passed in every later run (the full run after the fixes, the coverage run and the fast tier), so it looks like a timing flake under the heavy load of the baseline, when every Harness test spent 2 s in teardown. If it shows up again, look at what sets `read.started` before raising the timeout.
