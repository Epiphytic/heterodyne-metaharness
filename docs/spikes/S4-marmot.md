# Spike S4: Marmot threads, reactions and sender identity

Status: All 7 steps complete and PASS. Step 6 (real-client check) is done —
see "Step 6: result" below. `$S4` scratch state has been removed as part of
spike cleanup.

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
- **A membership change never appears as `group_state_changed` on the
  subscription of the identity that authored it.** The event is only
  delivered to *other* members' subscriptions, observing the change as
  relay-propagated. (Consistent with reactions/messages carrying an explicit
  `is_self`/`sender` distinction elsewhere in the protocol.)

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

### `send_reaction` / `remove_reaction`

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
`remove_reaction` takes the same shape (optional `emoji` — omitted retracts
all of this account's active reactions on the target) and returns the same
response type.

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

### `reaction_added` / `reaction_removed`

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

`reaction_removed` additionally carries `reaction_event_id_hex` (the id of the
retracted reaction event) alongside `event_id_hex` (the id of the retracting
delete). `target_message_id_hex` equals the card's `message_id_hex`, and
`emoji` matches the reacted emoji. **Confirmed both directions** in Step 3.

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

| Step | Result | Notes |
|---|---|---|
| 1. Second (`admind`) identity in a scratch home | PASS | Required `--secret-store file` (no OS keyring in this environment) and a private `wnd` with explicit `--discovery-relays`/`--default-account-relays`. |
| 2. Test group between harness and `admind` | PASS | Created via `group_create` on the harness's `wn-agent` control socket (members = founding list); accepted via `wn groups accept <group>` on the `admind` side. |
| 3. Threads and reactions, both directions | PASS | `reply_to.message_id_hex` and `reaction_added.target_message_id_hex` both matched the card id, both directions, observed on each side's subscription. |
| 4. Idempotent send | PASS | Two `send_final` calls, same `idempotency_key` → one message. |
| 5. Authenticated sender + membership changes | PASS | Sender pubkey confirmed to come from `message.sender`/`actor` metadata, never text. `member_added` and `member_removed` both observed via `group_state_changed` on a non-actor subscription; a non-admin's own add attempt failed closed with `not_group_admin`. |
| 6. Real-client check | PASS | Operator joined from their phone client, replied to the seeded card and reacted to it; the harness subscription log showed both, and the operator confirmed their client rendered the reply as a threaded reply. See "Step 6: result" below. |

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
cards posted before they joined. Either (a) (re)post any still-open card
again after a membership-change event adds a new member, or (b) treat a
`group_state_changed` `member_added` event as a trigger to re-render all
currently-open cards to the group. (This is a plan-6/admind implementation
note, not an ADR change — see `GATE.md`.)

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

Response: an `ack` envelope, followed by a `group_state_changed`
(`"change":"member_removed"`) on the leaving identity's own subscription.
Fails with `admin_cannot_self_remove` when the caller is the group's sole
admin.
