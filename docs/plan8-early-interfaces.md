# Plan8 early implementation interfaces

This implementation follows the approved Plan8 d4 scheduling and reconciliation design. It provides
synthetic-testable interfaces before the remaining Plan5–7 integrations. It does not install units,
initialize production state, register live effect producers, start timers, send alerts, or establish
72-hour live acceptance.

`wsd.timer_owner` remains `internal` by default. Changing it requires a restart. `systemd` ownership
requires the installed private `schedule-manifest.json`: exact installation/generation UUIDs, UID,
canonical config/state/queue directory identities, release/unit digests, enrolled workstreams,
cadences and finite job registry. Invalid manifest identity refuses systemd-mode startup; internal mode ignores leftover external manifests. `schedule_units.render`
only returns immutable service/timer text; an approved deployment must capture previous owners,
verify the actual immutable installation closure, retire conflicting wake owners and enable units.

The scheduled socket variant admits pickup/reconcile for all enrolled lanes or none. It preserves
BACKSTOP trigger semantics and records at most 64 origin IDs in memory. The existing private control
socket exposes `wsctl scheduled-hold` for switch step2: it closes new scheduled/effect/paired-backup
admission on the serving loop until daemon restart. It does not pause workstreams, stop admitted jobs,
retire timers, suppress observer diagnostics or grant deployment authority. The installer must still
stop/quiesce runners and drain WSD, then verify the new owner before enabling matching timers. Core wakes do not require
civil-clock readiness or an available effect store. Health publication begins before recovery and
uses immutable cached effect observations rather than event-loop SQLite queries.

The private effect database is opened by one daemon writer. Missing, incompatible or corrupt state
holds effects; ordinary startup never initializes or replaces it. Explicit initialization helpers
are deployment primitives, not command-line recovery authority. The generic `effect-wake` request
refuses while no reviewed producer is registered. Civil slot arithmetic and reconciliation are
available to future registered producers; no live clock synchronization source is inferred here.

`paired-backup` accepts only the manifest's installed owner binding. The external-owner adapter
requires an explicitly initialized private installation-bound counter, calls the native backup once,
and makes one companion request. It does not select, replace or invent the host's native backup
owner/cadence. Deployment must inventory that real owner and wire this adapter under its original
maintenance authority. A missing/corrupt/locked companion counter holds only the companion; the separately owned native
attempt still runs once, with no invented cycle ID. Unknown native outcome or unavailable companion
never creates a second native attempt. A daemon copy lane takes separate read-only WAL snapshots, writes
verified private artifacts before its manifest and settles only exact matching durable attempts.
After positive local copy-worker completion or exclusive process-owner recovery, failed/incomplete
local attempts are recorded failed and consumed; later native cycles can create uniquely named local
copies. This never replays the native backup or turns unknown native status into success.
Native-unknown bundles are not successful acceptance evidence. Retention preserves unresolved and
last verified artifacts. A real restore rehearsal remains a separate deployment/acceptance gate.

The observer reports independent true/false/unknown conditions and stores bounded episode state.
A maintenance window labels observations without suppressing liveness diagnostics or renewing
itself. Shared `local-alert-v1` files use exact canonical identity/payload encodings, a bounded
cross-process directory lock and a 256-file cap. Coalesced notices remain pending locally until
space returns. A bounded private exact-ID/hash pending set makes retries idempotent; saturation of that
255-entry dedup set refuses only new local diagnostics, never core wakes. Only explicit owner retirement
increments retired counts, not ordinary clearance/publication. Shared helper metadata/byte fixtures
must include this pending field before cross-package activation. Local publication/settlement is not relay delivery or operator acceptance. The
reserved overflow file can change locally after admind's filename-based deduplication, so later
remote freshness is not promised. The Plan6C integration must pass a shared byte fixture before
activation; this implementation does not claim the sibling consumer is already deployed.

Offline regression coverage includes legacy daemon/socket behavior, strict framing and identities,
all-lane admission, fatal partial submission, effect uncertainty/restart, civil DST slots, WAL copy
consistency, exact backup-attempt recovery, bounded health, observer reset/unknown state, alert
capacity/idempotency, native one-attempt pairing and pure immutable unit rendering. Live ownership,
transport delivery, native backup health, restoration and long-running service acceptance remain
unclaimed. Quality-delta findings remain unwaived in the accompanying author evidence.
