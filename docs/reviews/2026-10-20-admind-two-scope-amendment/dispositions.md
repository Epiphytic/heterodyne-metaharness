# Sole HIGH review: ordered author dispositions

Original reviewed draft SHA256: `1c6d8ee2e6d93ce5b5ef448e1f4f0af7383f0c5022e9b80da3b1ba23701fe29a`. Original wrapper SHA256: `30cfe01258c76d61b62997a5455a99305efad262c56458431ffc32ad2e743b91`. Actual verdict REVISE remains unchanged. Incorporation once; no second review/validation, live checks, controls or authority inferred. All dispositions are author changes, awaiting genuine human amendment consent. Collector implementation/qualification remains an explicit execution prerequisite.

## Finding 1

1. **Step 3 can never pass (`design.md:32`).** It requires the private socket to be gone. tmux 3.4 leaves the socket file behind when the server exits, and step 5 forbids cleaning it up. `/tmp/tmux-1001` already holds 337 leftover `hws-e2e-*` socket files with no server running on any of them. **Fix:** count it as gone when tmux positively reports no server running on that socket and the socket inode is unchanged. The product's own `_no_server()` already uses that test.


Author disposition: incorporated. Stale unchanged socket plus exact positive no-server diagnostic is allowed; process/cgroup cessation remains independently required. No unlink.

## Finding 2

2. **Step 4 can never pass (`:33`, `:24`).** Its checks run only after the server has exited, but they require identities and ancestry to be unchanged, and by then they have changed. Survivors get re-parented to the user `systemd` (PID 3579). **Fix:** survivors must be a subset of the captured {pid, start_ticks, uid, exe}, the cgroup inode must be unchanged, and the only parent change tolerated is a move to the user manager.


Author disposition: incorporated. Survivor subset pins retain boot/start/UID/executable and cgroup identity; only captured-parent or captured actual user-manager reparenting is allowed.

## Finding 3

3. **Rollback can't stop the new 717 agent (`:36` vs ADR line 77).** Only the two historical scopes are pinned, and new scopes are excluded as targets. The original same-scope rule can never hold under tmux 3.4. So if acceptance fails, the rollback stop HOLDs with a live 717 writer still running during the Marmot outage. **Fix:** a class rule, freshly captured each time: the exact server scope from the launcher, plus pane scopes whose systemd Description and parent PID tie them to that server.

Author disposition: incorporated. One finite rollback pair is captured before stop using actual qualified launch, sole session/pane, exact Description/PPID and complete identities. No prefix-only retargeting.

## Finding 4

4. **The TERM consent can't be given when it's needed (`:33`).** By step 4, admind was stopped in step 1, so a Marmot ask is impossible. ADR §3.4 and the controller rules also forbid terminal-only decisions. **Fix:** say this is advance consent, given inside the deployment ask before the outage.


Author disposition: incorporated. Optional TERM is explicitly included in genuine advance consent before outage; no deferred/terminal decision.

## Finding 5

5. **The server-scope TERM is either unreachable or unsafe (`:33`).** If the server is still alive, step 4's condition is false, so the TERM can't fire. If the server exited, anything left in its scope wasn't captured, which line 24 says means HOLD. **Fix:** drop that target, or define the single case where it applies.


Author disposition: incorporated. Server-scope TERM removed. Only one attributed pane-scope TERM after positive server cessation may be authorized per finite stop branch.

## Finding 6

6. **`kill --kill-whom=all` signals whatever is in the cgroup at signal time (`:33`).** That includes any child forked after the last check. **Fix:** re-read membership right after the signal, and treat any extra PID that got signalled as HOLD/incident.


Author disposition: incorporated. Immediate post-signal membership read and incident/HOLD for new members; non-atomic fork race and observation limitation explicitly disclosed.

## Finding 7

7. **"Escaped descendant" can't be detected as specified (`:24`).** cgroup membership and parent-PID walks both miss a writer started with `systemd-run --user` or under another tmux socket. **Fix:** name a concrete detection method, or state that this relies only on the ADR §5A.4 writer inventory.


Author disposition: incorporated. No escape-detection guarantee: finite attribution relies on original ADR writer inventory/no-start agreement; observed escape HOLD, no follow-and-kill.

## Finding 8

8. **No replacement checker is specified (`:11`).** The design says the collector can't be used unchanged, but names nothing to replace it, so the refresh would be done by hand during the outage. **Fix:** a minimal collector change that allows exactly the pinned pane scope and checks the tmux-spawn unit's Description, with its hash pinned in `evidence.json`.

