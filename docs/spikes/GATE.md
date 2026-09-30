# Plan 1 spike gate

| Spike | Result | ADR impact | Plan affected |
|---|---|---|---|
| S1 Codex parity | Q1: pre-tool deny works in the interactive TUI and never fires under `codex exec`. Q2: hook-trust bypass could not be reproduced independently. Q1 and Q2 are therefore **unsettled**. Q3: no launch-time ID; the assigned thread ID is recorded, and a post-launch rename is addressable. Q4: `codex queue` works only through a dedicated `app-server` socket. | Amendment: §4.1–§4.3, §5.3, §10, §7 | Plan 4 (the Codex adapter; hooks tested with a real `hooks.json`; app-server inside the sandbox) |
| S2 Claude channels | UNAVAILABLE. The contract is documented; the org gate is likely but unconfirmed. | §5.6 wording only (v1 steers with `tmux send-keys`) | Phase 2 |
| S3 bubblewrap sandbox | The fail-closed self-test passes: 8 probes plus 3 outside-sandbox checks, and the negative controls fail as expected. Both CLIs log in with read-only auth files. Credential files are not written mid-session. | Amendment: §7 (clear the environment; block connectors; freshness gate; writable synthetic home; exact-host allowlist; probes must prove specific enforcement) | Plan 4 (the bwrap backend) |
| S4 Marmot | All 7 steps PASS: threads, reactions, idempotent `send_final`, the authenticated sender field, membership events, and (step 6) the real-client check — the operator's phone client rendered a threaded reply and a reaction against a real card, matching the harness's subscription log. | Amendment: §3.4 (membership changes by the harness's own identity produce no event) | Plans 2 and 6 |

## Decision

The spikes contradict revision 11 (`e36f6d0`, approved in `btq-96hm`), so an amendment was required.
- ADR 0001 **revision 12** is at design-repo commit `d6e997c271eaba25b587012b1447d9c58a582ce3`.
- It was reviewed cross-model in rounds r12–r17; r17 approved it with no findings.
- The §5.9 approval bead is **`btq-freh`**, which pins the ADR, the range, the review record and these spike documents.

Plan-1 code tasks (B1, B2, 7–11) cite `btq-freh` and revision `d6e997c`. They are blocked until the operator approves it.

The S4 step-6 check passed: threads and reactions render correctly in a real client, so no further §6 revision is needed.

**Plan-6/admind note (not an ADR change):** step 6 also found that messages sent before a member joins a group are not visible to that member in the client. Plan 6 (router, outbox) and plan 2 (`admind`) should (re)post still-open cards after a membership-change event adds a member, or treat a `member_added` `group_state_changed` event as a trigger to re-render currently-open cards. This does not contradict any explicit statement in ADR §6 (which is silent on join-time visibility of prior messages), so it is recorded here as an implementation note rather than as an amendment.
