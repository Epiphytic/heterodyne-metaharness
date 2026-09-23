# Explicit workstream closure (btq-bjs)

## Problem and design

A deploy status event terminalized octo-sts-rust because its persistent flag was
absent. The observer and status projection then hid outstanding work for roughly
27 hours. Task results must not determine the lifetime of their workstream.

New runs default to persistent; `start --no-persistent` is an explicit compatibility
opt-out, never permission to close. Completed status events and successful command
exits record task completion and leave the run idle. Failed commands remain failed
(nonterminal). Repeated observation of the same exited command reports once.
Status events on an explicitly terminated run record task data without reopening it.

`close RUN --consent-file JSON [--force]` is the sole completion operation. Existing
`stop` still stops processes, but shares the terminal guards and consent contract.
Both persist terminal state before stopping panes, preserving reboot behavior.
Neither command closes Beads, resolves asks, deploys, or accepts native prompts.

Consent is a trusted local operator-evidence attestation, matching the existing
approval override boundary, not cryptographic authentication. It contains exact
`run_id`, `action` (`close` or `stop`), `approved_by`, verbatim `response`, retained
`evidence_ref`, boolean `force`, and sorted `open_beads` IDs explicitly covered by
an override. A worker's task completion, blanket permission, or a status message is
not consent to close. The CLI retains the complete record and digest in SQLite and
the checkpoint. Consumed consent cannot be reused after resume. Identical retries
of an already terminal operation can finish stopping panes without new authority.

Under the normal supervisor lock, fetch all Beads in the bound workstream (all
agents and statuses, not just ready work); also inspect the bound issue and linked
delivery members. Fail closed on unavailable queues, malformed metadata or bounded
read overflow. Nonclosed beads block unless both --force and the consent's exact
open-bead set match. Unresolved operator asks always block. Pending deployment
members and lifecycle histories at merged/final-tested without deployment completion
also block, even with force; library delivery formulas without deployment are exempt.
The guard observes Beads immediately before the local transition; separate Beads and
SQLite stores cannot provide a distributed atomic snapshot. Operators must quiesce
admission before closure; later admissions require explicit reopening.

The read-only /status projection includes terminal runs with nonclosed cached routed
beads, retaining bounded scans and ambiguity checks. This exposes historical damage
without automatically restarting a closed run or treating cached data as authority.
No live sends or manager-side deployment are part of this change.

## Validation

Regression tests cover persistent defaults and opt-out; completed events on legacy
and nonpersistent runs; command exit dedup; no-consent and stale-consent rejection;
open-bead force scope, queue failure, unresolved asks and pending deploy refusal;
terminal status visibility and explicit close/stop with retained evidence. Run the
full harness suite and governance validation before recording tested and pr-open.
