# One-review author incorporation

Actual verdict: REVISE. Incorporated once; no second review or validation. Human approval remains required.

## Finding 1

1. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:89`: Every acquired record is put into the ledger spool, which allows only 1024 active records (MAX_SPOOL), keeps at most 100000 files in the spool directory over its lifetime (MAX_OBJECTS), and frees space only on terminal release. Pending captures are never deleted and are capped at 1024, so a 72-hour run cannot fit, and the planned 100000-row benchmark cannot even run.

   Failure scenario: WSD emits more than 1024 progress rows before the terminal fence, which takes 8 full 128-record batches or a few hours of normal activity. The next Ledger.spool raises spool_overflow and acquisition HOLDs for the rest of the segment. Separately, each completed batch leaves its pending capture behind. If empty polls also create one, 60-second polling gives 4320 captures in 72 hours, far over the 1024 cap. The parent design (d4, line 167) uses the spool only for arrivals while the fence is held, not for all acquisition. Spooling every record also costs O(n^2): each spool() call re-runs _spool_ids(), which re-reads and re-hashes every active spool file and retirement receipt while holding spool.lock.

Author disposition: §4 dedicated finite occurrence store; completed pending cleanup, no empty receipts; §5 separately preserves ledger publication cap.

## Finding 2

2. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:117`: Ledger.release retires whatever _spool_ids() returns at that moment, a raw list of spool digests that knows nothing about acquisition receipts. A terminal release can therefore retire originals from a batch that has been spooled but has no receipt yet, contradicting the claim that releasing snapshot A cannot consume later snapshot B.

   Failure scenario: Acquisition persists pending batch B and spools 60 of its 128 originals (spool.lock is free between spool() calls). Commit or release then takes publisher.lock and spool.lock, snapshots the spool IDs including those 60 B records, and writes spool-retired markers for them. B's receipt is published afterwards. B's records were consumed by snapshot A's release without being covered by any reconciled receipt or terminal cursor bound.

Author disposition: §4 receipt-only selectable ranges; ledger release has no acquisition retirement API.

## Finding 3

3. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:110`: Spool entries are keyed by the content digest and retired by digest, so a later record whose bytes match an already-retired original is silently treated as retired. That breaks the requirement that arrivals after the snapshot stay active.

   Failure scenario: A byte-native JSON source emits the same record (for example an identical status line) at ordinal 5 and again at ordinal 900. Ordinal 5 is released and retired. When ordinal 900 is captured, spool() finds the path already exists and returns the digest without error. _spool_ids() leaves it out because it is retired, so the new arrival never appears in the next active snapshot.

Author disposition: §4 occurrence tuple includes lane, registration and ordinal; matching content never erases a later occurrence.

## Finding 4

4. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:101`: Ledger.spool calls codec.load, which rejects floats, duplicate keys, nesting deeper than 64 and more than 200000 values, and raises InvalidEvidence rather than Hold. This check happens only after the pending capture is already durable, and recovery must finish that exact capture, so one record the spool won't accept blocks the source forever without a defined HOLD code.

   Failure scenario: A byte-native source record contains {"t":1.5}. The pending capture is fsynced, then spool raises InvalidEvidence('noninteger_number'). Every restart has to complete the same pending capture before reading anything new and is not allowed to overwrite it, so the source never advances and the error is not one of the design's fixed HOLD codes. The design checks only for oversize records, not for records that are not valid strict JSON, before persisting.

Author disposition: §2/4 byte-native retention bypasses strict JSON parser; structured parsing cannot poison acquisition restart.

## Finding 5

5. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:71`: The design says a changed previously retained cursor must HOLD, but capture(after, ...) only returns records from after+1 onward. Nothing re-reads the record at the last acknowledged ordinal and compares it to its retained digest, so a restore that is later followed by new appends cannot be detected.

   Failure scenario: Ack is at seq 5000. The journal is restored from a backup at seq 4800, and wsd then writes 300 new events, so seqs 4801-5100 now hold different content. On the next capture high-water is 5100, which is not a regression, and records 5001-5100 are accepted as contiguous. The replaced 4801-5000 range goes unnoticed. An anchor check (re-read the row at `after` in the same snapshot and compare its envelope digest) would catch this without needing an epoch.

Author disposition: §2 same-snapshot anchor check plus explicit limitation: anchors do not prove a complete restore-safe epoch; WSD remains diagnostic.

## Finding 6

6. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:94`: The receipt schema is declared exact and closed, but it records no ledger entry digest or run, no fence or terminal anchor, and no boot/monotonic capture time. It also uses integer ordinals while the parent's AuditInterval uses ID-typed cursors and needs from/to monotonic_ns. A later checkpoint or terminal-fence contract cannot be built from these receipts without changing the schema.

   Failure scenario: Two runs or ledgers share one source's acquisition namespace, and a receipt cannot say which ledger's spool holds its originals. The future terminal contract must show that the capture happened after Ledger.begin's fence (ledger.py:269-272, where the fence comes first and sampling after), but receipts carry no fence digest or boot/monotonic time. AuditInterval.from_monotonic_ns/to_monotonic_ns cannot be filled in from a receipt.

