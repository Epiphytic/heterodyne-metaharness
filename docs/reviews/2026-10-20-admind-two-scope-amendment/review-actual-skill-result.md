Skill "code-review" completed (forked execution).

Result:
The design has three blockers. Either step 3 or step 4 can never pass as written, and the rollback path can't stop the agent that 717 launches.

I checked the design against `evidence.json`, `known-process-scope-metadata.json`, the original ADR, the disabled collector, `agent.py` and `tmux.py`, and the retained tmux 3.4 sources. I also did a few read-only `/proc` and `/tmp/tmux-1001` checks. I ran no process or service controls.

- **Step 3 is unreachable (strongest evidence).** It requires the private socket to be gone, but tmux 3.4 leaves the socket file behind when the server exits, and the design forbids cleaning it up. `/tmp/tmux-1001` holds 337 `hws-e2e-*` socket files and no tmux server is running on any of them. The product's own `_no_server()` treats "no server running on" as meaning the server is gone, even when the socket file is still there.
- **Step 4 can't be satisfied.** It needs the same identities and ancestry as the snapshot, but it only applies after the server has exited, and by then that state has necessarily changed. Survivors get re-parented to PID 3579 (`systemd`).
- **Rollback is stuck.** ADR line 77 applies the same checks to any rollback stop. The amendment pins only the old scopes, excludes new ones, and the original same-scope rule can never hold under tmux 3.4.

