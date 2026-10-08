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

**How long SH is held.** The launcher opens the lock in two steps, which are separate functions in `tmux_guard`:
1. `_open_parent(socket_path)` opens `socket_path.parent` (O_RDONLY|O_DIRECTORY).
2. `_open_lock_at(parent_fd)` opens `launch.lock` relative to it with `LOCK_FLAGS` (O_RDONLY|O_CLOEXEC, never O_CREAT), then closes the parent fd.

This makes the kernel's split between resolving the parent and resolving the final component explicit, so a test can drive the gap (A6 regression 8).

Then it takes a blocking LOCK_SH, runs the command with `pass_fds=(fd,)`, and closes its own fd when the command returns.
- **It closes the fd; it never calls LOCK_UN.** The lock belongs to the open file description, which the client and the server share. LOCK_UN from any holder would release SH for all of them, including a server that is still starting. LOCK_UN is therefore forbidden on the launcher side; this is a mutation target.
- That one open file description is shared by the launcher, the allowed prefix, the tmux client and the server it forks. So SH lasts until the last of them exits, which for a started server means its whole lifetime.
- **Prefixes must keep the fd through the tmux exec.**
  - `pass_fds` only covers the immediate exec. A prefix could close the fd, or run tmux through another process with `close_fds=True`, and the server would then start without SH.
  - So GuardedTmux accepts exactly two launchers: `None` (tmux runs directly), and `GateLauncher`, the shell gate from test 3, built by `tmux_guard`.
    - The gate's script runs `"$@"` from `sh`, which passes every open fd through to tmux.
    - Any other launcher raises WatchdogError in the constructor, before anything runs.
  - Production launchers (systemd-run) never reach a guarded start: the serve test stubs `cli.tmux_launcher` to `None`, and the launcher unit tests mock `subprocess.run` (see the exemption below).
- The review's suggestion was to hold SH "until the command returns". That isn't enough: SIGKILL of pytest mid-launch is exactly the case this design is for.
  - If only pytest held SH, it would be released while a server is still bound but not listening. That reopens the r4 hole.
  - With the fd inherited, the server holds SH itself, whatever happens to pytest or the client.
- The lock path comes from the `-S` path's parent, so it lives in the same root as the socket.
  - After the rename, the open fails with ENOENT. The launcher raises WatchdogError and nothing runs.
- Every test-side invocation takes SH, not only starts. It costs one open and one flock, and the argument then doesn't depend on the CMD_STARTSERVER table staying as it is.

**The injection point: a test subclass on a no-behaviour-change seam.**
- **The production seam.** In `src/heterodyne/tmux.py`, both `subprocess.run` call sites (`_run`, and the launcher branch of `new_session`) call one new private method, `_exec(argv, *, input, timeout)`.
  - `_exec` calls `subprocess.run(argv, input=input, capture_output=True, timeout=timeout, check=False)`.
  - That is the same call as today, so behaviour and arguments are unchanged. Production gains no parameter and no test-only path.
  - All exception handling stays in the callers, exactly as today:
    - `_run` maps a non-zero exit with `check` to `TmuxError(f"tmux {args[0]} failed: <stderr>")`, and lets `TimeoutExpired` and `OSError` propagate (`paste` maps them itself);
    - the launcher branch maps `OSError` and `TimeoutExpired` to `TmuxError(LAUNCHER_FAILED)`, and maps a non-zero exit to the same error.
  - **Pinning tests, written before the extraction** and passing both before and after it. They patch `subprocess.run` and record:
    - argv, `input`, `timeout` (15 for `_run`, 30 for the launcher branch), `capture_output=True` and `check=False`;
    - each of the mappings above, for each caller, including `paste`'s `TmuxError` versus `TmuxPasteUncertain` split.
