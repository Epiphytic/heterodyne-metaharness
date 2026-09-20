# Terminal observation deployment

Contract: [active specification](../spec/terminal-observation.md).

After reviewing the signed Bead patch, operator merges and runs the full suite on
that exact main commit with HERMES_WORKSTREAM_RUN, HERMES_WORKSTREAM_ROLE and
BTQ_SESSION_ID unset. Retain log, SHA256 and command for lifecycle evidence.
Back up harness SQLite/checkpoint coherently using the established quiet window.
Restart hermes-workstreams.service from the verified clean canonical checkout;
no model restart, new package, hook installation or task replay is needed.

Verify the original worker and manager identities remain unchanged. Observe a
natural turn: each checkpoint terminal_buffer has capture text, digest and identity.
An unchanged subsequent capture has changed=false (UI timers may change bytes).
Confirm a natural approval yields complete durable evidence and multipart visible
notices with exact native/pane identity. Do not manufacture a live approval or approve
one for testing. Record fixture-only coverage if no natural approval occurs.
Existing tmux history may be smaller than the requested 2,000 lines; clipped evidence
must be reconciled through Hermes, never treated as a full native request.

Rollback: return supervisor source to prior reviewed commit and restart only the
supervisor. Added run JSON fields are inert to older code; no schema/package rollback
is required. Preserve checkpoints and permission receipts. This document is a runbook,
not deployment evidence; completion awaits the operator's retained execution evidence.