Author disposition: §4 closed run/lane binding, observed capture clock and optional fence correlation; §5 separate real AuditInterval/fence proof remains required.

## Finding 7

7. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:63`: Snapshot reads under the Journal's in-process RLock and connection mean the collector has to run inside the wsd process, and the topology is never stated. The read holds the mutex that wsd's event loop and worker threads use. If 'checks row sizes and schema' reuses _check, it runs PRAGMA integrity_check on every capture.

   Failure scenario: A collector in a separate process can't get an opened Journal without constructing one, which the design forbids, so in practice it ends up in wsd and shares wsd's privileges and failure domain. That weakens the authority separation the design claims. Running _check per capture does a full integrity_check over a 100000-row journal while holding the RLock and stalls wsd. Journal.transaction() and snapshot() use BEGIN IMMEDIATE, so reusing them takes a write lock instead of a read snapshot.

Author disposition: §1/3 explicit in-process same-UID topology, bounded read BEGIN and no integrity_check/BEGIN IMMEDIATE per poll.

## Finding 8

8. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:56`: The 'lossless typed' SQL envelope leaves out the storage-class encoding (typeof, BLOB, non-UTF-8 TEXT), and the pre-materialization byte check isn't defined. SQLite length() on TEXT counts characters, not bytes, and JSON escaping can make the envelope up to 6x larger than the SQL value.

   Failure scenario: A detail value of 400k characters, mostly control characters or 4-byte code points, passes a length()-based check under 1 MiB, but its JSON envelope is 2-6 MiB, which exceeds the batch limit after materialization. A BLOB or invalid-UTF-8 value in `at` or `detail` (dynamic typing) either makes Python's sqlite3 decode fail or gets coerced by str() like events_since does. Either way the 'lossless' claim fails, or it hits an undefined HOLD.

Author disposition: §3 closed storage-class envelope, byte preflight with CAST AS BLOB lengths and exact hex envelope calculation before content fetch.

## Finding 9

9. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:109`: Artifact identity is supposed to bind source ID, epoch, ordinal and digest. But m.Artifact.id is a 128-character ID and Binding stores only (id, sha256), so the binding can't fit in the ID. Step 3 (WSD diagnostics) also needs an owner epoch for receipts and identity, yet WSD has none, and the design rules out guessing one.

   Failure scenario: Source ID (up to 128 chars) + epoch ID (up to 128) + 10-digit ordinal + 64-hex digest is longer than ^[A-Za-z0-9_:.+\-]{1,128}$. Any workaround that hashes these into the ID loses the binding unless a separate retained mapping object is specified. For WSD, implementers must either invent an epoch value, which contradicts 'no guessed epoch', or fail to produce receipts. The design should define an explicit 'unestablished' epoch type that cannot be confused with an admitted epoch.

Author disposition: §2 unestablished epoch union; §5 bounded hash IDs with separately retained exact mapping Artifacts.

## Finding 10

10. `docs/designs/2026-10-18-plan9-source-acquisition-boundary.md:113`: The design says capture and spooling continue while publisher.lock is held, but Ledger.commit and Ledger.release take spool.lock while still holding publisher.lock and run the terminal proof callback inside it. Spooling is blocked for that whole window, and the proposed test only holds publisher.lock in a separate process.

    Failure scenario: During commit (ledger.py:544-568) or release (682-737), spool.lock is held through _proof, _object writes and retirement markers. Acquisition then blocks in Ledger.spool, possibly while holding its own acquisition lock, for as long as the proof takes. The planned regression test, which holds only publisher.lock, passes and never exercises this. The design should either bound or measure this window or test against an actual commit/release in progress.

Author disposition: §4/5 independent acquisition lock/store; §6 actual blocked commit/release fixtures, not publisher-only proxy.

## Additional frontier note

The doc's mutable frontier pointer can't be built with the ledger's write-once `_atomic` helper, which refuses to overwrite. It also duplicates what the receipt chain already records. Numbered, contiguous receipt files (the ledger's `_events()` pattern) would remove the "interrupted frontier update" case. This was left out to stay within the 10-finding cap.

Author disposition: §4 numbered contiguous immutable receipts are the only frontier; no mutable frontier pointer.

## Publication filing

Original raw SHA256: `c84b400c94a723b2d281b46a22eb976ca6eb88f266c758833628bf7714632b31`. Public encoded raw SHA256: `c215a2f1595a70a425a41eefdf496a4cf179a6b9e33a090a81ffc4c5e64328f3`. Public raw home-path text uses reversible HTML entity encoding; replace `/home/&#111;penclaw` with the decoded path to recover original bytes. Original external log is untouched. Finding text is unchanged.
