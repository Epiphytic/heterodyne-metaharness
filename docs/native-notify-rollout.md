# Native completion notifier rollout

Contract: [status](../spec/status.md). This is a second partial slice of Bead 259f;
permissions relay and deployment lifecycle work remain open.

The official [Codex advanced config documentation](https://developers.openai.com/codex/config-advanced/)
describes the notify argv plus JSON payload, including thread-id, turn-id, cwd and
last-assistant-message. The [configuration reference](https://developers.openai.com/codex/config-reference/)
places notify in user config; project config cannot override it. No package added.

After operator review and integration, from the canonical repository:

```sh
python3 install_notify.py --config /home/operator/.codex/config.toml --output /tmp/hermes-notify-plan.json
python3 install_brain.py --apply /tmp/hermes-notify-plan.json --quarantine /home/operator/.local/state/hermes-quarantine/codex-notify
```

Keep the plan private: it contains the complete configuration. Review it locally,
never post it to chat or commit it. Apply uses the existing verified backup,
source-drift and rollback manifest tooling. Repeat apply must change zero files;
regenerating a plan should show no changes. Only notify changes: prior command
arguments are chained exactly, no shell evaluation, bounded to fifteen seconds.
Unfamiliar TOML layouts are rejected unchanged. No hook trust or approval changes.

Back up the harness DB/checkpoint coherently using the existing quiet-window
procedure. Restart the supervisor to load stale-lifecycle rejection. At a safe
boundary exact-resume the existing Codex session to load notify; never restart a
task or accept a pending approval. Verify a natural completed turn records one
native_completion event with applied=true and matching native/turn, timestamp and
message digest. The checkpoint should show native_turn_state=idle and no stale
working task_state. Verify services and original identities, and record actual
deployment evidence before completion. No live receipt/reboot is asserted here.

A missing identity, unmatched active turn or busy shared lock cannot clear a turn.
The native lifecycle/pane paths remain fallback; unmatched receipt events explicitly
record applied=false. These are not task or brain-context acknowledgments. The
notifier is a trusted local process integration, not an authentication boundary
against another process with the same filesystem/environment access.
