**Verdict: REVISE.** This was the one full high-effort review, run with the built-in `code-review` skill (not the plugin), with Claude Opus reviewing the Codex-authored design. I checked that `design.md` matches the pin (`1c6d8ee2…fe29a`). The review read the design, `evidence.json`, `known-process-scope-metadata.json`, the original ADR, the disabled collector, `agent.py`/`tmux.py` and the tmux 3.4 sources, plus a few read-only `/proc` and `/tmp/tmux-1001` checks. It ran no tests, edits, process controls, services, live DB, queue or messages. No second review or validation was run.

**Blockers: as written, the amendment can't finish a clean cessation or a rollback**

1. **Step 3 can never pass (`design.md:32`).** It requires the private socket to be gone. tmux 3.4 leaves the socket file behind when the server exits, and step 5 forbids cleaning it up. `/tmp/tmux-1001` already holds 337 leftover `hws-e2e-*` socket files with no server running on any of them. **Fix:** count it as gone when tmux positively reports no server running on that socket and the socket inode is unchanged. The product's own `_no_server()` already uses that test.
2. **Step 4 can never pass (`:33`, `:24`).** Its checks run only after the server has exited, but they require identities and ancestry to be unchanged, and by then they have changed. Survivors get re-parented to the user `systemd` (PID 3579). **Fix:** survivors must be a subset of the captured {pid, start_ticks, uid, exe}, the cgroup inode must be unchanged, and the only parent change tolerated is a move to the user manager.
3. **Rollback can't stop the new 717 agent (`:36` vs ADR line 77).** Only the two historical scopes are pinned, and new scopes are excluded as targets. The original same-scope rule can never hold under tmux 3.4. So if acceptance fails, the rollback stop HOLDs with a live 717 writer still running during the Marmot outage. **Fix:** a class rule, freshly captured each time: the exact server scope from the launcher, plus pane scopes whose systemd Description and parent PID tie them to that server.

**Important**

4. **The TERM consent can't be given when it's needed (`:33`).** By step 4, admind was stopped in step 1, so a Marmot ask is impossible. ADR §3.4 and the controller rules also forbid terminal-only decisions. **Fix:** say this is advance consent, given inside the deployment ask before the outage.
5. **The server-scope TERM is either unreachable or unsafe (`:33`).** If the server is still alive, step 4's condition is false, so the TERM can't fire. If the server exited, anything left in its scope wasn't captured, which line 24 says means HOLD. **Fix:** drop that target, or define the single case where it applies.
6. **`kill --kill-whom=all` signals whatever is in the cgroup at signal time (`:33`).** That includes any child forked after the last check. **Fix:** re-read membership right after the signal, and treat any extra PID that got signalled as HOLD/incident.
7. **"Escaped descendant" can't be detected as specified (`:24`).** cgroup membership and parent-PID walks both miss a writer started with `systemd-run --user` or under another tmux socket. **Fix:** name a concrete detection method, or state that this relies only on the ADR §5A.4 writer inventory.
8. **No replacement checker is specified (`:11`).** The design says the collector can't be used unchanged, but names nothing to replace it, so the refresh would be done by hand during the outage. **Fix:** a minimal collector change that allows exactly the pinned pane scope and checks the tmux-spawn unit's Description, with its hash pinned in `evidence.json`.

**Minor**

9. **Step 3 has no time bound (`:32`).** **Fix:** a bounded observation window, e.g. 15s to match step 4.
10. **"Executable identity" is undefined (`:24`).** A routine binary update gives a `(deleted)` exe path: an exact path match HOLDs, and a path match that strips the suffix accepts a replaced binary. **Fix:** define it as the path plus the exe's dev/inode, and say how a deleted exe is handled.

None of these findings disputes the core premise: the separate pane scope is real tmux 3.4 behaviour, not something unknown.

**Next:** one design fix pass for the author (Codex) on findings 1–10, then publish for human approval. The TERM-only backup in steps 3–4 still needs its own separate approval; no existing approval covers it.
