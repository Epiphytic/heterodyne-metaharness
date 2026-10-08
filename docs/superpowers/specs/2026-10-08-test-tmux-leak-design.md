# Design r4 (approved, with the two r4 review notes folded in): offline tests must not leak tmux servers (btq-q1r4p)

**Problem.** `run_with` and the test_tmux fixture start a private tmux server and kill it in a `finally`. If pytest is killed, the server and its panes stay running; one ran for 5.5 h. The fix is a watchdog outside pytest that cleans up a directory only this pytest can use.

## 1. Every socket in the root is ours
- **The directories.** At session start the plugin creates `run = mkdtemp(prefix="hzt", dir="/tmp")`, resolved, and `root = run/s`. Both have mode 0700.
- **Why that makes every entry ours.** The name is random, mkdtemp creates it with O_EXCL, and only our uid can enter it. Only this pytest knows the path; it hands out `new_test_socket_path() -> root/<hex8>`. So every entry in `root` was created by this pytest or one of its children: a socket, or the `<socket>.lock` file tmux uses while starting a server. No other run, xdist worker or worktree can bind a socket there or swap one in. A same-uid attacker is out of scope.
- **Path length.** The paths ignore `TMPDIR` and `TMUX_TMPDIR` and are under 40 bytes.
- **Tmux change (src).** `Tmux(socket_name, binary="tmux", launcher=None, *, socket_path=None)`. A single `_selector()` returns `("-S", path)` or `("-L", name)`, and both command builders use it. The defaults are unchanged, only starts use the launcher, and production never passes `socket_path`.
- **drop_tmux** uses the exact path.

## 2. The watchdog (tests/tmux_watchdog.py)
**Startup.**
- The plugin starts it with `Popen([sys.executable, <absolute script path>, <absolute run>, <ack fd>], stdin=PIPE, start_new_session=True, close_fds=True, pass_fds=(ack_w,))`.
- stdout and stderr go to `run/watchdog.log`.
- Pytest holds the only stdin writer. It is non-inheritable, and every child is started with `close_fds`.
- The watchdog writes `ready` on the ack fd within 5 s. If it doesn't, the plugin terminates it, waits 2 s, kills it, and reaps it. `new_test_socket_path()` then raises, so no tmux test launches.

**Wait.** The watchdog blocks until stdin reaches EOF. That happens when pytest exits by any signal, or when the plugin's teardown closes the writer.

**Barrier.** It runs `rename(root, run/dead)`. A launch that starts afterwards gets ENOENT. A bind that had already resolved `root` before the rename can still create its socket in `dead`. That is why closure, below, is defined by a successful rmdir, not by a single sweep.

**Closure loop.** The deadline is 30 s, monotonic. Each pass sweeps every entry in `dead`, each with its own error handling and every command bounded to 5 s.

- **A socket.** Query `display -p '#{pid}'`, then:
  - **PID known:** run `kill-server`, then wait up to 5 s for it to be **dead**. Dead means both:
    - a connect is refused, or the socket is missing; and
    - the PID is not running: no `/proc/<pid>` or state Z on Linux, empty `ps -o stat=` or state Z on macOS.

    If it's dead, unlink the socket and record it in `killed`. If not, it is in `survived`, and the socket is kept.
  - **PID unknown, connect refused:** tmux binds before it listens, so this isn't proof of death. Open `<p>.lock` and try a non-blocking `flock` 20 times at 100 ms intervals; tmux holds that lock from before the bind until the listen. Then:
    - lock taken and the connect is still refused: a dead or crashed start. Unlink the socket and the lock file, and record it in `stale`;
    - lock taken but the connect now accepts: treat it as a live server, using the PID-known path above;
    - lock never taken: keep the socket and record it in `unresolved`.
  - **PID unknown, connect accepted:** run kill-server with a bounded wait, then re-query. If it is still unproven, it is `survived`.
- **A `.lock` file with no socket.** Unlink it if `flock` succeeds. If not, leave it for the next pass. If it is still held at the deadline, it is recorded in `unresolved`.
- **Then `rmdir(dead)`.**
  - Success: closure. Nothing can be created in `dead` any more.
  - `ENOTEMPTY`: sleep 200 ms and run another pass.
  - Deadline reached: stop, keep `dead`, and report what's left.

