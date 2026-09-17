# Status and pane fallback rollout

Normative behavior: [status specification](../spec/status.md).

This first slice of Bead 259f removes observation timestamps and adds pane-based
reporting suppression. It does not complete native notify integration, permissions
relay, deployment lifecycle or outstanding-ask work in the same Bead.

Operator deployment after review/merge: verify the intended signed commit in the
canonical repository; preserve a coherent private harness SQLite/checkpoint backup;
restart hermes-workstreams.service using the established operator procedure. Do not
restart coding sessions or replay tasks. Record service PID/start time and source
commit, then verify status shows pane_digest and pane_unchanged_ticks. With an
unchanged nonempty worker pane across two observations, confirm pane_stopped true,
no repeated progress outbox row, and a durable duplicate_status_error for attempted
static progress production. Genuine new pane output must reset the counter and
allow a changed notice. Verify outstanding approvals/recovery remain protected.
No live rollout or physical reboot is claimed by this source change.

The existing capture includes 200 scrollback lines. Normalization only removes
elapsed digits/units in recognized Working (... • esc to interrupt) chrome, not
arbitrary clocks or output numbers. Silent tools can have static panes: this is a
reporting fallback, never evidence authorizing input or task completion. Raw native
state remains available separately. No package, service configuration or native
approval setting changes are required for this slice.

Quality review: ripwire quality-delta is non-clean, reporting dynamically invoked
test fixtures as dead code, supervisor short-horizon churn, and test class growth.
No clean quality gate is claimed. Behavioral tests cover the reporting contracts.