Author disposition: incorporated. Concrete root-owned disabled initial-capture helper pinned at ff9dbd2eaf3298ac5a73a58800c34476bf26535f3523853387def2309eed3090 and root r02 capture disclosed. No author execution. Cessation and fresh rollback collector remain specified, not completed or qualified; actual final hash pin required before execution.

## Finding 9

9. **Step 3 has no time bound (`:32`).** **Fix:** a bounded observation window, e.g. 15s to match step 4.


Author disposition: incorporated. Post-kill-server and post-TERM observation windows each bounded15s, with bounded reads and monotonic deadlines.

## Finding 10

10. **"Executable identity" is undefined (`:24`).** A routine binary update gives a `(deleted)` exe path: an exact path match HOLDs, and a path match that strips the suffix accepts a replaced binary. **Fix:** define it as the path plus the exe's dev/inode, and say how a deleted exe is handled.

Author disposition: incorporated. Executable identity is exact path plus device/inode; deleted/inaccessible/unstable/replaced identity HOLDs.

## Exact original review wrapper

```text
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
```

## Exact reviewed draft retained before incorporation

```markdown
# Narrow amendment: private admind server and pane scopes

Author draft only. This document does not authorize an operation. Preserve the genuine first 717 deployment approval, its exact ADR/checklist and all execution observations. Seek a separate genuine approval for the finite scope correction below before relying on it. No second review or approval has been claimed here.

## Why the same-scope precondition cannot hold

The deployed product launches the private tmux server inside `heterodyne-admind-tmux-<12 hex>.scope` through `tmux_launcher()` and the single wrapped `Tmux.new_session()` invocation. The installed Ubuntu tmux 3.4-1ubuntu0.1 binary independently creates a new systemd scope for each pane. Upstream 3.4 `spawn.c:386–396` calls `systemd_move_pid_to_new_cgroup(new_wp->pid, ...)`; `compat/systemd.c:72–212` creates `tmux-spawn-<UUID>.scope`, binds the pane PID through the PIDs property, and inherits the parent's slice. This moves the pane out of the server scope while preserving its process-parent relationship. It is tmux's compiled cgroup integration, not a second product launch or evidence of a different native admin session.

Installed binary strings contain the exact scope-format string and systemd StartTransientUnit machinery; the installed package changelog records enabled systemd integration. Retained upstream sources explain the mechanism but are not a byte-for-byte Ubuntu source rebuild attestation. The actual local process and cgroup metadata independently corroborate this mechanism.

Replace only the first approval's same-cgroup requirement with two independently pinned, exact private scopes. Do not weaken the requirement to a shared UID, scope-name prefix, arbitrary tmux pane, or arbitrary descendant outside these scopes. The disabled metadata collector currently refuses different pane/server cgroups and allows only the server scope prefix; it cannot be used unchanged to claim this proof.

## Exact finite observation and allowed targets

At the retained read-only observation (2026-10-20T06:12:02Z), boot ID and full facts are in `known-process-scope-metadata.json`:

| Role | Exact scope | Original PID / start ticks | Observed members |
|---|---|---|---|
| Private server | `heterodyne-admind-tmux-f65115902f59.scope` | 3165315 / 287754583 | 3165315 |
| Sole admin pane and its descendants | `tmux-spawn-d269c90c-f13a-4a04-ac92-ca036a222cb9.scope` | 3165316 / 287754584 | 3165316, 3165398, 3165400, 3165513, 3165592, 3165596, 3165597 |

All observed UIDs are 1001. The pane's PPID is the original server. Each other pane-scope member's observed ancestry terminates at that pane without leaving the captured set. Scope cgroup device/inode pairs are 31/28353698 and 31/28353748. Membership was read twice and stable during this bounded observation. These are historical identity facts, not current quiescence, application-idleness, lease, or future absence proof.

Before any operation, refresh exact private socket identity at `/tmp/tmux-1001/heterodyne-admind`, sole admin session and sole live pane, both original process boot/start/UID/executable identities, exact systemd ControlGroup mappings and recursive membership. Bind the two exact cgroup directory identities. Require the captured server/pane ancestry relation and every member to be independently attributable to that private admin agent. An unexpected member, changed original process, new scope, escaped descendant, unresolved process, socket replacement, or conflicting mapping is HOLD; no retargeting by name or fresh PID is allowed. Read-only metadata must omit argv, environment, raw native session/account/nonce and transcript data. The existing safe bound of 32 cgroup directories and 64 members suffices for this captured set; exceeding it is HOLD.

## Proposed finite cessation sequence

Retain every original prerequisite: fresh idle/completed current turn, no unsafe in-flight or pending work, valid native session/account/replace_pending state and launch headroom, identified writer entrypoints/principals with deployment-window no-start agreement, and no concurrent root writer calls. A sleeping process or an empty FD scan proves none of these. Unrelated default-socket or same-UID shells are not automatic termination targets.

1. Execute the already approved bounded clean daemon stop first; require the original clean inactive/dead/MainPID0 and exit-result proof. Unknown, timeout, forced termination or restart is HOLD.
2. Recheck the exact private socket and both pinned scopes/process identities and membership, then execute the original single `timeout 15s tmux -S /tmp/tmux-1001/heterodyne-admind kill-server`. This request targets only the positively bound private server. Preserve its result and before/after observations.
3. Observe both exact known scope subtrees, both original process identities and every captured member, the private socket and daemon state. Success requires positive cessation of all captured writers, both scoped subtrees empty/removed with no surviving captured descendants, and the private socket absent. A missing socket, client error, inactive unit label, PID reuse, or successful command alone is insufficient. Do not follow any process into an unrelated scope and terminate it.
4. If the private server exits but attributed members remain in either exact captured scope, the first approval does not establish their cessation. This amendment proposes one additional, separately consented, bounded TERM-only operation per surviving exact scope, after fresh unchanged identity/membership checks and the same application-idle prerequisites. The literal proposed operations are `timeout 5s systemctl --user kill --kill-whom=all --signal=SIGTERM heterodyne-admind-tmux-f65115902f59.scope` and `timeout 5s systemctl --user kill --kill-whom=all --signal=SIGTERM tmux-spawn-d269c90c-f13a-4a04-ac92-ca036a222cb9.scope`; invoke only the still-present eligible target. This is a new finite operational permission requiring explicit approval, not an interpretation that the old approval already permits extra signals. No automatic `systemctl stop` escalation, SIGKILL, wildcard, numeric-PID kill, default tmux control, repeated TERM, or newly discovered scope is authorized. Observe for at most 15 seconds after the permitted signal; surviving/unknown/escaped writers HOLD. If the operator does not approve this TERM-only extension, step3 noncessation simply HOLDs without step4.
5. Preserve all per-step evidence and original identities. No cleanup/recreation of sockets, native DB edits, session replacement, reset counters, `!new`, replay, or synthetic ownership/exclusion lease. Proceed to the original deployment steps only after both independent scope proofs and every unchanged writer-exclusion prerequisite hold. A timeout helper is not writer-cessation evidence.

Restart remains the original conditional route: same stored native session, genuine resumed audit and SessionStart acknowledgement, available launch headroom. New tmux/server/pane/process/scope identities are expected after the authorized restart and must be captured afresh; they are not termination targets under this finite amendment. Rollback and newest-DB preservation remain exactly the first approved design. Fulfillment remains disabled; no migration, provider send, queue/native custody activation or source-policy change is added.

## Source references and publication

- [Deployed product launcher](https://github.com/Epiphytic/heterodyne-metaharness/blob/5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156/src/heterodyne/admind/agent.py#L38), [tmux wrapper](https://github.com/Epiphytic/heterodyne-metaharness/blob/5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156/src/heterodyne/tmux.py#L114), and [operator guide](https://github.com/Epiphytic/heterodyne-metaharness/blob/5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156/docs/admind.md#L280). Exact retained-source hashes are in evidence.json.
- [Upstream tmux 3.4 spawn boundary](https://github.com/tmux/tmux/blob/3.4/spawn.c#L386), [systemd scope producer](https://github.com/tmux/tmux/blob/3.4/compat/systemd.c#L72). Exact downloaded bytes and SHA256 are retained; version tag is descriptive, retained content hashes identify this observation.
- The approved original ADR and disabled collector are hashed in evidence.json. Root must supply the actual immutable published original approval/design link when publishing this amendment; no URL or human disposition is invented here.

This is a narrow author proposal correcting the operational scope model. It is not a review, production source patch, executed stop, completed deployment, or change to native authority/history. All existing task gates remain.
```
