# Agent resolution of babysitter findings

Extends [continuation](continuation.md), [operator asks](operator-asks.md),
[session safety](sessions.md) and [change lifecycle](changes.md).

Detectors supply explicit unhealthy/healthy observations for invalid-pause,
missing-operator-notification, unverified-agent-job and blocked-handoff-commit
findings. Identity binds the registered run, category and stable subject. Never
infer invalidity from an intentional operator pause, nor health from a missing
observation. The repair ledger is durable diagnostic state, not task ownership.

Each finding queues a focused task to the existing manager through the durable
inbox. No replacement agents or native input from detectors. Delivery rechecks
manager idle, recovery, resume and terminal-state guards; ordinary steering has
priority. The agent must preserve approvals and independently retain repair
verification. Assignment grants no additional authority.

Verification requires a fresh positive observation, not an agent success claim.
After a submitted attempt, a fresh negative observation after the five-minute
verification window counts one failure. Pending/uncertain delivery is never blindly
resent. Three failed attempts open one durable tagged operator ask and stop repair
attempts. Missing delivery or verification for fifteen minutes also escalates.
State and inbox/outbox writes commit together; repeated polling and restart do not
reset the circuit. Only a verified clear followed by recurrence starts a new episode.
Pending input is superseded when cleared; open operator asks still require the
existing explicit evidence-backed resolution workflow.

The external EROFS observer excludes manager panes and matches anchored tool errors.
Repeated unchanged output is stale. Empty output and missing/replaced panes are
unknown. The externally installed adapter uses a source-digest-bound backup/apply
plan. Other detector producers use the same observation contract; absent producers
are not implicitly enabled or silently treated as healthy.
