- [BLOCKING] **§4.1, §4.3 and §10 — Codex session recovery.** The new §4.2 correctly says Codex assigns its thread ID, but the ADR still says sessions resume by the derived `uuid5` ID. Clarify that `uuid5` is the harness’s logical key; Codex resume and reconciliation must use the assigned thread ID recorded on the bead, or a confirmed post-launch name.

- [BLOCKING] **§5.6 — Claude steering.** It still specifies “Claude: channel,” while §12 and the S2 result say v1 uses `tmux send-keys` and the channel gate has not passed. Change the v1 steer path to `send-keys` and reserve channels for phase 2.

- [BLOCKING] **§7 and §13 — sandbox verification.** “Bubblewrap … passes” overstates S3: its Codex run used `codex exec`, while S1 says the managed TUI requires an app-server whose sandboxed execution remains unverified. State that S3 passed its tested bubblewrap shape and that plan 4 must verify the app-server launch shape before declaring the managed Codex path passed.

- [BLOCKING] **§3.4 and the response file’s S4 rationale.** S4 found that an identity’s own membership change produces no `group_state_changed` event on its subscription. The ADR’s unconditional alert and approval-suspension rule therefore has an uncovered case. Require locally initiated membership changes to trigger the same suspension and alert, or specify another detection mechanism; update the “no change needed” rationale.

- [NON-BLOCKING] **§7 and the response file — connector evidence.** S3 observed connector-related hosts and an MCP URL, but did not test connector access from the sandbox. Replace “tokens reach account connectors” with a qualified risk statement while retaining the proposed controls.

- [NON-BLOCKING] **§13 and the response file — S4 status.** “Both identities through the `wn-agent` control socket” blurs the scratch identity’s use of its own CLI/daemon, and S4 has no section titled “ADR impact.” Describe the tested paths accurately and remove the claim that every spike has that section.

The amended lines introduce no operator name, user, home path, npub value or IP address.

VERDICT: REJECT