- **The test side.** `tests/tmux_guard.py` adds `GuardedTmux(Tmux)`, which overrides only `_exec` to wrap the call as above. Its single public entry is `new_test_tmux(launcher=None) -> GuardedTmux`, with a fresh socket path in the root.
  - The `tmux` fixture, the Harness and the child-prelude bodies use it.
  - **The serve test.** Its `cli.Tmux` monkeypatch returns the Harness's own guarded `h.tmux`: `lambda name, launcher=None: h.tmux`, with an assertion that `launcher is None`.
    - The test shares that server on purpose. It asserts `h.tmux.has_session(SESSION)`, and `drop_tmux(h)` tears it down.
    - `cli.tmux_launcher` is already stubbed to `None` there, so no prefix is involved.
    - A fresh socket would start a second server, which would fail the assertion and leave Harness cleanup aimed at the wrong server.
  - `new_test_socket_path()` becomes private (`_new_socket_path`). Tests that need the path read `t.socket_path`.
  - The raw `tmux -S` queries in the child prelude (`tmux_out`) go through a guarded helper, `guarded_tmux(sock, *args)`, which uses the same SH wrapping.
- **Why not a keyword-only hook.**
  - A hook would add a constructor parameter that production never passes, which is test-only surface on a production class.
  - It would also have to carry `pass_fds` into both call sites. That is the same seam, plus a public API.
  - Overriding `_run` and `new_session` without a seam would copy the launcher branch into tests, and the copy would drift.
