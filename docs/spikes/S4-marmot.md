# Spike S4: Marmot threads, reactions and sender identity

Status: Steps 1-5 complete and PASS. Step 6 (real-client check) requires the
operator and has not been run yet — see "Step 6: what the operator must do"
below. `$S4` scratch state has been left in place (not deleted) so Step 6 can
still be executed; the harness-side subscription keeps appending to its log
file.

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
| 6. Real-client check | NOT RUN | Needs the operator; see below. `$S4` and its subscriptions were left running for this. |

## Step 6: what the operator needs to do

1. Join the group named **`hz-s4-test`** from their phone's Marmot/White Noise
   client. This requires the harness identity (the group's only admin) to
   invite the operator's device identity first — the operator cannot self-join.
   The controller (whoever can reach the harness's `wn-agent` control socket)
   should:
   - Ask the operator for their client's **npub** (visible in their app's own
     account/profile screen — usually under a settings or "your identity" view
     that shows a `npub1…` string or a QR code encoding it).
   - Run one `group_member_add` request against the harness's `wn-agent`
     socket (same shape documented above) with that npub in `members`.
   - The operator then sees + accepts the invite in their client's UI.
2. Once joined, ask the operator to **reply to** the harness's existing test
   card in `hz-s4-test` (there is already a card seeded for this — the
   controller has its id) and to **react to** any other message in the group.
3. **Pass** if the harness-side subscription log shows both a matching
   `inbound_message` (with `reply_to` pointing at the card) and a matching
   `reaction_added`, **and** the operator confirms their client visually
   rendered the reply as a threaded reply (not just a plain new message).

### Commands the controller can use to verify afterward

- Tail/inspect the harness-side subscription log (already running, appending
  live) for the operator's reply and reaction events (`inbound_message` with
  a `reply_to`, and `reaction_added`), matching the shapes documented above.
- Re-run `group_info` for `hz-s4-test` against the harness's `wn-agent`
  socket and confirm `member_count` increased by exactly one (the operator)
  compared to this spike's end state (2 members: harness + `admind`).
- Re-run `group_info` for each of the harness's other, pre-existing groups
  and confirm `member_count`/`subject` are unchanged from this spike's
  before/after snapshots (all six were confirmed unchanged as of Step 5).
- Leave `hz-s4-test` from the scratch (`admind`) identity, and — once Step 6
  evidence is captured — from the harness identity too, then remove the `$S4`
  scratch directory.
