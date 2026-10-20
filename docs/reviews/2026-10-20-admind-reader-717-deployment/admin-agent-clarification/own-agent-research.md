# Existing admin-agent quiescence mechanism (read-only discovery)

The deployed5db source already documents a host break-glass stop path. It has no durable Marmot pause/stop command for the admin agent. `!interrupt` releases dispatch after Escape and can enable subsequent work; `!new` deletes agent_session and starts a new logical session. Neither is the required quiescence primitive.

## Exact source evidence

All references below are to the retained exact deployed5db source export recorded in inputs.json, not the newer working daemon. Agent, command table, tmux wrapper, unit and documentation bytes were independently compared with the working source; daemon bytes differ, so the deployed methods were read directly and retained separately.

- docs/admind.md:280 explicitly documents: first `systemctl --user stop heterodyne-admind`, then `tmux -L heterodyne-admind kill-server`, or its agent scopes. It calls this host break-glass and explains why ordering matters.
- admind/unit.py:16–25: Restart=always, KillMode=control-group; admin tmux lives in a separate transient scope, so daemon stop/restart leaves it running.
- admind/agent.py:31–48: private socket heterodyne-admind, session admin; Linux scope name heterodyne-admind-tmux-<12hex>. agent.py:107–146 handles adopt/resume/new launch.
- deployed admind/daemon.py:2195–2226 start_agent(startup=True): launch/adopt via ensure_running. :2238–2247 adopts a live pane with a fail-closed dispatch hold. :2253–2286 watchdog relaunches a dead agent and abandons in-flight work when admind remains active.
- agents/claude_code.py:44–62 emits literal `--resume <stored-native-ID>` when resume=True, otherwise `--session-id <ID>`.
- tmux.py:213–217 exact session kill is `kill-session -t =admin`; documented server kill removes all sessions on the private server. No default or unrelated socket is involved.

## Resume and identity boundary

A daemon-only restart adopts the existing live pane if there is a recorded agent_session, current launch_nonce, no replace_pending, and matching login; it holds dispatch until a current Stop or successful interrupt/new. It does not by itself quiesce the agent.

After stopping the daemon and killing only the verified private admin server/session, `systemctl --user start heterodyne-admind` calls startup again. It resumes the same stored Claude native session ID only when session_started equals agent_session, the stored agent account matches the configured account, and no pending new replacement overrides it. The launch count must remain below3. Each launch gets a fresh nonce; a new tmux server/session/pane/process/scope is created. Thus chat/session-ID retention is conditional; tmux/process identity retention is impossible for this stop route. Persistent DB/state/transcripts are not deleted by the tmux kill. Unfinished transcript flush is not proven; obtain a genuinely idle/completed current turn and preserve state before stopping.

Do not use !new, clear launch counters, alter replace_pending, edit native transcripts, or relabel a new chat as the former one. Unknown/mismatched stored identity or crash-loop state is HOLD. Stop alone does not mark in-flight effects complete. Existing startup/readback semantics govern pending work; no automatic resend is authorized by this discovery.

## Narrow guarded command sequence for an explicitly approved deployment

DRAFT ONLY, not executed and not authorized by this report. The published deployment design currently forbids tmux destruction; an explicit deployment deviation must permit stopping ONLY this verified own admind agent while preserving its recorded native session and state, and restarting it conditionally. This is an existing mechanism, not a new API. All consent still goes through Marmot before taking it offline.

1. Parent captures a fresh exact own-UID socket/server/session/pane/process/scope identity and the stored agent_session/session_started/account/replace_pending/launch count. Require only the intended `admin` session on `/tmp/tmux-1001/heterodyne-admind`; any unknown extra session, changed target, unproven idle/effects, denied writer discovery or inability to block writers means HOLD. Also retain the separate default-socket admin sessions as independently unresolved writers; this route does not stop them.
2. `timeout 100s systemctl --user stop heterodyne-admind` (fresh host unit TimeoutStopUSec was90s). Require successful stop and fresh `systemctl --user show heterodyne-admind -p ActiveState -p SubState -p MainPID` showing inactive/dead/0. A timeout/error, active/deactivating service or uncertain restart means HOLD; do not kill a live unsupervised agent first.
3. Recheck the previously captured exact socket owner, server PID and admin pane identity after daemon stop. Only with exact match and no other sessions, execute `timeout 15s tmux -S /tmp/tmux-1001/heterodyne-admind kill-server`. This is the documented private-server command pinned to the discovered absolute socket, avoiding ambient TMUX/TMUX_TMPDIR. Do not use kill-server without a selector, wildcard scope stops, default-socket kills or arbitrary PID killing. If additional private sessions exist, HOLD rather than silently expanding scope. Session-only kill via exact `-t =admin` exists in the wrapper but is not the documented whole-agent-server stop route.
4. Require a fresh exact absence observation with a known tmux no-server/no-such-session response and stop-state unchanged. A connection error is not generic proof of absence. Preserve the socket metadata/state snapshot; do not delete state DB or native session files. Stop does not disable future unit starts; known external restart controllers must remain blocked throughout the approved window.
5. Resume only at the deployment's approved release/rollback phase with `systemctl --user start heterodyne-admind`. Observe actual audit action `resumed` with the original session UUID and a fresh nonce/session-start acknowledgement; otherwise HOLD for real recovery, never assert identity retention. No !new fallback. The newly created tmux IDs are expected to differ.

The root may replace step3 with an exact already-verified own transient scope stop if its membership is positively bound to this server, but this report does not authorize the documentation's broad wildcard scope command.

No live systemctl/tmux/agent/queue/DB/model/network commands were run. Only bounded source reads, Semble discovery and private evidence writes occurred. No incorporated design files were changed.
