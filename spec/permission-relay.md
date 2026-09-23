# Native permission relay

Authority: [approval policy](approvals.md), [reactions](reactions.md),
[session lifecycle](sessions.md).

Captured terminal evidence is not an authenticated pending native request. A
recognized approval heading plus a trailing confirmation/cancel footer triggers
lossless relay of the captured approval region. Earlier scrollback is excluded.
The evidence is tied to the exact run, native identity, pane and content digest.
The original region is retained in SQLite and a durable manager Bead request.
The supervisor creates the Bead when it observes the prompt. If Beads creation
temporarily fails, the durable request is retried by the one-minute pickup.
The one-minute manager pickup claims that Bead before steering the existing idle
manager session. Observation itself creates no operator outbox item. The Bead
contains the exact captured region, run, pane, native session and stable relay ID;
the manager still verifies the live native dialog before acting. If the terminal
omits a native session ID or the detector recognizes a modal form the relay does
not parse, the Bead marks that uncertainty for inspection.
Unsupported UI forms remain native and require explicit inspection.

Hermes manager observation requires the native dangerous-command panel's title,
allow/deny choices and trailing live selection hint before reporting a pending
approval. Historical prose about approving a command is not a pending request.
This heuristic does not authenticate a request or authorize an approval; an
unrecognized screen remains unknown, never proven safe or completed.

A pane may itself omit command content. The manager Bead discloses that limitation;
it never asserts that a clipped terminal screenshot is a complete native command.
Manager inbox evidence remains available even if client display truncates a part.
Observation does not accept approvals, change native permissions or treat tool
output matching an approval heading as authorization. The manager may answer a
verified native dialog under the standing auto-approval policy; dangerous classes,
clipped requests and missing authority require one operator escalation.

Historical numbered outbox parts and acknowledged message bindings remain readable
for old consent receipts. New observations do not emit those parts or invite a
reaction as the approval path.

## Durable consent and native inspection

The authenticated Marmot mutation callback accepts thumbs-up (approve once) or
thumbs-down (deny) only for an exactly recorded account/group/message binding and
an explicitly allowed non-self sender. It retains the entire connector event,
including original signed event data when supplied, alongside the exact delivered
message text, complete captured approval region and observation digest. It does
not invent original Nostr fields absent from the connector's event. Wildcard
chat access is not consent authority. Changed run/native/pane/group bindings fail
closed. Conflicting content for an existing reaction identity is an error.

A transaction records `hermes.native-consent.v1` evidence and one manager inbox
handoff; duplicate delivery is a no-op, failed transactions can retry. The state is
`requires-native-inspection`, never `approved` or a resolved Beads gate. The
manager must inspect the current native request and full command/reason against
that evidence before its once-only native decision. Missing, clipped, stale or
ambiguous requests require reconciliation. No automatic keystrokes or guessed
native request IDs are used. Terminal observation and Beads gate integrations
consume this evidence schema; a later gate can adopt the observation and consent
identities without confusing delivery acknowledgment with native approval.

Historical consent reaches the durable inbox without interrupting an active tool
or native approval. Receipt does not demonstrate successful native execution or
resolve the Bead. New native resolution requires an independently observed
post-action screen, a Nostr-signed Bead comment containing the full captured
approval text, and an append-only `runs/RUN/approval-log.jsonl` record.

Capture and exact worker/manager buffer comparisons follow [terminal observation](terminal-observation.md).
