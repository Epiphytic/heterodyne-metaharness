# Plan9 source acquisition boundary

Status: author proposal; no design review or human approval yet. This document
does not authorize deployment or start the qualification clock.

Parent: [approved Plan9 d4](https://github.com/Epiphytic/heterodyne-metaharness/blob/e009a2c6d1a4c7887bb5c5effd5f7d9bbbd023cc/docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d4.md),
native approval `btq-e9toz`. This proposal makes the previously unspecified
acquisition/retention interface concrete. It changes no qualification predicate,
approval requirement, source completeness obligation, or 72-hour duration.

## Problem and existing implementation

The acceptance ledger already separates `publisher.lock` from `spool.lock`.
`Ledger.spool(raw)` retains exact strict JSON bytes without the publisher lock;
`Ledger.retain(Artifact, raw)` publishes the same original bytes and immutable
identity under the publisher lock. Terminal release names an exact spool
snapshot, so later arrivals stay active. These mechanisms currently have no
production source acquisition owner.

`wsd.journal.Journal.events_since(seq, limit)` reads real progress events with
fields `seq, at, ws, bead, kind, detail, ref`. It bounds row count only. Its UTC
`at` is not a boot clock, its SQLite sequence is not an authenticated epoch,
and this log is not a complete host, native approval, process, or privilege
audit. Constructing Journal can create a database. A collector must therefore
receive an already opened owner; it must never construct Journal from a path
in submitted evidence or discover the host's database.

The existing captured native and managed-install adapters remain diagnostics.
Neither a successful parser nor this acquisition protocol turns them into
authority. Default acceptance `Proofs` remain Unavailable.

## Finite acquisition contract

Trusted deployment construction admits each concrete source owner separately.
Its registration pins source ID, owner epoch, implementation/configuration
identity, retention location, cursor semantics and supported snapshot method.
Registration is a retained artifact with independent admission evidence; a
JSON string or constructor flag is not proof of registration. No evidence
field chooses a Python class, executable, arbitrary path or database.

The internal read interface is `capture(after, record_limit, byte_limit)`.
It returns a closed immutable batch with source ID, admitted owner epoch,
after ordinal, high-water ordinal, and ordered `(ordinal, original_bytes)`
records. Limits are 128 records and 1 MiB total original bytes per batch;
ordinals are exact integers 0..2147483646, rejecting booleans. Zero is only the
initial cursor. IDs use the existing 128-character acceptance ID grammar.

The high-water belongs to the source snapshot, not acquisition completion time.
Records are contiguous from after+1, never beyond high-water; a partial batch
must make positive progress. An empty batch requires after==high-water. The
owner must enforce the byte limit before materializing unbounded row values.
An oversized single record is an explicit unsupported-source HOLD, never
truncation, stringification or a skipped cursor.

For byte-native JSON sources, original bytes are retained verbatim before any
decoding. For SQLite progress rows, the source itself supplies a separately
versioned lossless field envelope: all seven original typed columns, including
the exact original `at` and `detail` strings and nullable columns. This is a
representation of SQL values, not a claim to original database-file bytes.
The raw database and WAL are protected through the approved backup owner;
the collector does not copy a live database file or invent byte provenance.

The WSD owner adds a bounded snapshot-read method under its existing journal
mutex and a single read transaction. It first checks row sizes and schema,
then reads the bounded rows and snapshot high-water. It must reject an existing
write transaction rather than joining it. No schema migration, event emission,
source mutation, SQLite write, producer pause, service restart or schema-check
bypass is part of capture. Busy/unusable/corrupt outcomes become distinct fixed
HOLD codes with original private exception evidence retained.

An epoch change, missing cursor, regressing high-water or changed previously
retained cursor is HOLD. The production owner registration must establish
immutability and detect restore/replacement of the source; an inode alone is
insufficient. If the current WSD journal cannot establish that guarantee, its
captured rows are diagnostic inventory only, and it cannot satisfy audit
continuity. No guessed epoch or automatic cursor reset fills this gap.

## Durable acquisition and publication

Each source has a private mode0700 acquisition namespace, with owned single-link
mode0600 regular files and a retained lock inode. Paths are generated from
validated digest keys, never source IDs used as path components. Reuse the
ledger's checked private-file primitives rather than copying an unsafe writer.
Acquisition does not hold a product/queue lock or publisher.lock.

Before spooling, persist an immutable pending capture containing the complete
exact batch bytes, prior receipt digest and next intended cursor. Fsync the
capture and its directory before acknowledging any progress. Then spool every
original unchanged, persist a canonical immutable receipt referencing each
original digest, and fsync it. Finally publish a durable frontier pointing to
that receipt and fsync the directory. Only this final publication acknowledges
the cursor. A receipt proves local retention, not source authentication.

The receipt has exactly: schema version, source ID, owner epoch, previous
receipt digest (nullable only at zero), after, through, high-water, and ordered
ordinal/digest pairs. Pending original bytes and completed receipts are never
deleted as part of retry or terminal release. Bound pending captures to1024
and completed receipt objects to100000; exhaustion holds with originals intact.
Existing ledger spool/retained-object bounds also apply. There is no pruning.

On restart, finish the exact pending capture before asking the source for a
new batch. Do not overwrite it with a later snapshot. Verify every pending
original and every receipt link. An identical retry is idempotent; conflicting
bytes, multiple incompatible tails or a missing acknowledged original HOLD.
An interrupted atomic frontier update is resolved only from its retained exact
intent and complete receipt, never from a maximum filename or source absence.

Publishing captured artifacts is a separate operation after releasing the
acquisition lock. Artifact identity binds source ID, epoch, ordinal and original
digest. Publication calls `Ledger.retain` on the exact spooled bytes; it never
re-reads the live owner. Terminal proof callbacks consume immutable captured
snapshots only. They perform no source reads, native calls or product locking.
Source capture and spooling must continue while the publisher lock is held.

The source owner still needs an independently admitted terminal fence/coverage
contract before a receipt can satisfy checkpoint or terminal_fence proof.
This proposal supplies no such proof. Releasing snapshot A cannot consume
later snapshot B or retire the acquisition journal. No terminal conclusion is
inferred from an empty source batch.

## Delivery order and checks

1. Implement bounded capture/receipt/retry mechanics with a real independently
   persisted temporary owner. Preserve noncanonical original JSON bytes.
2. Add the WSD owner's read-only bounded snapshot method and test it against
   an actual independently written temporary Journal, including WAL concurrency.
3. Bind WSD progress capture as diagnostics through explicit trusted construction.
   Keep every operational source proof unavailable until registration and real
   completeness/epoch guarantees are demonstrated.
4. Admit other source owners individually through their existing supported APIs.
   A generic source protocol is not admission of native approvals, Marmot
   history, daemon control, process audit, clocks or installations.
5. Prepare a separate exact-release deployment request with parent design and
   this amendment linked, configuration/source registrations, protected data,
   rollback, and a list of deviations. Offline merges are not deployment.

Required regressions cover gaps, overlap with changed bytes, epoch rotation,
regressing watermarks, oversized values before row materialization, busy and
poisoned journals, each fsync crash boundary, exact retry after source append,
full spool/receipt capacity, unsafe private files and competing acquisition.
Use a separate process to hold publisher.lock while capture completes; prove
post-snapshot arrivals remain active after exact release. Preserve production
constructor/network/service traps. Tests must show default checkpoint and
terminal-fence proofs still HOLD even with a complete acquisition receipt.

Record measured capture time, peak retained memory, lock duration and owner
load on a representative100000-row temporary journal. No numerical production
latency claim is made before measurements; if the 300-second nominal capture
and 600-second maximum-audit requirements cannot be met, entry remains HOLD.
Full configured typing, focused tests, existing acceptance oracles, normal
hooks, actual Claude code review and hosted CI remain merge gates.

## Human decision

Approve this acquisition/retention boundary and the explicit lossless SQL-value
envelope, permitting offline implementation and diagnostic WSD integration.
Accept that insufficient epoch/completeness capabilities keep operational
acceptance blocked until a separately demonstrated source-specific contract
exists. This approval does not waive that contract, activate a collector,
authorize production data access, deploy services, or start the release clock.
