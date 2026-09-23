# Quota monitoring

The optional standalone monitor collects server quota snapshots and explicitly
labeled configured estimates. Configuration owns provider adapters, ceilings,
models and OPS destination. Missing data or ceilings are errors, never zero quota.
Five-hour estimates from daily-only sources are conservative upper bounds.
Codex duration and reset timestamps identify authoritative windows; shared limits
must not be misrepresented as model-specific quotas.

Threshold identity is source/model/interval/window-epoch/threshold. Persistent
admission and frozen delivery batches survive restart; retry uses the same socket
idempotency key. Delivery awaits the installed agent-control client and uses text.
No wn CLI or model dispatch is permitted. Failures retain waiting-on-agent health,
queue OPS error alerts and return nonzero. Unavailable transport retains pending
alerts and never asserts a successful notification.

A systemd timer polls every ten minutes. Retain JSONL history for 90 days, pending
alerts until acknowledged, and threshold identities through window-end plus 90
days. Deployment/activation remains operator-owned under [changes](changes.md).
The design document docs/usage-monitor.md records measurement limitations.
