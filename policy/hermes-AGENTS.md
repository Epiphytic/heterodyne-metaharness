# Hermes scoped operating policy

For this Hermes tree, the current specification at
`/home/operator/repos/hermes-workstream-harness/spec/README.md` supersedes inherited
legacy watchdog, manual relay, polling and channel-wake instructions for managed
sessions. Preserve inherited unrelated engineering standards.

All significant Hermes implementation uses existing `hermes-maintenance` Codex/tmux.
Use `workstream maintenance --title TITLE --file TASK --key REQUEST_ID`.
Do not create another writer; preserve approvals. The maintenance worker implements
its assigned task directly without recursive self-delegation. Simple configuration
and personal memory edits are allowed locally. Beads owns task tracking.
