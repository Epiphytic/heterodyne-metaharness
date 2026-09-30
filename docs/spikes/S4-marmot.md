# Spike S4: Marmot threads, reactions and sender identity

Status: Steps 1–6 (the protocol checks) and step 7 (documentation and
cleanup) are complete; see the per-step PASS/FAIL table below for the exact
result of each step, including two items marked UNTESTED. Step 6
(real-client check) is done — see "Step 6: result" below. `$S4` scratch
state has been removed as part of spike cleanup. The per-step notes below
are a summary of the equality checks and event/response shapes that were
inspected during the run; the underlying scrubbed request/response and
event log files were deleted as part of cleanup and are not part of the
committed record.

## Method notes: real CLI surface vs. the brief's guesses

The brief's flags were guesses in several places. What actually holds:

- **The harness's `wn` home cannot be opened by the `wn` CLI at all while the
  production `wn-agent` process is running against it.** Any `wn <cmd> --home
  <harness-home>` call (e.g. `wn groups list`, `wn messages subscribe`) either
  fails with `"marmot runtime root is already in use"` (direct-mode commands)
  or `daemon_unavailable` (commands that require a `wnd` daemon, since no
  `wnd` is running there — only `wn-agent` is). This is a hard architectural
  constraint, not a flag issue: MLS ratchet state cannot safely be shared
  across two processes. **Every harness-side operation in this spike went
  through the running `wn-agent` process's own control socket**
  (`marmot.agent-control.v2`, NDJSON-over-Unix-socket), using the exact
  request/response shapes the Hermes Marmot adapter's
  `MarmotAgentControlClient` already speaks (`~/.hermes/plugins/marmot/adapter.py`).
- **There is no "list groups" operation in the deployed `wn-agent` control
  protocol** (checked the full `AgentControlRequest`/`AgentControlResponse`
  enums for the exact source version the deployed binary reports). To satisfy
  "list existing groups before creating anything" without opening the
  harness's locked home, this spike used the **existing, read-only
  `group_info` operation** against the group ids the harness's own channel
  directory already advertises, capturing `subject` (name), `member_count`
  and `is_direct` before and after the spike. All pre-existing groups were
  unchanged afterward (same `subject`/`member_count` for every one of them).
- **The scratch identities (`admind`, `third`) are plain local `wn` CLI
  accounts with their own private `wnd` daemon** (`wn daemon start --home
  <scratch-home> --secret-store file --discovery-relays <relay-urls>
  --default-account-relays <relay-urls>`); a system keyring was not available
  in this environment, so `--secret-store file` was required for
  `create-identity` to succeed. Ordinary `wn` CLI subcommands (`create-identity`,
  `groups accept`, `messages send`, `messages react`, `messages subscribe`)
  work normally against these, since nothing else holds their home open.