- **Enforcement: a source scan, which is a tripwire for mistakes, not a proof.**
  - **What it parses.** It parses (with `ast`) every `*.py` module under `tests/`, except for the trusted paths below. It also parses every string constant in those modules that is valid Python, so the generated child-pytest scripts are covered.
  - **Trusted paths**, an exact list of three, skipped by name:
    - `tests/tmux_watchdog.py`, the watchdog's command plumbing. Its raw `tmux -S <path> display-message/kill-server` must stay outside SH, so that it can kill servers that hold SH.
    - `tests/tmux_guard.py`, the guard itself, which defines and uses the private helpers.
    - `tests/data/tmux_scan_specimens.txt`, the scanner's specimens. Each one is a block headed `# specimen: <rule id> <expect: flag|pass>`. It isn't a `.py` file, so pytest never imports or collects it, and it is still named explicitly so that the skip can't widen.

    `tests/live/` is also out of scope; another worker owns it.
  - **Rules.** Each rule has an id, and it fails on:
    - `alias-socket-path`: a call to `heterodyne.tmux.Tmux` under any name that passes `socket_path=`. "Any name" includes `import ... as` aliases, module attributes (`tmux.Tmux`) and simple assignments (`T = Tmux`).
    - `kwargs`: the same call with `**kwargs`.
    - `raw-tmux-S`: a list or tuple literal, or call arguments, that start with the constant `"tmux"` and contain `"-S"`.
    - `private-ref`: any reference to `_new_socket_path`, `_open_parent` or `_open_lock_at`.
  - **Regression 8's plumbing lives in the guard.** `tmux_guard.begin_lock_open(socket_path)` runs `_open_parent` and returns a `finish()` callable, which runs `_open_lock_at` with `LOCK_FLAGS`. That gives regression 8 a public, non-starting entry point and leaves `private-ref` without exceptions.
  - **The exemption is narrow.** The fully mocked selector and launcher tests replace `subprocess.run` with a recorder, so no tmux runs; they are listed as exact (module, function) pairs. Findings inside those function bodies are ignored.
    - The scan fails if a listed function is missing, or no longer patches `subprocess.run`, so the list can't go stale.
  - **What it can't see:**
    - construction through `getattr`, `exec` or `eval`, or a string that isn't valid Python;
    - a tmux command built at run time, for example `["tm" + "ux", ...]`, or a socket path passed in a variable to a list that doesn't start with `"tmux"`;
    - tmux started from shell scripts or other non-Python files;
    - any process that runs tmux on its own.

    None of these exist today. An unguarded start that slips past the scan is the residual risk in A5.
  - **Scan tests.** The scanner is a function, `scan(relpath, source) -> findings`, so each case is a (path, source) pair.
    - **Negative cases**, at least one per rule, each flagged under an ordinary path such as `tests/test_x.py`:
      - `alias-socket-path`, in three forms: an `as` alias, `tmux.Tmux`, and `T = Tmux`;
      - `kwargs`: `Tmux(**kw)`;
      - `raw-tmux-S`, in three forms: a list in `subprocess.run`, a tuple, and positional call arguments;
      - `private-ref`, once for each of the three names;
      - every one of the above inside a child-script string constant.
    - **Positive cases**, one per exception:
      - the watchdog's raw `tmux -S ... kill-server` passes under `tests/tmux_watchdog.py`, and the same source is flagged under `tests/test_x.py`;
      - a private-helper reference passes under `tests/tmux_guard.py`, and is flagged elsewhere;
      - the specimen file is skipped in the tree scan, but each `flag` specimen in it, scanned under `tests/test_x.py`, gives exactly its declared rule id;
      - a flagged specimen scanned under `tests/data/untrusted.py` is still flagged. This catches widening the specimen skip from the exact file to `tests/data/`;
      - an exempted mocked selector test passes. The same body in an unlisted function is flagged, and a listed function that has stopped patching `subprocess.run` fails the scan.
    - The tree scan of the real repository passes.

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
  1. If any earlier step recorded `replaced` or `launch lock missing`, stop: unlink nothing, keep `dead`, and set `closed: false`.
  2. Take EX non-blocking. If it is busy, retry next pass; at the deadline it is `unresolved` (`launch lock held`).
  3. Under EX, repeat the identity check: `lstat(dead/launch.lock)` must match the recorded `(st_dev, st_ino)`. If it doesn't, record `replaced`, LOCK_UN, and stop as in step 1. This way the watchdog can't lock the original inode and then unlink a replacement.
  4. Still holding EX on the original fd, unlink `launch.lock`, then `rmdir(dead)`. Only after the rmdir attempt does it LOCK_UN and close.
     - Keeping that fd open through the rmdir also stops the recorded inode number from being reused while it is still being compared against.
  5. If the rmdir fails for any reason (ENOTEMPTY means something appeared after the unlink; see A6 regressions 8 and 12), that is terminal:
     - leave every entry where it is;
     - list each one (name, type, `(st_dev, st_ino)`) in `unresolved` with reason `appeared after final unlink`;
     - set `closed: false`;
     - remove nothing more, and end the closure loop.

     There is no recovery sweep. Any further removal would need EX, which is only available on the original inode, and that inode no longer has a path. Reopening a replacement would break the original-inode invariant, and removing without EX would add an unserialized path.

  **What this step does and doesn't establish.**
  - EX excludes current SH holders, and guarded pending binds stay serialized against every removal.
  - A guarded start that comes after closure can't resolve the original socket parent: `root` was renamed, and `dead` is gone or going.
  - The process that holds the unlinked inode may still exist (A4 case 3). So closure does not mean that no guarded process remains.
  - Only a successful `rmdir(dead)` establishes filesystem closure. That is what `closed: true` reports.
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
   - (c) Replacement in a directory that holds only the lock. `dead` contains only `launch.lock`, and the test replaces it with a new inode just before the final step (through the `unlinking()`-style test hook). Then:
     - the replacement is not unlinked;
     - `dead` is kept;
     - the reason is `launch lock replaced`;
     - `closed` is false.
   - (d) An earlier replacement is honoured. A replacement detected during a socket removal stops the final step even if the path has been restored by then.
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
7. **The seam and the scan.** These are the pinning tests and the source-scan cases from A2.
8. **Delayed final-component create** (deterministic, in-process, real filesystem). This is the only gap between parent and final-component lookup that matters.
   - The sequence:
     - before the rename, the test calls `finish = begin_lock_open(socket_path)`;
     - the watchdog renames and runs closure;
     - from a `before_rmdir` test hook, so after the final unlink and before `rmdir`, the test calls `finish()`.

     The test makes no direct calls to the private helpers.
   - Correct code: the open fails with ENOENT, so the launcher raises WatchdogError and runs nothing; `rmdir` succeeds and `closed` is true.
   - With O_CREAT in `LOCK_FLAGS`: the open creates a new `launch.lock`, `rmdir` fails with ENOTEMPTY, and the test fails.
   - A second case creates `launch.lock` directly at that point, as the delayed `O_CREAT` would. Regression 12(a) gives the expected result.
