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

## Amendment 1 (after code review r4)
**Why.** §2 serializes socket removal against tmux startup with tmux's own `<p>.lock`. That doesn't work.
- tmux 3.4 `client_get_lock` opens `<p>.lock` with `O_CREAT` before it flocks it.
- After bind and listen, `server_start` unlinks the lock path, and only then closes the descriptor.
- So a delayed client can open the old inode, flock it after its path is gone, and start server B while it holds an unlinked inode. The watchdog then creates and locks a fresh `<p>.lock`, sees a refused connect from B (bound, not yet listening) and deletes B's socket.
- Unlinking a lock we hold has the same flaw: a new inode can appear at the same path, and our flock protects only the old one.

This amendment replaces that lock with one that we own and whose path stays put. It supersedes every use of `<p>.lock` in §2 and §4.

### A1. The launch lock
- **Created with the root.** The plugin creates `root/launch.lock` (O_CREAT|O_EXCL, 0600) right after `root`, before it starts the watchdog. It holds no descriptor on it.
- **Never replaced.** Nothing unlinks or recreates it until the last step of closure (A3).
  - Launchers open it without O_CREAT.
  - The rename moves it into `dead` with the same inode. The watchdog and every launcher therefore lock the same inode.
- **Who holds what.**
  - Every test-side tmux invocation runs under LOCK_SH, taken before the command starts (A2).
  - Before each removal, the watchdog takes LOCK_EX non-blocking (A3).
- **No `<p>.lock` reliance.**
  - The watchdog never creates, opens or flocks `<p>.lock`. So cleanup leaves no artifacts of its own, which settles review r4 #2 at the root.
  - A `<p>.lock` it finds is just an entry to remove under EX (A3).
  - I'm not keeping it as an extra signal: the review showed it is unreliable, and checking it is what created the artifacts.

### A2. Where SH is taken, and for how long
**Facts checked in the tmux source (tags 3.4 and 3.5a; the server.c start path is identical in both).**
- `client_connect`: if connect fails with ENOENT or ECONNREFUSED, take `<p>.lock`, then connect again, then `unlink(p)`, then `server_start`.
- `server_start`, in the forked server, before the event loop:
  - `proc_fork_and_daemon` (socketpair, then `fork`, then `daemon(1, 0)`);
  - `server_create_socket` (unlink, then bind, then listen; with HAVE_SYSTEMD it goes through `systemd_create_socket`, which falls back to it);
  - only then `server_client_create(fd)` for the client's socketpair.

  So the client's command (`start-server`, `new-session -d`) is handled only after listen has succeeded or failed. If it failed, the cause becomes the client's exit message, and the command exits non-zero ("No such file or directory" after the rename: this is test 3).
- **Inherited fds.** `closefrom(STDERR_FILENO + 1)` runs only in `client_exec`, in pane spawn (`spawn.c`), in jobs (`job.c`) and in `pipe-pane`.
  - The client and the daemonised server keep every inherited descriptor.
  - Panes, hooks and jobs drop them.
- **Which commands can start a server.** Only those with CMD_STARTSERVER do: `new-session`, `start-server`, `attach-session` and `list-keys`. The watchdog's `display-message` and `kill-server` never do.

**How long SH is held.** The launcher opens `<socket_path.parent>/launch.lock` (O_RDONLY, close-on-exec) and takes a blocking LOCK_SH. It runs the command with `pass_fds=(fd,)`, and closes its own fd when the command returns.
- That one open file description is shared by the launcher, any prefix (`sh -c ...`), the tmux client and the server it forks. So SH lasts until the last of them exits, which for a started server means its whole lifetime.
- The review's suggestion was to hold SH "until the command returns". That isn't enough: SIGKILL of pytest mid-launch is exactly the case this design is for.
  - If only pytest held SH, it would be released while a server is still bound but not listening. That reopens the r4 hole.
  - With the fd inherited, the server holds SH itself, whatever happens to pytest or the client.
- The lock path comes from the `-S` path's parent, so it lives in the same root as the socket.
  - After the rename, the open fails with ENOENT. The launcher raises WatchdogError and nothing runs.
- Every test-side invocation takes SH, not only starts. It costs one open and one flock, and the argument then doesn't depend on the CMD_STARTSERVER table staying as it is.