```json
[
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 32,
    "summary": "Step 3 success requires 'the private socket absent', but tmux 3.4 does not unlink its socket on server exit, and step 5 forbids socket cleanup, so a clean cessation can never satisfy step 3.",
    "failure_scenario": "kill-server succeeds, the server, the pane and all 7 pane-scope members exit, and both scopes are collected. /tmp/tmux-1001/heterodyne-admind still exists as a stale socket node (as with the 337 stale hws-e2e-* sockets in /tmp/tmux-1001 with no live server). Step 3 fails, nothing may remove the socket, and the deployment HOLDs every time even though the writers are gone. The product's own _no_server() in tmux.py treats 'no server running on' (socket present, connection refused) as absence. The criterion should be that positive tmux no-server readback plus an unchanged socket inode, not socket removal."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 33,
    "summary": "Step 4 only applies after the server has exited, yet it requires 'fresh unchanged identity/membership checks', the server/pane ancestry relation (line 24) and 'both original process identities', all of which have necessarily changed by then.",
    "failure_scenario": "kill-server makes the server exit and the claude pane (3165316) gets SIGHUP. The surviving MCP children (3165398/3165400/3165513/...) are re-parented to the user systemd subreaper (PID 3579, confirmed comm=systemd), the server PID is gone and the pane PID may be gone. Every 'unchanged identity/ancestry' check fails, so the TERM extension is never eligible in the one case it was written for. If operators read 'unchanged' loosely, nothing defines which changes are allowed. It needs an explicit rule: survivors are a subset of the captured {pid,start_ticks,uid,exe}, the cgroup inode is unchanged, and a PPID change to the user manager only is tolerated."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 36,
    "summary": "The amendment drops the original same-cgroup rule but pins only the historical scopes and says new post-restart scopes 'are not termination targets'. ADR §5A line 77 applies the same stop checks to any approved rollback stop, so the preconsented rollback can no longer stop the agent 717 launches.",
    "failure_scenario": "717 installs and starts, launching a new private server in heterodyne-admind-tmux-<new>.scope with its pane in tmux-spawn-<new>.scope. Acceptance then fails and the post-mutation rollback must stop admind and its agent. The original same-scope check can never pass under tmux 3.4, and the amendment excludes the new scopes, so the rollback stop HOLDs with a live, unstopped 717 agent writer. This happens during the Marmot outage, when ADR §3.4 says no new authority may be obtained. The amendment needs a class rule (an exact server scope from the launcher plus pane scopes whose systemd Description/PPID bind to that server), freshly captured each time, not just the two historical names."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 33,
    "summary": "Step 4 says the TERM extension is 'separately consented' and implies the operator decides at step-4 time, but by then the daemon has been stopped in step 1, so Marmot/admind (the only approval channel) is down.",
    "failure_scenario": "At step 3 survivors remain and the operator is to 'approve this TERM-only extension'. The decision would have to be terminal-only or invented offline, which ADR §3.4 forbids ('No new questions or authority may be invented while offline') and so does $SERVICE_HOME/.hermes/CLAUDE.md ('Every decision goes out as a Marmot ask (admind), never terminal-only'). The design must say this consent is advance, pre-outage consent inside the deployment ask, otherwise step 4 is unusable."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 33,
    "summary": "The TERM against heterodyne-admind-tmux-f65115902f59.scope can never fire legitimately: step 4 requires the server to have exited, the server is that scope's only captured member, and any other member there is an 'unexpected member' that line 24 says is HOLD.",
    "failure_scenario": "Case A: kill-server times out and the server is still alive. Step 4's condition ('If the private server exits') is false, so the server-scope TERM isn't allowed even though that is the case it would help. Case B: the server exited but a process remains in the server scope. That process is by definition uncaptured, so line 24 says HOLD, yet step 4 would TERM it. The literal server-scope operation is either dead text or a way to signal unknown processes."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 24,
    "summary": "'escaped descendant' is listed as a HOLD condition, but there is no mechanism to detect it: cgroup membership can't see processes that left the scope, and PPID walks lose orphans re-parented to systemd --user.",
    "failure_scenario": "The admin agent (unsandboxed, bypass-permissions) earlier ran systemd-run --user, or started work under another tmux socket (e.g. hermes-workstreams). That writer is in neither pinned scope and, once its parent dies, it is no descendant of any captured PID. The refresh check and step 3 'positive cessation of all captured writers' both pass while it keeps writing. The design should state the concrete detection (e.g. a /proc scan for UID-1001 processes whose start_ticks fall after the pane start and whose ancestry crossed a captured PID) or say this relies only on the ADR §5A.4 writer inventory."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 11,
    "summary": "The design says the disabled collector 'cannot be used unchanged' (it rejects pane cgroup != server cgroup at METADATA-ONLY.DISABLED.py:223) but specifies no replacement checker, hash pin or review, so the line 24 refresh would be done by hand during the outage.",
    "failure_scenario": "At step 2 the operator must check socket inode, boot/start/UID/exe of two processes, ControlGroup mappings, two cgroup inodes, recursive membership, ancestry and the 32/64 bounds by hand, without the collector's alias/symlink/vanish/moved-during-inventory checks (lines 229-262). That invites a mistaken pass, or a different check on each run. Add a minimal collector change allowing exactly the pinned pane scope, also checking the tmux-spawn unit's systemd Description ('tmux child pane 3165316 launched by process 3165315') as a manager-side binding, and hash-pin it in evidence.json."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 33,
    "summary": "systemctl --user kill --kill-whom=all <name>.scope signals whatever is in the cgroup when systemd resolves the name, not the verified set, so any member added between the fresh check and the signal is TERMed without attribution.",
    "failure_scenario": "The fresh membership check passes with the 7 captured PIDs. During the window before the TERM, the dying playwright MCP (3165398) or uv/python (3165513) forks a child, or claude spawns a hook. kill-whom=all sends it SIGTERM, which breaks the 'never kill unknown' boundary. The design pins dev/inode but nothing re-checks that the name still maps to that inode at signal time. Require a membership re-read right after the signal and treat any extra signalled PID as a HOLD/incident."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 32,
    "summary": "Step 3 has no time bound for its cessation observation, so it is undefined when noncessation is declared and step 4 (or HOLD) starts.",
    "failure_scenario": "After kill-server returns, claude and its MCP children take a while to exit after SIGHUP/EOF. One operator moves to step 4 at T+1s and TERMs processes that were already exiting cleanly; another waits indefinitely during the outage. Step 4 has a 15s bound but step 3 has none. Specify a bounded observation window, e.g. 15s matching the kill-server timeout."
  },
  {
    "file": "717-private-scope-amendment/design.md",
    "line": 24,
    "summary": "'original process ... executable identities' is not defined (path string vs. dev/inode, handling of a ' (deleted)' suffix), and the collector uses os.readlink(exe), so a routine binary update makes the check wrong in one direction or the other.",
    "failure_scenario": "Claude Code auto-updates claude.exe under nvm, or unattended-upgrades replaces /usr/bin/tmux, during the window. readlink then returns '.../claude.exe (deleted)', an exact-path comparison fails and the run HOLDs with no way to retarget. Alternatively, a path-only comparison that strips the suffix accepts a replaced binary. State the identity tuple (e.g. path plus exe st_dev/st_ino captured at observation) and say explicitly how a deleted exe is handled."
  }
]
```

The files are in `$SERVICE_HOME/.hermes/handoff/2026-10-15/codex/autonomous-resume/717-private-scope-amendment/`.
