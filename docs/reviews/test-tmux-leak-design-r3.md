1. **[BLOCKING] Rename does not finish in-flight binds.** [design-tmux-leak.md:28](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:28). Tmux correctly does **not** create the parent for `-S`. However, Linux resolves the parent before creating the socket, and renaming a directory within its parent does not lock the source directory. The resulting possible interleaving is: bind resolves `root`; rename completes; the watchdog enumerates `dead`; bind creates a socket in that renamed directory afterward. A single sweep can miss it. This is inferred from [Linux’s bind implementation](https://raw.githubusercontent.com/torvalds/linux/v6.8/net/unix/af_unix.c) and [pathname creation and rename locking](https://raw.githubusercontent.com/torvalds/linux/v6.8/fs/namei.c). R2 finding 1 remains partially unresolved.

   **Fix:** Keep rename, but require an actual closure condition. For example, repeat cleanup until `rmdir(dead)` succeeds; handle `ENOTEMPTY` by sweeping again, and preserve/report uncertainty on deadline expiry. Account for tmux’s `.lock` files. Successful directory removal closes creation through previously resolved directory references.

2. **[BLOCKING] A refused connection with an unknown PID does not establish death.** [design-tmux-leak.md:38](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:38). Tmux calls `bind()` before `listen()`. If paused between them, discovery and `kill-server` can both encounter refusal. The proposed rule then declares it dead and unlinks its socket. When resumed, the server can finish starting and receive launch commands through the original client’s separate socketpair, leaving an unreachable live server. [Tmux socket startup](https://raw.githubusercontent.com/tmux/tmux/3.4/server.c), [client/server socketpair](https://raw.githubusercontent.com/tmux/tmux/3.4/proc.c). Rename also preserves established connections; a client mid-connect can still reach the original server.

   **Fix:** Synchronize with startup before interpreting unknown-PID refusal as a stale socket—for example, use the tmux startup lock, with bounded retries. Preserve/report unresolved startup rather than unlinking. Add a deterministic bound-but-not-listening case. R1 finding 5 remains partially unresolved.

3. **[BLOCKING] The SIGKILL assertions contradict the retention policy.** [design-tmux-leak.md:78](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:78). These tests necessarily require the watchdog to kill servers, but line 47 requires retaining `run` whenever anything was killed. Consequently, correct cleanup fails the assertion that `run` disappears; the process-group test inherits this contradiction.

   **Fix:** For successful emergency cleanup, assert dead server/pane PIDs, removed sockets, and a retained summary with `killed` populated and `survived` empty. Require `run` removal only for cleanup that needed no killing.

4. **[BLOCKING] The fake runner still misses both verification mutations.** [design-tmux-leak.md:85](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:85). With both an accepting socket and a live PID, deleting either check leaves the other independently selecting `survived`. Neither mutation fails this test. R2 finding 6 remains unresolved.

   **Fix:** Add two asymmetric cases after a fake successful kill: a refused socket with a **known live PID**, and an accepting socket with a **dead PID**. Both must retain the socket and report `survived`; deleting the corresponding check must fail.

5. **[NON-BLOCKING] Timeout paths do not specify eventual watchdog reaping.** [design-tmux-leak.md:52](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:52). The normal readiness and teardown contracts resolve most of R2 finding 3. However, printing a timeout warning leaves an unready watchdog potentially running, or an eventually exiting cleaner unreaped in a live `pytest.main()` interpreter. Multiple sockets can also exceed the fixed 20-second wait.

   **Fix:** Terminate and reap startup failures, since no launches were permitted. For an acknowledged cleaner exceeding teardown’s wait, arrange eventual reaping while allowing cleanup to continue.

6. **[NON-BLOCKING] The launch test needs a barrier handshake and timeout margin.** [design-tmux-leak.md:80](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:80). Opening the gate immediately after killing pytest can beat the watchdog’s rename, making a correct implementation successfully launch and then clean the server. Also, the 15-second assertion budget equals discovery, kill, and death-check maxima without scheduling margin.

   **Fix:** Wait for confirmed namespace closure before opening the gate, and allow additional bounded scheduling margin on both CI platforms. The private namespace, removed signal customization, zombie handling, selector compatibility, and session-level CI enforcement otherwise resolve or make moot the remaining earlier findings.

REVISE