9. **Prefixes.**
   - A launcher outside the two allowed ones raises WatchdogError and runs nothing. The test's launcher is a prefix that closes inherited fds (`python -c` that re-execs tmux with `close_fds=True`).
   - The allowed gate passes the fd through: after a gated real start, the server process itself holds the launch-lock inode.
     - The check compares `(st_dev, st_ino)` with `os.stat(launch.lock)`, never the inode alone.
       - **Linux:** `os.stat` each `/proc/<pid>/fd/<n>` for every `n` in `/proc/<pid>/fd`.
       - **macOS:** `lsof -n -P -a -p <pid> -F fDi`.
         - **Parsing.** The parser reads lsof's `-F` output line by line, as field records.
           - A `p` line starts a process, and an `f` line starts a file record. Both reset the per-file state, so `D` and `i` count only when they belong to the same `f` record.
           - A candidate is a record whose `f` is a numeric fd and which carries both `D` and `i`.
           - A record that lacks `D` or `i`, or whose `f` isn't numeric (`cwd`, `txt` and so on), is a non-candidate, not malformed output. lsof legitimately omits fields for some files.
         - **Failures:**
           - a value that is present but malformed, such as a non-hex `D`, a non-decimal `i`, or an `f` that isn't numeric yet looks like a number;
           - a non-zero lsof exit;
           - no `p` record for the pid;
           - no candidate matching the expected `(st_dev, st_ino)`.
         - The parser has unit tests on synthetic output:
           - `D` and `i` split across two `f` records, which must not match;
           - records with missing fields, which are skipped;
           - each malformed value, which fails;
           - the wrong-device case.
       - A positive control proves the mapping on that platform, notably that lsof's `D` equals `st_dev`. The test opens `launch.lock` at a known fd in a helper process it controls and requires the same inspection to find exactly that `(fd, dev, ino)`.
     - An inspection error fails the test explicitly; it doesn't count as "not held" and doesn't skip. Errors include a missing lsof, a non-zero exit, an unparsable record, a missing `/proc` entry, and a `stat` error.
     - The server stays alive throughout: its pane sleeps, and the test kills it only in a `finally` after the inspection. The test also checks that the PID is still alive after the inspection, so a PID that died part-way through can't pass as empty.