- **`wn messages subscribe` (the `wnd`-backed CLI stream) never surfaces MLS
  group-state/membership events** — only `message`/`reaction`/`message_delete`
  triggers. To observe `group_state_changed` (membership changes) from a
  non-actor's point of view, one of the scratch identities was switched from
  its private `wnd` to its own **private, throwaway `wn-agent` process**
  (own home, own socket, invoked directly — not the systemd-managed
  `wn-agent` service, and not the harness's), then used
  `subscribe_inbound` on that identity's own control socket.
- **The deployed `wn-agent` control protocol supports post-creation
  membership management**: `group_member_add`, `group_member_remove`,
  `group_admin_add`, `group_admin_remove`, `group_welcome_status`, confirmed
  empirically against the live process (the brief's source-adjacent reference
  notes, dated earlier, said this was "not exposed via the connector" — that
  has since shipped in the deployed build). `invite_members` at group-creation
  time is still founding-members-only (`group_create`'s `members` list); there
  is no separate pre-creation invite op.
- A running identity can only author `group_member_add`/`group_member_remove`
  if it is a **group admin**; a non-admin member's attempt fails closed with
  `not_group_admin` (confirmed empirically).
- **An actor performing `group_member_add`/`group_member_remove` on other
  members never sees the resulting `group_state_changed` on its own
  subscription** — the event is only delivered to *other* members'
  subscriptions, observing the change as relay-propagated (confirmed in
  Step 5). (Consistent with reactions/messages carrying an explicit
  `is_self`/`sender` distinction elsewhere in the protocol.) **This spike
  does not settle what a self-`group_leave` produces on the leaving
  identity's own subscription** — see the `group_leave` note further down
  and the Step 6 finding; treat that as UNVERIFIED until plan 2/6 confirms
  it empirically.

## Request shapes (harness → `wn-agent` control socket)

All requests are one NDJSON line on the Unix control socket, wrapped in an
envelope: `{"marmot_agent_control":"marmot.agent-control.v2","id":"<request-id>", ...fields}`.
The response mirrors the same `id` and `marmot_agent_control` fields.

### `send_final` (durable message; supports reply + idempotency)

```json
{
  "type": "send_final",
  "account_id_hex": "<account_id_hex>",
  "group_id_hex": "<group_id_hex>",
  "text": "string",
  "reply_to_message_id_hex": null,
  "idempotency_key": "s4-1"
}
```

Response: `{"type":"final_sent","message_ids_hex":["<id>"],"maintenance_disposition":"ready"}`.
`idempotency_key` is optional; when present, a retry with the same key returns
the **same** `message_ids_hex` instead of posting again (confirmed, see Step 4).

### `send_reaction` (tested) / `remove_reaction` (UNTESTED)

```json
{
  "type": "send_reaction",
  "account_id_hex": "<account_id_hex>",
  "group_id_hex": "<group_id_hex>",
  "target_message_id_hex": "<id>",
  "emoji": "👍"
}
```

Response: `{"type":"app_event_sent","message_ids_hex":["<id>"],"maintenance_disposition":"ready"}`.
Confirmed in Step 3 (both directions).

`remove_reaction` is **UNTESTED** — it was never called in this spike; no
request/response for it was captured. The shape sometimes assumed for it
(same fields as `send_reaction`, with `emoji` optional and its omission
meant to retract all of this account's active reactions on the target) and
its claimed response type (`app_event_sent`) are **unconfirmed
carry-overs from the brief**, not something this spike verified. Plan 6
must verify the reaction removal contract empirically before relying on
it.

### `group_create` (founding members only; no post-creation invite in this op)

```json
{
  "type": "group_create",
  "account_id_hex": "<account_id_hex>",
  "name": "hz-s4-test",
  "members": ["<account_id_hex_or_npub>"],
  "description": null,
  "relays": null
}
```

Response: `{"type":"group_created","group_id_hex":"<id>","agent_created":true,"pending_welcome_count":0}`.
Non-idempotent: calling it again creates a distinct group. The creator must
NOT be listed in `members` (implicit).

### `group_member_add` / `group_member_remove` (post-creation membership)

```json
{
  "type": "group_member_add",
  "account_id_hex": "<account_id_hex>",
  "group_id_hex": "<group_id_hex>",
  "members": ["<account_id_hex_or_npub>"],
  "initial_admins": []
}
```

Response: `{"type":"group_membership_updated","group_id_hex":"<id>","pending_welcome_count":0}`.
`group_member_remove` takes `{account_id_hex, group_id_hex, members}` (no
`initial_admins`) and returns the same response type. Both **require the
caller to already be a group admin**; a non-admin caller gets:

```json
{"type":"error","code":"not_group_admin","message":"local identity is not an admin of the group","retryable":false}
```

### `group_info` (read-only; used here in place of a "list groups" op)

```json
{"type":"group_info","account_id_hex":"<account_id_hex>","group_id_hex":"<group_id_hex>"}
```

Response: `{"type":"group_info","account_id_hex":"<account_id_hex>","group_id_hex":"<id>","agent_created":true,"member_count":2,"is_direct":true,"subject":"hz-s4-test"}`.
`subject` is omitted for groups with no name set (e.g. plain DMs).

## Event shapes (inbound, via `subscribe_inbound` on the control socket)

Subscribing: send `{"type":"subscribe_inbound","account_id_hex":"<account_id_hex>|null","group_id_hex":"<group_id_hex>|null"}`;
the socket first replies with an `ack` envelope, then streams one event
envelope per line indefinitely (same `id` as the subscribe request).

### `inbound_message` (carries the thread/reply link)

```json
{
  "type": "inbound_message",
  "account_id_hex": "<account_id_hex>",
  "group_id_hex": "<group_id_hex>",
  "message": {
    "message_id_hex": "<id>",
    "sender": {"account_id_hex": "<account_id_hex>", "display_name": "string|null", "is_self": false},
    "text": "string",
    "recorded_at": 1790738645
  },
  "mentions_self": false,
  "reply_to": {
    "message_id_hex": "<id>",
    "availability": "available",
    "sender": {"account_id_hex": "<account_id_hex>", "is_self": true},
    "recorded_at": 1790738629,
    "text_excerpt": "string",
    "text_truncated": false,
    "attachments_truncated": false
  }
}
```

`reply_to.message_id_hex` equals the target card's `message_id_hex`. `reply_to`
is present only when the inbound message was sent as a reply; when absent
there is still a legacy-shaped `reply_to_message_id_hex` field the adapter
also normalizes from it. **Confirmed both directions** (harness↔scratch
identity) in Step 3.

### `reaction_added` (tested)

```json
{
  "type": "reaction_added",
  "account_id_hex": "<account_id_hex>",
  "group_id_hex": "<group_id_hex>",
  "event_id_hex": "<id>",
  "target_message_id_hex": "<id>",
  "actor": {"account_id_hex": "<account_id_hex>", "display_name": "string|null", "is_self": false},
  "emoji": "👍",
  "recorded_at": 1790738646,
  "target": { "...same shape as reply_to above..." }
}
```

`target_message_id_hex` equals the card's `message_id_hex`, and `emoji`
matches the reacted emoji. **Confirmed both directions** in Step 3.

### `reaction_removed` — UNTESTED

No `remove_reaction` request was made in this spike, so no `reaction_removed`
event was ever observed. The field list sometimes cited for it (an
additional `reaction_event_id_hex` — the id of the retracted reaction event
— alongside `event_id_hex`, the id of the retracting delete) is a
carry-over assumption from the brief, not something captured here. Do not
treat this shape as confirmed; see the `remove_reaction` note above.

### `group_state_changed` (membership / admin / rename / avatar / retention)

```json
{
  "type": "group_state_changed",
  "account_id_hex": "<account_id_hex>",
  "group_id_hex": "<group_id_hex>",
  "event_id_hex": "<id>",
  "change": "member_added"
}
```

`change` ∈ `member_added`, `member_removed`, `member_left`, `admin_added`,
`admin_removed`, `group_renamed` (adds a `detail` string: the new name),
`group_avatar_changed`, `disappearing_timer_changed`. **The subject member's
pubkey is never included** — by design, this event only carries the coarse
change kind (plus, for a rename, the new display name). Confirmed both
`member_added` and `member_removed` in Step 5, observed only from a *different*
member's subscription (the authoring identity's own subscription never sees
its own membership-change action).

### `message_edited` / `message_deleted` (not directly asked for, seen along the way)

Both carry `event_id_hex`, `target_message_id_hex`, `actor` (same shape as
above) and a `target` (bounded snapshot of the original message), plus
`replacement_text` for edits.

## The authenticated sender field (§3.4)

The MLS-authenticated sender is always carried in event **metadata**, never
parsed from `text`:

- Inbound messages: `message.sender.account_id_hex` (plus `sender.is_self`,
  `sender.display_name`).
- Reactions / edits / deletes: `actor.account_id_hex` (plus `actor.is_self`,
  `actor.display_name`).

Both are populated straight from the MLS/Nostr event's authenticated author,
independent of anything the message body contains. Confirmed by inspecting
live `inbound_message`/`reaction_added` events during Step 3/5 — the sender
field for a scratch identity's card and reaction consistently resolved to
that identity's own `account_id_hex`, never to the harness's, and was present
even though the message text carried no identity information at all.

## Idempotent send (Step 4)

Two `send_final` requests with the same `idempotency_key` and identical
`account_id_hex`/`group_id_hex`/`text` both returned the **same**
`message_ids_hex`, and the receiving side's timeline showed the text exactly
once. **Pass.**

## Pass/fail per step

The rows below are a summary of the equality checks and operator
confirmation performed at run time. The scrubbed request/response and
event log pairs behind them were reviewed live but were not retained in
the committed record — they were deleted along with the rest of `$S4`
during cleanup, per the spike's cleanup instructions. This table, and the
per-shape notes above, are the evidence of record.

| Step | Result | Notes |
|---|---|---|
| 1. Second (`admind`) identity in a scratch home | PASS | Required `--secret-store file` (no OS keyring in this environment) and a private `wnd` with explicit `--discovery-relays`/`--default-account-relays`. |
| 2. Test group between harness and `admind` | PASS | Created via `group_create` on the harness's `wn-agent` control socket (members = founding list); accepted via `wn groups accept <group>` on the `admind` side. |
| 3. Threads and reactions, both directions | PASS | `reply_to.message_id_hex` and `reaction_added.target_message_id_hex` both matched the card id, both directions, observed on each side's subscription. Reaction **removal** (`remove_reaction`/`reaction_removed`) was not exercised in this step — see the UNTESTED notes above. |
| 4. Idempotent send | PASS | Two `send_final` calls, same `idempotency_key` → one message. |
| 5. Authenticated sender + membership changes | PASS | Sender pubkey confirmed to come from `message.sender`/`actor` metadata, never text. `member_added` and `member_removed` both observed via `group_state_changed` on a non-actor subscription; a non-admin's own add attempt failed closed with `not_group_admin`. |
| 6. Real-client check | PASS | Operator joined from their phone client, replied to the seeded card and reacted to it; the harness subscription log showed both, and the operator confirmed their client rendered the reply as a threaded reply. See "Step 6: result" below. |
| 7. Documentation and cleanup | PASS | `S4-marmot.md` and `GATE.md` written; scratch identities and background subscription processes stopped; `admind` left `hz-s4-test` cleanly via `group_leave`; the harness (sole admin) could not leave (`admin_cannot_self_remove`) and was left in the group as instructed, recorded rather than worked around; `$S4` removed with `rm -rf`. This row, like the others, is a summary of what was checked at commit time — the underlying logs no longer exist to re-check independently. |

## Step 6: result

The operator was invited into `hz-s4-test` via one `group_member_add` request
against the harness's `wn-agent` control socket, as planned, and accepted the
invite in their phone's Marmot/White Noise client.

**A wrinkle:** the original seeded test card was sent *before* the operator's
join completed, and the Marmot client does not surface messages sent prior to
a member joining — so the operator never saw it. The controller resent an
equivalent card via `send_final` with a fresh `idempotency_key`
(`s4-step6-resend-1`), after the join, and the operator replied to and
reacted to that resent card instead.

**Evidence (from the harness's `subscribe_inbound` log):**
- An `inbound_message` event ("Hey!") whose top-level `reply_to.message_id_hex`
  equals the resent card's `message_id_hex`.
- A `reaction_added` event (👍) whose `target_message_id_hex` equals the
  resent card's `message_id_hex`.
- A second reply/reaction pair was observed shortly after from further
  operator interaction — consistent, not required for pass.

This also confirms the shape documented above: **`reply_to` is a top-level
field of the `inbound_message` envelope** (a sibling of `message`, carrying
its own `message_id_hex`/`availability`/`sender`), never a field nested
inside `message`.

The operator confirmed their phone client rendered the reply as a threaded
reply (not a plain new message). **Pass.**

**Finding:** messages sent before a member joins a group are not visible to
that member in the client, even though the message already exists in the
group's history from the relay/`wn-agent` point of view. Plan 6 (router,
outbox) and plan 2 (`admind`) must not assume a newly-joined member can see
cards posted before they joined; still-open cards must be (re)posted after a
join. (This is a plan-2/plan-6 implementation note, not an ADR change — see
`GATE.md`.)

**Plan 2 / Plan 6 implementation notes and acceptance items (from this
spike's findings):**

1. **For the harness's own adds, re-posting after a join is armed on the
   request path and fired on a confirmed join. For the harness's own adds, no `member_added` event arrives
   to fire it.**
   Step 5 confirmed that an actor performing
   `group_member_add`/`group_member_remove` never sees the resulting
   `group_state_changed` on its own subscription; that event reaches only
   *other* members' subscriptions. Plans 2/6 add members through the
   harness's own `wn-agent` control socket, so the harness never receives
   a `member_added` event for adds it makes itself.
   - The harness's own successful `group_member_add` response
     (`group_membership_updated`) **arms** a pending re-post of the open
     cards. It only means the invite was sent, not that the new member has
     accepted or can see messages. In step 6 the operator accepted *after*
     the add succeeded, and saw only the card that was re-posted after
     joining.
   - The re-post **fires** on a join or visibility confirmation. No such
     signal was established in this spike. The one candidate with any
     evidence is the first inbound event from the new member in that group.
     Step 6's reply arrived only after the operator had joined, but that
     doesn't prove it's a reliable join signal. The recorded `group_info`
     response carries `member_count` but no member or welcome state, so it
     is **not** evidence of acceptance. Establishing and verifying the
     fire signal is a plan 2/6 acceptance item.
   - For adds made by some *other* admin identity, there is no harness
     request path. Step 5 observed `member_added` reaching a non-acting
     member (the harness acted, another member received it). It's an
     inference, not an observation, that the harness would likewise receive
     it when another admin acts. And nothing here
     shows that the event arrives only after the new member has accepted or
     can see messages. It is therefore an **unverified candidate** signal,
     pending plan 2/6 validation, not an established trigger.
2. **Verify the reaction removal contract.** `remove_reaction` and inbound
   `reaction_removed` are UNTESTED in this spike (see the request/event
   shape notes above). This is an explicit plan 6 acceptance item: verify
   the reaction removal contract (request shape, response shape, and the
   resulting event) empirically before relying on it.
3. **Verify `group_leave`'s self-event behavior.** Whether a successful
   self-leave produces a `group_state_changed` event on the *leaving*
   identity's own subscription is UNVERIFIED in this spike (see the
   `group_leave` note above) — confirm this empirically before plan 2/6
   relies on any leave-triggered event for the leaver itself.

**Cleanup performed:** the scratch `admind` identity left `hz-s4-test`
cleanly via a `group_leave` request on its own control socket (previously
undocumented above; discovered during cleanup — see the request shapes note
below). The harness identity could not leave: `group_leave` on its own
socket returned `admin_cannot_self_remove` (it is the group's sole admin),
and `group_member_remove` targeting its own `account_id_hex` also failed.
Per the spike's cleanup instructions, the harness was left in the group
(now down to two members: harness + operator) and this is recorded here
rather than silently worked around. All background spike processes were
stopped and the scratch data home was removed.

### `group_leave` (self-removal; not in the brief, found during cleanup)

```json
{"type": "group_leave", "account_id_hex": "<account_id_hex>", "group_id_hex": "<group_id_hex>"}
```

`admind`'s `group_leave` call against `hz-s4-test` succeeded (no error
returned). The harness's own `group_leave` attempt failed closed with
`admin_cannot_self_remove` (it is the group's sole admin) — confirmed.

**What this spike does not confirm:** whether a successful self-leave
produces a `group_state_changed` (`"change":"member_removed"`) event on the
*leaving* identity's own subscription — `admind`'s own subscription log was
not inspected for a resulting event at leave time. This is a different
question from the confirmed rule for `group_member_add`/`group_member_remove`
(Step 5), where the *acting* identity never sees its own action as an event
on its own subscription when acting on *other* members; `group_leave` is
self-targeted and may not behave the same way. Treat the leaver's-own-
subscription behavior for `group_leave` as UNVERIFIED; plan 2/6 must confirm
it empirically before relying on any leave-triggered event for the leaver
itself.
