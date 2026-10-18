# Admind source operation protocol

Implementation scope: approved recovery design `e613fd9e91acd4263e9871b402049b338171924a`, section 5. This server capability does not implement the sister Journal, Store fence, installer, or publication recovery clients. No deployment or end-to-end withdrawal is asserted.

## Wire contract

Use the existing local ask Unix socket, one UTF-8 JSON object followed by LF. The closed request has exactly these fields (all required):

```json
{"type":"approval.operation.v1","mode":"post","source":"<sha256>","journal":"<sha256>","operation":"<uuid>","bead":"btq-abc12","context_digest":"<sha256>","intent_hash":"<sha256>"}
```

The msgspec tag field is `type`. Modes are `post`, `status`, `withdraw`. Each hash is exactly 64 lowercase hexadecimal characters; UUID is canonical lowercase hyphenated UUID text. Bead grammar is `[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}`. `bead` and `context_digest` may both be JSON null for status/withdraw, never post. Unknown/duplicate fields, wrong types and requests over 16,384 bytes refuse. No caller supplies a decision, answer, operator, database path, or scope authority. Socket peer UID must equal daemon UID; this is trusted same-UID convention, not enrollment security.

The identity is SHA256 of msgspec JSON encoding of `[source,journal,operation]`. Its first durable association fixes bead, context, intent and UID. Changing any association refuses; `withdrawn-payload-conflict` identifies a tombstoned key without rebinding it. A beadless tombstone prevents a delayed same-key post. If a client later discovers an orphan bead, retain that diagnostic in its own Journal; this server does not infer cancellation or allow the key to be rebound.

Replies contain `result`, `code`, `receipt`, `revision`, `withdrawn`, `lineage` (defaults are encoded). Every lineage entry contains `ask_id`, `status`, `digest`, `refreshed_from`, `attempt`, `answers`, `decision_refs`, `outbox`. Answers are message IDs, decisions are immutable attempt references, outbox entries are durable keys; no answer text is returned. Receipt is a stable association hash, not an authority signature or physical-effect proof. Revision is an observational CAS generation, not permission. Maximum encoded reply is 1,048,576 bytes.

Results are `posted`, `ok`, `withdrawn`, `decision-in-flight`, `not-found`, `refused`, `unavailable`. Fixed codes further describe the outcome. Clients must treat transport loss, malformed replies, generic socket `failed`, and store unavailability as unknown and perform status/readback, never infer absence or replay an effect. A successful status is not consent. Exact bound post retries perform read-only observation without new native reads, capacity, cards, supersession, or audit events.

## Atomicity and races

First enrollment enumerates every same-bead approval row, verifies retained refresh parents and digest identity, and maps all rows or rolls back. Another key for the bead refuses. Existing incomplete history holds. Post uses existing principal/latch/capacity/rate and native context checks before creating a card. Mapping, card and outbox are committed together. Legacy approval posts for an enrolled bead refuse; unrelated legacy asks retain their prior behavior.

Withdrawal sets a durable tombstone and cancels all eligible open/answered siblings, including durable notices, in one transaction. Deciding/uncertain/issued work remains intact and is reported as decision-in-flight; native consent is never rewritten. The decision worker checks the tombstone again in its transaction before claim and before issuing. Refresh checks it in the same transaction and descendants inherit the key. A withdrawn in-flight attempt remains for existing native reconciliation; a later positive untouched reconciliation may reopen a row, but the tombstone prevents decision issuance and a repeated withdrawal cancels it. Neither time nor a failed read proves absence.

Audit rows bind exact peer UID/PID, association, action, time and before/after reply hashes in the mutation transaction. Limits are 4,096 operation associations, 256 lineage rows per operation, and 4,096 returned answer/attempt/outbox references total. Overflow holds without partial mutation. There is no automatic tombstone expiration or pruning; authenticated future retention/recovery policy is required before a full store can admit more distinct keys. These bounds do not authorize deletion or bypass capacity/policy.