10. **Children release the fd** (real tmux). A guarded server runs four children:
    - a pane;
    - a `run-shell -b` job;
    - a `pipe-pane` command;
    - a `set-hook` hook (`run-shell`).

    Each child runs a small Python probe. The probe:
    - enumerates its actually open fds by listing `/proc/self/fd` on Linux and `/dev/fd` on macOS, with no numeric bound;
    - `fstat`s each one;
    - writes the `(fd, st_dev, st_ino)` of any match with the launch lock;
    - also checks the known inherited fd number (the guard's fd number, passed in the environment) directly.

    EBADF is accepted in exactly two places:
    - from the direct check of the known fd, where it is the expected sign that the child closed it;
    - for the listing's own directory fd.

    Any other enumeration or `fstat` error, including any other errno from the direct check, makes the probe exit non-zero, and the test fails. An error never reads as "none".
    - All four must report none.
    - The server itself must hold it (as in regression 9).
    - **Negative controls.** The same probe runs in a helper process that holds the launch lock at fd 1500 (`dup2`), and again at the known fd number. Both must report the match. A probe with a bound below 1500 fails the first control.
11. **Mixed survivor and healthy server** (scripted, with real flocks: "servers" are helper processes that hold SH).
    - A survives kill-server, so it keeps SH. B dies on its kill-server. C is first discovered on a later pass.
    - Every pass still issues kill-server and the death wait to every live server, whatever happened to earlier removals:
      - B's removal is `held` (A holds SH), yet C is killed and proven dead;
      - B's and C's PIDs are dead at the end.
    - At the deadline:
      - A is `survived`;
      - B and C are `unresolved` (`held`) with their sockets kept;
      - `closed` is false.
    - A mutant that skips the remaining kills once a removal is `held` must fail.
12. **Something appears after the final unlink** (deterministic, real filesystem, through the `before_rmdir` hook).
    - (a) `launch.lock` is recreated at that point.
    - (b) Another entry is created at that point: a stale socket that would otherwise be removable, so that any later removal would be visible.

    In both cases:
    - rmdir fails;
    - the entry is left in place and listed in `unresolved` with reason `appeared after final unlink`;
    - `closed` is false and `dead` is kept;
    - the original lock fd was held through the rmdir attempt. Before the final unlink, the test opens a second descriptor on the original inode by path. This is its own `open()`, a separate open file description, not a `dup()`. In the hook, `flock(LOCK_EX|LOCK_NB)` on that descriptor must fail with EWOULDBLOCK. Reopening the path in the hook would fail, or would reach the replacement inode.
    - nothing more is unlinked: a recorder on the watchdog's unlink records no calls after the hook, and the loop ends without another pass.

**Platforms.** GitHub-hosted macOS runners are banned (2026-10-08), so CI is Linux only.
- **The tests stay portable.** The real-tmux lifetime tests are regressions 3(b), 5, 9 and 10, plus test 3. They keep their macOS branches: lsof in regression 9, `/dev/fd` in regression 10, and `ps` in place of `/proc`.
  - When inspection is unavailable, they fail rather than skip: a missing lsof, `/dev/fd` that can't be listed, or a failing `ps`.
  - Under `HZ_REQUIRE_TMUX=1`, a missing tmux still fails the session.
- **Linux evidence** comes from CI, with the implementation.
- **macOS evidence** must come from a non-hosted source, either Liam's self-hosted runner or a manual run on his laptop.
  - The run is the full suite with `HZ_REQUIRE_TMUX=1`.
  - Its result, the commit, and the macOS and tmux versions are recorded in the implementation's review record.
  - Until such a run exists, macOS behaviour, notably the lsof `D`-to-`st_dev` mapping and `/dev/fd` enumeration, is **open evidence**, not established.
- This also supersedes the macOS CI references in §3 (CI installs tmux "on both platforms") and §4 (the "90 s on macOS CI" budget). The 90 s budget still applies to macOS runs from those other sources.

**Mutation targets** (`run5.py`/`results-r5.md`):
- remove even though EX failed;
- take EX blocking;
- drop `pass_fds` (pytest-only SH);
- unlink `launch.lock` on every pass;
- skip the identity check;
- let the watchdog create `<p>.lock`;
- let `sweep_lock` bypass `remove`;
- skip the identity check at the final step;
- ignore an earlier `replaced` at the final step;
- O_CREAT in `LOCK_FLAGS` (killed by regression 8; no longer classed as equivalent);
- an early LOCK_UN in the launcher after the command returns (killed by regression 5);
- `GuardedTmux` accepting any launcher (killed by regression 9);
- skip the remaining kills after a `held` removal (killed by regression 11);
- release EX before the rmdir (killed by regression 12's held-lock assertion);
- a recovery sweep after a failed final rmdir (killed by regression 12(b));
- a bounded probe loop, `range(3, 1024)`, in place of enumeration (killed by regression 10's fd-1500 control);
- comparing the inode alone in regression 9's inspection (killed by a unit test of the matcher that feeds it synthetic `/proc` stats and lsof records with the right inode on the wrong device, which must not match);
- each trusted-path or exemption entry widened to a directory (killed by the positive and negative case pairs);
- each source-scan rule removed (each is killed by its negative case);
- the serve factory returning a fresh server (killed by the serve test's `has_session` assertion);
- `GuardedTmux` not overriding `_exec`.

If a mutant can't be killed deterministically, it is recorded in `results-r5.md` as surviving open evidence, not as equivalent.