**The injection point: a test subclass on a no-behaviour-change seam.**
- **The production seam.** In `src/heterodyne/tmux.py`, both `subprocess.run` call sites (`_run`, and the launcher branch of `new_session`) call one new private method, `_exec(argv, *, input, timeout)`.
  - `_exec` calls `subprocess.run(argv, input=input, capture_output=True, timeout=timeout, check=False)`.
  - That is the same call as today, so behaviour, arguments and error mapping are unchanged. Production gains no parameter and no test-only path. A unit test pins the argv/timeout pairs that exist today.
- **The test side.** `tests/tmux_guard.py` adds `GuardedTmux(Tmux)`, which overrides only `_exec` to wrap the call as above. Its single public entry is `new_test_tmux(launcher=None) -> GuardedTmux`, with a fresh socket path in the root.
  - The `tmux` fixture, the Harness and the child-prelude bodies use it.
  - The serve test's `cli.Tmux` monkeypatch returns `new_test_tmux(launcher=launcher)`.
  - `new_test_socket_path()` becomes private (`_new_socket_path`). Tests that need the path read `t.socket_path`.
  - The raw `tmux -S` queries in the child prelude (`tmux_out`) go through the same guarded helper.
- **Why not a keyword-only hook.**
  - A hook would add a constructor parameter that production never passes, which is test-only surface on a production class.
  - It would also have to carry `pass_fds` into both call sites. That is the same seam, plus a public API.
  - Overriding `_run` and `new_session` without a seam would copy the launcher branch into tests, and the copy would drift.
- **Enforcement.** A source-scan test fails if any module under `tests/` other than `tmux_guard.py` (and excluding `tests/live/`) passes `socket_path=` to `Tmux` or calls the private path helper. An unguarded start in the root is the one way around this protocol (see A5).

### A3. Watchdog changes
- **Opening the lock.** After the rename, the watchdog opens `dead/launch.lock` once (O_RDONLY, close-on-exec, so its tmux and ps children don't inherit it), and records `(st_dev, st_ino)` from `fstat`.
  - If that fails, it removes nothing: every socket is kept and listed as `unresolved` (reason `launch lock missing`), and `closed` is false.
- **Removal protocol** (`remove`), for every unlink of a socket or a `<p>.lock`. `sweep_lock` goes through the same function, so "every unlink goes through remove" is true again.
  1. `flock(LOCK_EX|LOCK_NB)`. If it is busy, the result is `held`: keep the entry and retry next pass. It is `unresolved` at the deadline.
  2. Under EX:
     - `lstat(dead/launch.lock)` must still match the recorded identity. If not, the result is `replaced`: remove nothing for the rest of closure, and record `unresolved` (reason `launch lock replaced`).
     - Then the existing per-entry checks:
       - the generation re-lstat;
       - a fresh connect (refused or missing), for sockets;
       - the cached-PID death proof where one exists.
  3. Unlink the entry, then any sibling `<p>.lock`. Then LOCK_UN, in a `finally`.
- **What EX proves.** Every guarded client and server holds SH. So under EX, no guarded tmux process with a descriptor on this root is alive, and in particular none is between bind and listen. The connect and generation checks remain for anything unguarded.
- **The kill phase is unchanged.** `display-message`, `kill-server` and the death wait need no lock. A killed server releases its SH when it exits, so its removal succeeds on the same pass, or the next one.
- **Removed from §2.**
  - `try_lock`, the 20-try loop, and every create or flock of `<p>.lock`.
  - The PID-unknown/refused branch becomes: EX taken plus checks pass means `stale`; otherwise `held`.
- **Final step.** When `dead` holds only `launch.lock`:
  - take EX non-blocking (if busy, retry next pass);
  - then unlink it, LOCK_UN, close, and `rmdir(dead)`.

  This step isn't load-bearing for safety: after the rename no one can bind in `dead` (A4). It is there so that closure means "no guarded process remains", and a holder still present at the deadline is reported as `unresolved` (`launch lock held`).
- **Deadline.** EX is never taken blocking, so no step can outlast the deadline because of a lock. The r4 #7 ordering (check the local end before accepting success) is kept.

### A4. The late cases
- **The order of events.** The rename happens before the watchdog's first EX attempt, and every removal happens under EX. A launcher L with the fd on our inode is in one of these cases:
  1. **L opens after the rename.** ENOENT, WatchdogError, and no tmux runs.
  2. **L acquires SH before some watchdog EX.** The watchdog can't remove anything while L, its client or its server holds SH.
     - If the bind landed before the rename, the socket is in `dead`, the server listens, and the connect is accepted. The socket is killed, its death is proven, SH is released, and then it is removed.
     - If the bind came after the rename, it fails with ENOENT. The client exits non-zero and releases SH, and nothing is bound.
     - Holding SH therefore blocks removal until that server dies, which is stronger than "until it listens".
  3. **L acquires SH only after a watchdog EX** (it opened the lock before the rename and was blocked or delayed), including after the final unlink, on the unlinked inode.
     - The rename came before that EX, so L's tmux runs after the rename. The `-S` path, the client's `<p>.lock` open and the server's bind all fail with ENOENT, and nothing is bound.
     - Holding a lock on the unlinked `launch.lock` inode gives L nothing to race against.
- **The pytest SIGKILL cases.**
  - Before bind (test 3): the gated launcher holds SH through the gate. Closure waits for it (`held`), the late launch fails with ENOENT, SH is released, and closure completes.
  - Between bind and listen: the server holds SH through the inherited fd, so case 2 applies.
- **tmux's own lock is no longer involved.** A client holding an unlinked `<p>.lock` inode (Codex's reproduction) still holds launch SH, so case 2 applies.

