# Native permission relay

Authority: [approval policy](approvals.md), [reactions](reactions.md),
[session lifecycle](sessions.md).

Captured terminal evidence is not an authenticated pending native request. A
recognized approval heading plus a trailing confirmation/cancel footer triggers
lossless relay of the captured approval region. Earlier scrollback is excluded.
The evidence is tied to the exact run, native identity, pane and content digest.
The original region is retained in SQLite and a durable manager inbox item.
Visible delivery uses numbered bounded parts with stable IDs; no 1200-character
summary replaces the permission evidence. Retries preserve those IDs. Every
acknowledged visible message ID is bound to its account, group and outbox part.
Unsupported UI forms remain native and require explicit inspection.

A pane may itself omit command content. Relay explicitly discloses that limitation;
it never asserts that a clipped terminal screenshot is a complete native command.
Manager inbox evidence remains available even if client display truncates a part.
This transport does not accept approvals, change native permissions or treat tool
output matching an approval heading as authorization. Reaction acceptance needs
an independently verified native request/response contract. Until that contract is
implemented and live-tested, no reaction may type approval keys into a pane.

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

Consent reaches the durable inbox without interrupting an active tool or native
approval. Gateway reload to install the callback is operator deployment work.
Receipt does not demonstrate successful native execution or resolve the Bead.
