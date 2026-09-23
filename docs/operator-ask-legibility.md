# Operator-ask legibility

The operator directive for btq-dd4 replaces the stacked-summary reminder policy
in spec/operator-asks.md. Approval authority, routing, admin lookup, stable keys,
and native permission handling remain governed by the existing specifications.

Store the full original ask body and uncapped summary. Rendering preserves body
paragraphs and removes only recognized brain-plugin stamps. Replace inline ask
and Bead identifiers with numbered references whose values appear on a final
References line. Admin mentions precede that final line, after the question.
Raw evidence remains unchanged in the outbox; rendering is a presentation layer.

Each outgoing message contains only its own ask. Never attach outstanding asks
to status messages or to a new ask. A reminder is a distinct outbox event linked
to one existing logical ask, replaying all its original parts in order. Only
fully delivered, unresolved asks qualify. Schedule at most one reminder per
poll, at most hourly per ask, only when that group's outbox is idle. Ordinary
pending traffic takes precedence; failed reminders retain normal retry evidence.
Resolution cancels unattempted reminders; uncertain attempts retain their frozen
payload and idempotency key. Reminders never create new approval authority.

Existing frozen renders must not silently change under an uncertain-send key.
Legacy renders containing stacked asks, stamps or inline identifiers fail closed
for explicit delivery reconciliation. Already truncated legacy summaries cannot
be reconstructed; reminders use original outbox bodies, never those summaries.

Tests cover long multiline and Unicode bodies, babysitter persistence, multiple
open asks, separate reminders, resolution, restart/dedup, trailing references,
stamp removal, and frozen-payload retry/account invariants. Use mock transports;
no live messages are needed. Deployment remains manager-side.
