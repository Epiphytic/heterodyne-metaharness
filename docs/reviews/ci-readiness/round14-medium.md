## Round 14 MEDIUM review: no critical flaws. Good enough to implement as a PoC.

**How it ran:** I invoked the built-in `/code-review` Skill at `medium`, not the plugin version. It ran as its own forked process. It started no internal subagents, and I started none. It reviewed the uncommitted working-tree diff only, not the 182 committed branch commits.

**Side effects:** The Skill ran ruff, pyright and the 5 new tests, and `uv run` created an ignored `.venv`. That breaks the "no tests" limit you set, and I hadn't asked it to run anything. Ruff and pyright passed on the changed files, and the 5 new tests passed on a host with a high descriptor limit (1048576). I made no edits and touched no queue, network or comments.

**Inputs:** HEAD is `3c76022…`, and all seven file hashes match the frozen list exactly. Findings from rounds 1–2 (HIGH) and rounds 3–13 (MEDIUM) stay **OPEN**; nothing was reset.

### Critical findings
None.

### Non-critical findings (my verdict on each after reading the code)
1. **Stack-dump signal late in shutdown** (`scripts/pytest_shutdown_probe.py:78`), plausible but rare. The supervisor sends SIGUSR1 at 0, 20 and 40 s after the session ends. Python removes its SIGUSR1 handler near the very end of shutdown, and after that SIGUSR1 kills the process. A run that passes could then be reported as failed (exit 138), but only if a signal lands in that short final window. A shutdown stuck waiting on threads or atexit hooks still has the handler, so it's unaffected.
2. **60 s shutdown budget vs. the 45 s tmux watchdog wait** (`probe:82`, `tests/tmux_guard.py:41,238`). Confirmed. The 60 s budget starts when the session finishes, and `pytest_unconfigure` may then wait up to 45 s for the tmux watchdog, leaving about 15 s for everything else. A slow but healthy teardown could be cut off and reported as exit 124.
3. **Force-kill skipped once pytest dies** (`probe:91-100`). After SIGTERM, if pytest itself exits within 5 s, the loop stops and never sends SIGKILL to the rest of its process group. A child that ignores SIGTERM keeps running. On a normal exit the group isn't cleaned up at all.
4. **Job cancellation isn't passed on** (`probe:65`). Pytest runs in its own session, and the supervisor doesn't forward SIGINT/SIGTERM to it. If the job is cancelled or hits the 70-minute limit, pytest is orphaned and no diagnostics are recorded. On hosted runners, tearing down the VM limits the damage.
5. **Import path differs from `uv run pytest`** (`probe:36`). Setting `sys.path[0]` to the checkout root (as `python -m pytest` does) can hide import errors that plain `uv run pytest` would show. The code comment says this is deliberate.
6. **High-descriptor tests skip on macOS** (`tests/test_high_fd_waits.py:24`). The fixture skips when the soft limit is 2048 or lower instead of raising it toward the hard limit. macOS defaults to 256, so the kqueue path is never tested there.
7. **Missing argument crashes instead of printing usage** (`probe:120`). `--supervise` with no directory hits an unpacking error before the mode check runs.
8. **The same wait loop is written four times** (`fetch.py:419`, `tmux_guard`, `test_escapes`, `test_sandbox_creates`), with no shared helper. The existing code in `shim.py` and `channel.py` uses `select.poll` instead, so there are now two styles that could drift apart.
9. **Duplicate stack dump at 0 s** (`probe:74`). The first SIGUSR1 repeats the dump the worker just wrote, which adds noise to every log.

**Refuted:** The Skill also said `fetch-depth: 0` in the test job is unused. That's wrong. `test_harness_gittree.py`, `test_session_git.py`, `test_actions_p0.py` and `test_admind_links.py` use git history, and your evidence ties 4 of the 147 failures to the shallow checkout.

### PoC verdict
The `selectors` replacements look correct: deadlines, EOF handling and bounds are kept, and a negative timeout is clamped to 0. The probe never turns a failure into a pass: a hang gives 124, and otherwise pytest's own exit code is passed through. It is honestly scoped as diagnostics only. Items 2 and 3 are the ones most worth fixing before relying on its results; neither blocks a PoC.

This is not an APPROVE. It doesn't approve the design or authorize deployment, and it doesn't fix the post-summary hang or the remaining failing tests.

`★ Insight ─────────────────────────────────────`
- `select.select` can only watch descriptors numbered below 1024 (FD_SETSIZE), which is why it failed once the runner's descriptor count went high. `selectors.DefaultSelector` uses epoll or kqueue, which have no such ceiling.
- `faulthandler.register(SIGUSR1)` replaces the default action, which kills the process. Once Python removes the handler late in shutdown, the kill action is back. That is the source of the small false-failure window in item 1.
`─────────────────────────────────────────────────`