**Finish.**
- `survived` and `unresolved` describe the final state. An entry recorded in an earlier pass is dropped from them if a later pass resolves it.
- The watchdog removes `run` entirely only if closure succeeded (`rmdir(dead)`) and all four lists are empty.
- Otherwise it keeps `run`, with `run/summary.json` listing `killed`, `stale`, `survived` and `unresolved` with paths and PIDs, plus `closed: true/false`. If closure failed, it keeps `dead` as well.
- After a successful emergency cleanup, `killed` (or `stale`) is populated, `survived` and `unresolved` are empty, and `closed` is true.
- Panes die with their server, because kill-server sends them SIGHUP. Tests assert this.

**Session teardown** (`pytest_unconfigure`, which also runs after `pytest.main()`):
- The plugin closes the writer and waits up to 45 s.
- If the watchdog is still running, a daemon thread reaps it whenever it exits. If the interpreter exits first, init adopts and reaps it. Pytest doesn't block.
- It warns with the run path if the summary exists or the wait timed out. A leftover from a passing test is reported as a teardown gap.

## 3. Also in scope
- `before(h)` moves inside the `try` in `run_with`.
- CI installs tmux on both platforms. With `HZ_REQUIRE_TMUX=1` and no tmux, a single session-level `pytest.exit`.
- There is no SIGTERM customization, PID or cmdline ownership, registration or startup sweep.
- Concurrency: each pytest process (xdist worker or worktree) has its own `run` and its own watchdog. Nothing is shared.

## 4. Tests (tests/test_tmux_watchdog.py)
**Set-up.**
- **Children:** child pytests load the plugin with `-p`, start with `start_new_session=True` and run from another working directory. Handshakes use bounded files. The parent kills any captured PID that survives, in a `finally`.
- **Time budgets:** the watchdog deadline plus a margin, 60 s on Linux and 90 s on macOS CI. These are polled with a monotonic deadline and never sleep for a fixed time.
- **Assertions:** on PIDs, never `has-session`.

**The tests.**
1. **SIGKILL through each start path:** `run_with`, and the test_tmux fixture with two sessions on one server. The child reports `run` and the server and pane PIDs (`list-panes -a`), then blocks.
   - After the kill, every PID is dead and the sockets and `dead` are gone.
   - `summary.json` has `killed` populated, `survived` and `unresolved` empty, and `closed` true.
2. **killpg:** the same result, and the watchdog survives the kill in its own session.
3. **SIGKILL before bind:**
   - A gated launcher. Pytest is killed, and the test waits until `root` no longer exists (the rename is confirmed) before opening the gate.
   - Result: the late launch fails, no server exists, and `run` is removed.
4. **Bound but not listening (deterministic):**
   - The test binds a Unix socket in `root` without listening, and holds `flock` on `<p>.lock`. Then it closes the writer.
   - (a) The lock is still held: the socket is kept, `unresolved` is set and `closed` is false.
   - (b) The lock is released with no listener: the socket and lock are unlinked, the entry is `stale` and `closed` is true.
5. **SIGKILL between kill and unlink:** a gated drop_tmux.
   - The server PID is dead and the connection is refused.
   - PID discovery fails and the connect is refused, so the entry takes the `stale` branch. The watchdog unlinks the socket and doesn't re-kill.
   - `summary.json` has `stale` populated, `survived` and `unresolved` empty, and `closed` true; `run` is kept.
6. **A hook child still running at the kill:** the tmux, pane and hook PIDs are all dead.
7. **`pytest.main()` returning to a live interpreter:** the watchdog exits, it is reaped, and `run` is gone. A slow-cleanup variant, with the wait lowered, still reaps it later through the thread.
8. **A test that leaks (skips drop_tmux):** the watchdog kills it at teardown, and the warning names it.
9. **Fake runner, unit tests:** kill-server always "succeeds".
   - (a) The connect is refused but the PID is live.
   - (b) The connect is accepted but the PID is dead.

   Both must report `survived` and keep the socket. Deleting the PID check fails (a); deleting the connect check fails (b).
10. **Watchdog startup failure:** a bad script path, or no ack. The watchdog is terminated and reaped, tmux tests fail, and nothing launches.
11. **Socket root:** ignores `TMPDIR` and `TMUX_TMPDIR`.
12. **Tmux selector:** `-L` and `-S`, each with and without a launcher; launcher behaviour is unchanged.
13. **CI enforcement:** `HZ_REQUIRE_TMUX=1` with no tmux fails the session once.

**Mutation targets:**
- remove `kill-server`;
- remove the rename;
- one sweep, no rmdir loop;
- drop the PID check, the connect check or the lock check;
- inherit the writer;
- drop `start_new_session`;
- don't close the writer;
- remove the startup-failure reap.