### A5. Residual risks
- **Unguarded starts.**
  - A tmux server started in the root without GuardedTmux is not serialized. The connect, generation and PID checks still apply, which matches the exposure before this amendment.
  - The source scan (A2) is the guard against this. Panes can't start one with our descriptor, because tmux closes it.
- **Two concurrent starts on the same path.**
  - Two starts racing on one socket path are tmux's own race (client `unlink(p)` and server `unlink` then bind).
  - Each test socket path is unique (`uuid4().hex[:8]` in a private root), so this is out of scope.
- **A survivor blocks all removals.** A server that survives kill-server keeps SH, so every removal in the root waits. This is conservative: the survivor's socket already keeps `dead` from being removed, and the summary still lists everything with `closed: false`.
- **A same-uid attacker** remains out of scope (§1).

### A6. Test changes
- **Test 4 (bound but not listening).** The test holds LOCK_SH on `root/launch.lock` instead of flocking `<p>.lock`.
  - (a) SH is held: the socket is kept, the entry is `unresolved`, `closed` is false, and no `<p>.lock` exists afterwards.
  - (b) SH is released: the socket is unlinked as `stale`, and `closed` is true.

**New regressions.** These use real flocks; the scripted ones use the FakeSweeper.
1. **Startup holding an unlinked tmux lock** (Codex's reproduction).
   - A socket is bound but not listening.
   - A helper process holds EX on an unlinked old `<p>.lock` inode and SH on `launch.lock`.
   - Result: the socket is kept, no `<p>.lock` is created, and the entry is `unresolved` at the deadline.
2. **Lock replacement during cleanup.**
   - (a) `launch.lock` keeps the same inode across every pass until the final step.
   - (b) If `launch.lock` is replaced or deleted mid-closure, nothing more is removed, and the entry is `unresolved` with reason `launch lock replaced`.
3. **Late launcher after the rename.**
   - (a) `new_test_tmux()` after the rename raises WatchdogError and runs no tmux.
   - (b) A real tmux in a child pytest: GuardedTmux gated between open and flock, released after the final unlink. The start fails with ENOENT and no server exists.
   - (c) Test 3 extended: closure reports `held` while the gated launcher holds SH, and completes after the gate.
4. **Removal blocked while SH is held.**
   - A dead server's socket, refused with its PID dead, plus a holder of SH.
   - It is not removed while SH is held. It is removed on the next pass after release. At the deadline with SH held: kept, `unresolved`, `closed: false`.
5. **SH outlives the launcher.**
   - A guarded `_exec` runs a command that leaves a background child holding the inherited fd.
   - After `_exec` returns, EX non-blocking fails. It succeeds once that child is killed.
   - Real tmux in a child pytest: SIGKILL pytest after `new_session`, and EX stays unavailable until the watchdog has killed the server.
6. **Cleanup creates no artifacts** (replaces r4 #2's regression). The last server is dead at the deadline: `closed` is true, and no `.lock` is left.
7. **The seam.** `Tmux._exec` receives today's argv, input and timeouts. There is also the source scan from A2.

**Mutation targets** (`run5.py`/`results-r5.md`):
- remove even though EX failed;
- take EX blocking;
- drop `pass_fds` (pytest-only SH);
- unlink `launch.lock` on every pass;
- skip the identity check;
- let the watchdog create `<p>.lock`;
- let `sweep_lock` bypass `remove`;
- open `launch.lock` with O_CREAT in the launcher (expected to survive, because the path doesn't resolve after the rename: recorded as equivalent);
- `GuardedTmux` not overriding `_exec`.
