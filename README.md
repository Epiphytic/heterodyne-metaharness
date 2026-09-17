# Hermes workstream harness

A durable supervisor for an interactive coding agent plus a separate Hermes manager, with Marmot project channels. Python standard library, tmux and systemd provide persistence and process ownership. Codex, Claude Code and Hermes differences live behind agent adapters.

## Commands

```sh
workstream start project /absolute/repository codex --file task.txt
workstream start project /absolute/repository claude --file task.txt --group EXISTING_GROUP
workstream list
workstream status project
workstream send project --file steering.txt                    # to manager
workstream send project --target worker --file steering.txt    # direct coder steering
workstream event project --state completed --text 'Verified result and evidence'
workstream resume project                                     # restore conversations, no task replay
workstream stop project                                       # preserve worktrees, groups and history
workstream doctor
```

Start is idempotent by name; repeats never replace a manifest or resubmit input. A conflicting repository/agent is rejected. Reuse the project group; only one nonterminal run owns a repository/group. Use a new name after stopping an old run if starting genuinely new work. An uncertain creation result requires `workstream bind-group NAME GROUP_ID` rather than creating duplicates. A failed startup is visible; after correcting it, recover the conversation with `resume`, then explicitly steer the task.

The supplied `workstream-start NAME REPO AGENT [PROMPT] [GROUP]`, `workstream-stop`, `workstream-reconcile` and `twrap` entry points delegate to this implementation. No independent watchdog or progress-relay process is needed. `twrap` preserves command argv and working directory; arbitrary commands are never replayed after reboot.

## Recovery and compaction

State lives in `$HERMES_HOME/workstreams/harness.sqlite3`, independent of all model contexts. Each run has an inspectable `workstreams/runs/RUN_ID/checkpoint.json`, separate manager/worker identities, exact native session IDs when discoverable, last observations, input submission states and a durable message outbox.

A changed Linux boot ID queues an interruption report with the last observation, then restores native conversations without a prompt. Unknown native identity opens a blank worker and says so. Arbitrary command jobs remain interrupted. A recovered manager/worker waits for explicit steering; previous tests, builds and tools are not automatically replayed. A same-boot crash during a non-idempotent submit or launch is reported as uncertain, never retried blindly. `resume` restores conversation state and does not itself authorize task execution.

Compaction keeps the native identity and durable supervisor state; native Codex/Claude lifecycle events retain explicit turn-completion and compacted markers. A turn ending never means the whole task completed. The manager verifies task outcome and records it through `event`. Manager responses also relay from its persisted transcript when available.

## Reporting and routing

Every ongoing run, including unknown, blocked and failed states, queues a status at most every 240 seconds plus a 10-second observation tick. Delivery runs separately, validates acknowledged Marmot responses, retries using the same remote idempotency key, and escalates after three failures. Network outages cannot guarantee delivery; `doctor` exposes backlog and oldest pending report, and messages survive outages/restarts. Transports and tmux input do not share an exactly-once transaction: uncertain input requires inspection.

The installed Marmot hook sends authenticated messages for an owned channel to its durable manager inbox, before ordinary gateway dispatch. Unowned channels use the normal gateway. New groups require no gateway restart once the hook is installed. Duplicate inbound IDs cannot create duplicate manager inputs. Inputs retain their explicit target across restart.

## Deployment

Run `python install.py` with Hermes's venv Python to load current Marmot member configuration, then `python install_routing.py ~/.hermes/plugins/marmot/adapter.py`. First routing installation needs a gateway restart. The installer backs up replaced files and is idempotent. It creates `~/.config/systemd/user/hermes-workstreams.service`; enable user lingering for unattended boot (`loginctl show-user "$USER" -p Linger`). A dedicated `tmux -L hermes-workstreams` server retains interactive panes and exit status.

Configuration: `workstreams/harness-config.json` supplies `marmot` (bootstrap, socket, members, relays, timeout), `ops_group`, `tmux_socket`, `manager` executable/extra_args and CLI path. Per-worker `--agent-config` supplies explicit executable/extra_args. No permission bypass or approval-key automation is added. Worktrees and channels are preserved on stop; cleanup is a separately reviewed operation.

Legacy manifests can be imported with `workstream import-legacy`; they remain archived for inspection rather than automatically resurrecting historical tasks. Resume selected records explicitly after checking them.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary state, fake coding executables, isolated real tmux sockets and local mock Marmot servers. They never reboot the host, call real models, create real channels or send real messages.
