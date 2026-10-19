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
The title and multiline body must be literal plain text: no Markdown formatting,
control/bidi characters, unsafe secret-like strings, or lossy sanitizer output.
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
Default CLI readback retains the exact legacy v1 decoder. A v2-capable reader also
accepts genuine v1 results, but consumers must compare the requested version and
presentation, never treat a v1 response as a v2 request acknowledgment.

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
history and no contrary answer. Existing negative reactions map to `none`, and
ambiguous/contrary history must remain held. The presentation and title provide no
authority. This product component preserves the answer parser; it does not apply
management policy. `!details` exposes the existing full accepted grammar without
altering original card bytes; full repeat reproduces the original card.

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
