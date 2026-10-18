# Plan9 offline acceptance core

Implementation of the approved [Plan9 d4 contract](https://github.com/Epiphytic/heterodyne-metaharness/blob/e009a2c6d1a4c7887bb5c5effd5f7d9bbbd023cc/docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d4.md).
This package does not start an observation clock, collect live evidence, migrate
work, exercise drills, archive repositories or declare the project released.
Plans 5–8 and genuine deployment authority remain execution entry gates.

## Interfaces

- `acceptance.models` contains closed immutable evidence records. `codec.decode`
  rejects unknown/duplicate keys, floating values, trailing JSON, invalid calendar
  timestamps, booleans in integer fields and noncanonical nanosecond strings.
  Nanoseconds retain exact unsigned 64-bit decimal-string values; UTC conversion
  uses integer arithmetic. Original artifact bytes are hashed before decoding.
- `Evaluator.entry` checks the eighteen gate names, governing amendment chain,
  three migration repositories, two preclock coverage categories and seventeen
  distinct required drills. Projected drill components must match both kind
  and content digest in the release identity. Native authority timestamps use
  explicit RFC3339 forms with at most six fractional digits; unsupported
  precision holds rather than being silently truncated. `interval` evaluates one ordered segment, seven
  coverage categories, continuous audit cursors, same-boot checkpoint endpoints,
  the 600-second full-audit limit and consecutive/total one-second error limits.
  It never transfers elapsed time across segments or treats publication time as
  a clock sample. Global adverse events interrupt the active segment even when
  their segment identifier is null. Operator activity inside the interval must
  be an approval. Prepared reserve activation requires typed bound evidence.
- `Evaluator.archive` validates the noncircular prearchive assessment → archive
  → final assessment graph, sealed interval, three repositories, second migration
  delta and unchanged release identity. Its explicit `event_bytes` input must
  contain the full authenticated event prefix through the unique matching Stop;
  missing events HOLD. It replays checkpoints, gaps, interventions and all seven
  coverage rows, binding the actual start/end and duration to the sealed Segment.
  It performs no archival effects.

Every source-dependent predicate invokes `Proofs.verify(ProofRequest, Inventory)`.
The immutable request carries a finite role, original Entry SHA256 and canonical
JSON context; embedded original byte strings use msgspec's base64 representation.
An adapter raises `Hold` unless it establishes the exact request. Inventory reads
require both the original Artifact ID and hash. A JSON `valid` field is never an
adapter. No adapter is selected from evidence JSON or ambient configuration.

The default `Unavailable` adapter always raises a typed HOLD. A future reviewed
native-authority adapter must call the pinned queue's actual `approval_valid`
and `ask_digest` policy and preserve raw timestamps, outcomes and provenance;
this package deliberately contains no alternative approval algorithm. Other
roles cover governing amendments, gate completion, deployed release inventory,
whole-queue migration, claim identity, coverage, drills, checkpoints, audit
registry, event classification, reserve activation, terminal fence/drain,
archive completion, final migration and current release. Source runbooks must
prove the full role-specific context, not merely the referenced file's existence.
Tests inject an explicitly synthetic adapter; it cannot be used as operational
proof. This is a trusted-code integration seam, not a sandbox against host owners.

## Private ledger protocol

`Ledger.create` explicitly creates a fresh private ledger. Existing directories
must be owned, non-symlink directories with mode 0700; files are mode 0600,
owned, single-link, regular files. No native database or product Store is opened.
Terminal/event publication uses one retained lock inode; independent source
spooling uses a second retained inode. Terminal publication and release acquire
that lock after the publisher lock to bind an exact spool snapshot; ordinary
source spooling does not acquire the publisher lock.
Artifact IDs are mapped by
hash to exact immutable bindings; they are never used as filesystem paths.
Objects are bounded to 100,000, the independent spool to 1,024 envelopes, each
JSON object to 8 MiB and each in-memory inventory to 128 MiB. Failed
preparations retain original bytes in separate private `.prepared` /
`.prepared-{namespace}` quarantine directories, each limited to 1,024 records.
They do not count as envelopes or objects. Exhaustion holds;
there is no deletion, pruning or inference that missing evidence means success.

1. Retain original artifacts and append the ordered event prefix.
2. Call `begin(segment_id)` to fsync the admission fence **before** requesting
   the final independent clock/audit capture. The production collector must
   continue durable source spooling independently of the publisher lock.
3. Retain that capture and call `heartbeat` with its exact post-fence event.
   Evaluate the complete prefix outside the publisher lock. Nothing in these
   APIs samples a real clock or proves capture order by itself.
4. `commit` replays the fenced event prefix with the evaluator for sealed results
   and checks the exact endpoint-derived duration and artifact/event graph,
   persists an immutable intent, asks the bounded terminal-fence proof adapter
   to establish source completeness, then atomically publishes one immutable
   terminal manifest. That manifest is the commit point. The proof adapter
   must inspect retained source snapshots only while the local lock is held;
   collection, native calls and product locks do not belong in this callback.
5. `recover` completes only manifest-selected original bytes. Before the commit
   point, the intent can be resumed exactly or explicitly aborted with admitted
   proof; unknown tails or conflicting bytes HOLD. After commit it cannot be
   aborted, re-endpointed or rewritten. A committed fence stays in place.
6. `release` requires explicit proof of complete independent-spool/cursor drain
   and exact outside-segment reconciliation events. It retains original spool,
   abort, intent, terminal and release receipts, then removes only the active
   fence. The typed manifest/release receipts retain the exact verified spool
   IDs. Reusing an already-terminal segment ID refuses before fencing. Abort
   binds the exact retained prepared intent; missing active fences give typed
   HOLD. There is no automatic uncertainty clearing or automatic replay.

A crash between initial directory creation and complete ledger initialization,
foreign permissions, missing originals, damaged manifests or capacity exhaustion
holds for diagnosis; no repair command guesses missing bytes. Production
collector admission, lifecycle management and recovery runbooks remain required.

## Offline validation and remaining entry requirements

Run with Python 3.12 and the locked project's dependencies:

```sh
PYTHONPATH=src:tests python -m pytest -q tests/test_acceptance_codec.py tests/test_acceptance_evaluator.py tests/test_acceptance_ledger.py tests/test_acceptance_archive.py
```

The synthetic suite covers exact numbers and wire grammar, every required gate
and drill, missing/changed artifacts, audit gaps and clock boundaries,
interventions, original timestamp preservation, default unsupported-proof HOLD,
terminal crashes before/after commit, exact recovery, unknown tails, retained
abort evidence, spool bounds and the final archive graph. These fixtures are
nonoperational and do not claim seventy-two hours of real work occurred.

Still required before operational entry: final source-specific reviewed runbooks
and adapters, full live source/audit registry and capabilities, both deployed
managed adapters with genuine identities, all predecessor/gate completions,
whole-native-inventory migration, natural corpus/drill/coverage evidence,
authenticated deployment approval, independent clock/collector/spool ownership,
and final genuine archive authority. This package supplies no production CLI or
activation wiring. Its tests, type checks and code review cannot satisfy those
execution requirements. The observation clock remains null.

Full-chain validation and repeated inventory reads remain bounded but expensive.
Quadratic append work and long terminal lock holds are unwaived performance debt;
these offline tests do not establish production collector latency or capacity.


R2 precision and archive diagnostics: valid native timestamps exceeding six fractional
places raise the distinct typed `Hold("unsupported_native_precision")`; malformed
calendar or offset input remains `InvalidEvidence("invalid_native_time")`. The shared
Hold type is re-exported from the evaluator. No fractional truncation is returned.
An archived sealed interval must include exactly one qualification event immediately
after its Stop; a chain ending at the Stop does not prove complete publication.

The second full high-reasoning review and exact provenance are retained under
`docs/reviews/plan9-r2`. Whole-history replay, repeated inventory/object scans and
proof-adapter work within terminal locks remain explicit performance debt; no
72-hour throughput or independent-source latency acceptance is claimed. Source
adapters, corpus collection, live drills and deployment remain incomplete gates.

Second-review ledger repairs add per-attempt fence identity, validate aborts and
complete event proofs before publication, and replay exact durable release programs
through receipt-before-cleanup crashes. Only receipt-proven spool IDs retire from
active capacity; original bytes and unrelated late arrivals remain retained.
Original retention has a separate finite bound. Repeated proof and history scans
remain open performance debt; offline checks establish no 72-hour throughput.
