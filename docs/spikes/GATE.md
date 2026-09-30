# Plan 1 spike gate

| Spike | Result | ADR impact | Plan affected |
|---|---|---|---|
| S1 Codex parity | Q1: pre-tool deny works in the interactive TUI and never fires under `codex exec`. Q2: hook-trust bypass could not be reproduced independently. Q1 and Q2 are therefore **unsettled**. Q3: no launch-time ID; the assigned thread ID is recorded, and a post-launch rename is addressable. Q4: `codex queue` works only through a dedicated `app-server` socket. | Amendment: §4.1–§4.3, §5.3, §10, §7 | Plan 4 (the Codex adapter; hooks tested with a real `hooks.json`; app-server inside the sandbox) |
| S2 Claude channels | UNAVAILABLE. The contract is documented; the org gate is likely but unconfirmed. | §5.6 wording only (v1 steers with `tmux send-keys`) | Phase 2 |
| S3 bubblewrap sandbox | The fail-closed self-test passes: 8 probes plus 3 outside-sandbox checks, and the negative controls fail as expected. Both CLIs log in with read-only auth files. Credential files are not written mid-session. | Amendment: §7 (clear the environment; block connectors; freshness gate; writable synthetic home; exact-host allowlist; probes must prove specific enforcement) | Plan 4 (the bwrap backend) |
| S4 Marmot | Steps 1–6 PASS, step 7 (doc & cleanup) PASS: threads, reaction adds (removal UNTESTED — see `S4-marmot.md`), idempotent `send_final`, the authenticated sender field, membership add/remove events (self-`group_leave`'s effect on the leaver's own subscription UNVERIFIED), and (step 6) the real-client check — the operator's phone client rendered a threaded reply and a reaction against a real card, matching the harness's subscription log. Evidence is a summary of the logs checked during the run; the raw logs were deleted afterward per cleanup instructions. | Amendment: §3.4 (an actor performing `group_member_add`/`group_member_remove` on other members produces no event on its own subscription; a self-`group_leave`'s effect on the leaver's own subscription is unverified) | Plans 2 and 6 |

## Decision

The spikes contradict revision 11 (`e36f6d0`, approved in `btq-96hm`), so an amendment was required.
- ADR 0001 **revision 12** is at design-repo commit `d6e997c271eaba25b587012b1447d9c58a582ce3`.
- It was reviewed cross-model in rounds r12–r17; r17 approved it with no findings.
- The §5.9 approval bead is **`btq-freh`**, which pins the ADR, the range, the review record and these spike documents.

Plan-1 code tasks (B1, B2, 7–11) cite `btq-freh` and revision `d6e997c`. They are blocked until the operator approves it.

The S4 step-6 check passed: threads and reactions render correctly in a real client, so no further §6 revision is needed.

**Plan-2/plan-6 implementation notes and acceptance items (not an ADR change):** step 6 also found that messages sent before a member joins a group are not visible to that member in the client. Plan 6 (router, outbox) and plan 2 (`admind`) should (re)post still-open cards after a membership-change adds a member. This does not contradict any explicit statement in ADR §6 (which is silent on join-time visibility of prior messages), so it is recorded here as an implementation note rather than as an amendment.

The re-post trigger cannot be a `member_added` `group_state_changed` event on the harness's own subscription: Step 5 showed that an actor performing `group_member_add`/`group_member_remove` never sees the result of its own action as an event on its own subscription — only other members do. Plan 2/6 add members through the harness's own control socket, so **the harness's own successful `group_member_add` response on the request path arms the re-post**, not the inbound event stream. That response only confirms the invite. The re-post fires on a join or visibility confirmation. No such signal was established in this spike. The new member's first inbound event in the group is an unverified candidate. The recorded `group_info` shape (`member_count` only) is not evidence of acceptance. Establishing and verifying the signal is a plan 2/6 acceptance item. In step 6 the operator accepted after the add succeeded. For adds by some other admin identity, a `member_added` event does reach the harness's subscription. Whether it arrives only after the new member has accepted is unverified, so it too is only a candidate signal pending plan 2/6 validation.

Two further items are explicit plan 2/6 acceptance criteria before this contract can be relied on in production: (1) **verify the reaction removal contract** — `remove_reaction`/`reaction_removed` are UNTESTED in this spike; and (2) **verify `group_leave`'s self-event behavior** — whether a successful self-leave produces a `group_state_changed` event on the leaver's own subscription was not established here (`admind`'s own subscription was not checked for it at leave time).
