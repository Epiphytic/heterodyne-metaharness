All six r3 findings are addressed. Tmux’s shared startup code uses `<socket>.lock` with `flock`, retaining it across bind and listen; this works on Linux and macOS. See [client locking](https://raw.githubusercontent.com/tmux/tmux/3.4/client.c), [server startup](https://raw.githubusercontent.com/tmux/tmux/3.4/server.c), and [Apple’s fork inheritance semantics](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/flock.2.html). The closure loop and daemon-thread reaping introduce no blocking defect.

1. **[NON-BLOCKING] Test 5’s classification contradicts the cleanup rules.** [design-tmux-leak.md:79](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:79). With the server already dead, PID discovery fails and the refused connection follows the `stale` branch, not `killed`. Cleanup still succeeds.

   **Fix:** Expect `stale` populated, `closed: true`, and empty `survived`/`unresolved`. Retain the summary as specified.

2. **[NON-BLOCKING] Retention needs an explicit closure condition.** [design-tmux-leak.md:43](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:43). A held `.lock` without a socket can reach the deadline without populating any outcome list. “Remove `run`” then conflicts with the earlier requirement to preserve `dead`. Also, transient `unresolved` or `survived` entries should disappear when later passes resolve them.

   **Fix:** Remove `run` only when closure succeeded and all outcome lists are empty. Report held lock-only entries as unresolved, and make `survived`/`unresolved` describe the final state.

APPROVE
