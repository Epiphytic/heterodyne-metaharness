# Durable Marmot reactions

Authority: [entrypoint](README.md), [session ownership](sessions.md).

`workstream react RUN --message-id ID --emoji CONTENT --key KEY` queues an
explicit desired-active reaction on the run's exact Marmot group. It does not
send text, launch a model, create a group, or change task ownership. Callers must
have authorization to react and supply the actual durable source message ID;
never guess an ID from an inbox hash or select the latest message. This command
does not add automatic acknowledgement reactions to all inbound messages.

A nonempty caller-retained key is namespaced by stable run ID. Account, group,
target and exact reaction content are immutable under that key. Repeated enqueue
returns the same event, including after delivery or restart. Conflicting payloads
fail. Reaction payload and existing outbox row commit in one SQLite transaction.
Delivered rows remain the local deduplication record; do not prune them while a
caller may retry the key. Missing payload or changed configured account fails
closed, never falling back to a text send.

The facade uses existing agent-control v2 `send_reaction` and requires a correlated
`app_event_sent` response with nonempty valid message IDs. It performs one attempt.
The connector does not accept a reaction idempotency-key field: the request ID
correlates replies only. Connector application state deduplicates active own
reactions by account/group/target/content and returns the existing reaction ID.
Do not claim globally permanent remote key deduplication or exactly-once effects.

An outstanding intent means ensure this reaction remains active until acknowledged.
If an external actor removes the same account's reaction during uncertain delivery,
a retry may restore it. Operators must reconcile outstanding intents before
conflicting removal. Removal and toggling are outside this command's contract.

Existing serialized outbox delivery owns bounded attempts, retry timing, per-group
FIFO and escalation; unknown outcomes stay pending. A validated acknowledgement
marks delivery. Other groups can proceed past a failed group. Source support is
not proof of installed connector support: deployment must verify the running binary
and an authorized live reaction. Unsupported/rejected operations remain observable
failures, never silent success or a legacy CLI fallback.
