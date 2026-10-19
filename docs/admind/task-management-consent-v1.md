# Task-management consent presentation

This source component implements the product portion of approved design
`62e7ecf1433c601a36ad23b350da025b86a4c7dc`,
`docs/adr/2026-10-19-readable-management-approvals.md` on the reviewed design branch.
It does not implement queue mutation, native approval-bead authority, deployment,
or a fallback to an old daemon. A local poster can copy this presentation tag;
only a verified source-owned binding can support a management decision.

## Queue-child DTO

Post the existing `AskPost` (JSON discriminator `type: "post"`) with these exact
fields in addition to the ordinary local `poster`, `title` and `body`:

```json
{
  "kind": "question",
  "form": "choices",
  "choices": ["Perform this exact change", "Leave unchanged and held"],
  "recommended": "A",
  "presentation": "task-management-consent-v1",
  "render_version": 2,
  "management": {
    "request_id": "<64 lowercase hexadecimal characters>",
    "descriptor_sha256": "<64 lowercase hexadecimal characters>",
    "audit_url": "https://github.com/OWNER/REPO/blob/FULL_40_HEX_COMMIT/PATH"
  }
}
```

The metadata is a closed object; missing/extra keys, unsupported versions or
partial opt-in refuse. `links`, `pr_url`, `head_sha` and `bead` must be absent or
empty. Native kind stays `question`. Do not send raw JSON as title or body.
The title and multiline body use a deliberately narrow printable-ASCII literal
representation (body newlines only). Non-ASCII, combining marks, HTML/entity
syntax, renderer-owned prefixes, embedded URLs, Markdown, control characters and
secret-like strings refuse the whole request unchanged; no lossy sanitizer is used.
Audit URLs must pass the existing public-link validation and be immutable GitHub
blob links; percent escapes are refused. Refused facts must be corrected at the
producer, never shortened or disguised by the renderer.

Use operation-specific before/after prose, the reason, remaining holds, and
material holder/worktree impact. The renderer owns the heading, attribution,
request/digest prefixes, audit link, two choices and answer grammar. Do not repeat
these in the body. Entire original cards, including the actual native ask ID,
poster attribution and continuation framing, must fit 36 lines and 3200 characters
and the configured native chunk width. An over-budget card refuses before any
ask/outbox insertion; no truncation or legacy sanitization is used.

CLI flags on `admind ask post` are `--presentation task-management-consent-v1
--render-version 2 --request-id HEX --descriptor-sha256 HEX --audit-url URL`,
plus `--kind question`, the two repeated `--choice` values, `--recommended A`,
and the existing title/body-file flags. The posted
acknowledgment must echo the exact tag, version and metadata and stay question
kind. Missing acknowledgment is uncertainty, not permission to repost or use an
old writer.

## Binding readback

Use `admind ask binding --json --version 2 ASK_ID`. It uses the existing daemon RPC;
the explicit CLI option selects closed version-dispatched response decoding.
Default CLI readback retains the exact legacy v1 decoder. The internal
version-dispatched wire decoder can read genuine v1 results, but
explicit `--version 2` refuses a successful v1 envelope with `invalid-reply` and no
binding. The default `--version 1` success bytes are unchanged. Consumers must also
compare the exact presentation, never treat another card as this request acknowledgment.

With explicit `--version 2`, CLI refusals use the typed v2 reply domain and
`ask.binding.v2 unavailable`. That identifies the requested local decoding domain;
it does not prove the peer supports v2. The existing socket request is unchanged
and carries no version selector. Only a complete returned `EnvelopeV2` establishes
a successful v2 read; a refusal never supplies evidence or authorizes fallback.

A successful v2 reply is `AskBindingReplyV2` with `binding: EnvelopeV2`.
`binding.version` is integer 2. `binding.inputs` retains every v1 render input and
adds the exact `presentation`, `render_version` and closed `management` metadata.
The existing canonical sorted UTF-8 JSON algorithm hashes all these inputs into
`render_input_sha256`. Reproduction must match the actual retained original or
full-repeat card parts, with complete authenticated answer/decision histories and
a current trusted-policy snapshot. No client-supplied answer or policy files are
accepted. Pending/altered/truncated/unreproducible parts refuse as before.

The queue child must require native delivered status, exact metadata and descriptor
binding, a complete current-policy authenticated literal-A/accepted-positive
history and no contrary answer. Literal B records B; denial words and negative reactions record `none` (none of
these), not B. Both leave the request held, as the card legend states, and
ambiguous/contrary history must remain held. The presentation and title provide no
authority. This product component preserves the answer parser; it does not apply
management policy. The card lists every accepted positive word and emoji family,
including supported skin tones, and the denial-word/no/n distinction. A recorded
answer is not authorization: the queue must verify complete history and hold on
any contrary or ambiguous answer. Both B-then-A and negative-then-A remain in the
native history; the daemon does not enforce first-answer-wins. Management-only
choice confirmations explain this distinction; legacy confirmations are unchanged.
`!details` exposes the existing full accepted grammar, with the same final packed
36-line/3200-character and redaction checks as the card. An over-budget details
request refuses; it is never truncated and cannot widen either budget. Full repeat
reproduces the original card.

## Persistence and compatibility

Three nullable columns on `asks` (`presentation`, `render_version`, `management`)
are added in one transaction, without backfill. Metadata and original ask/outbox
writes commit together; insertion failure rolls everything back. Only all-three
NULL means legacy. Partial, malformed or unknown metadata refuses rendering or
binding. The original fixed-column legacy writer remains unchanged and produces
NULL metadata. A positional full-row predecessor writer fails atomically against
the migrated schema. No side table, synthetic metadata or old-writer fallback is
used. Legacy card generation, schemas, canonical v1 bytes and answer resolution
remain the default path.

Rollback requires stopping v2 producers first and retaining a compatible reader
until all genuine v2 requests are settled. This component cannot certify that
condition for a live queue. Do not alter posted cards, infer an answer from
silence, resend after uncertainty, or deploy on the basis of offline tests alone.
