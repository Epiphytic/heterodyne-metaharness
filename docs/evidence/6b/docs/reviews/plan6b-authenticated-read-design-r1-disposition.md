# Plan6B actual design R1 author disposition

Author: Codex. Proposed d2; no reviewer or human approval is asserted. R1 is 1 of maximum3; no reset.

Actual raw review SHA256: `628b9583c7d2804b6dfb8cf032684c3eb252d686d9c13d1ec8901ad79ab23c87`. Raw prose reports26problems; fenced JSON has **27** entries. Every entry below preserves its actual order and wording. Requested model/effort are invocation evidence, not an assertion of inner reviewer identity. Review reported no separate verification pass.

The missing6Bdatabase distinction was conditional in R1; d2 chooses a separate database explicitly. Native multi-ID/root and universal group-length behavior remain unproven; d2 fails closed on ambiguous receipts and uses a documented local group limit.

## 1. The design reuses Journal.snapshot/holds/states/usage_cache as 'read-only' points, but at source076 the Journal constructor creates a fresh journal when the file is missing (_create), opens mode=rw and switches to WAL, and snapshot()/usage_cache() run inside self.transaction(), which is BEGIN IMMEDIATE (a write lock).

**Disposition: addressed in proposed design.** Replace Journal construction and transaction helpers with fixed owner-side bounded deferred SELECTs through the existing initialized owner; absent owner is unavailable. Runtime schema untouched.

Revised contract: §§3–4; acceptance4.

Actual R1 failure scenario: The producer opens the wsd journal after it was lost or moved, and Journal(path) quietly makes an empty schema-3 journal. That contradicts §7's 'no empty replacement'. Under normal running every 30s refresh takes a RESERVED write lock that competes with the scheduler (5s BUSY_TIMEOUT), so the 'assert no source mutation' adapter test can't pass for the named reuse points.

## 2. The 5-second inclusive source-read budget and 100-row page bound can't be enforced through the named reuse points. Beads.ready/show call btq Queue.bd via subprocess.run(timeout=20) with '--limit 0', and the wsd factory builds Queue with make_state=True (mkdir) and wsd's write-capable Dolt credentials.

**Disposition: addressed in proposed design.** Do not construct Queue/use factory or writer credentials; new fixed read-role adapter caps bytes/items and kills/joins its process within aggregate5s; no limit0 reads.

Revised contract: §§3–4; acceptance4.

Actual R1 failure scenario: With a slow Dolt server, /queue refresh exceeds 5s while the subprocess thread keeps running up to 20s. Either the 4-worker cap is breached by orphaned bd processes, or refresh capacity is wedged. A large ready list is fully loaded into memory before any 100-row bound. Every 'read' also creates btq state directories and carries the wsd agent's write password. btq offers make_state=False for readers ('mine' and btq-supervise), but the design doesn't require it or a read-only DB identity.

## 3. BeadStatus reuses Beads.show(ws, bead), which runs 'bd show <id>' against the shared database with no workstream filter. The design never requires checking the returned bead's ws:/agent: routing labels against the WorkstreamBinding before projecting.

**Disposition: addressed in proposed design.** Require exactly one valid permitted ws routing label and fixed repository/record binding before native bead projection; public out-of-scope equals not-found.

Revised contract: §4.2; acceptance2.

Actual R1 failure scenario: In workstream group A, an allowlisted requester sends '/status <bead-from-B>'. bd show succeeds because labels are not filtered, and an implementation following the §4.2 table renders B's state, branch and launches. The §6 isolation rule is broken, and not-found versus out-of-scope becomes distinguishable.

## 4. The early-sequencing exception (implement 6B before complete Plan5) leaves ApprovalReadAdapter and the BeadStatus decision summaries with no native record contract: the approval bead's status/title/created_at fields and 'open' semantics are owned by the unfinished Plan5/e7knb writer.

**Disposition: addressed in proposed design.** Define the finite native approval display row as early source; unknown questions/Plan5 decisions remain unknown; future writer is not an implementation dependency.

Revised contract: §4.2; acceptance5.

Actual R1 failure scenario: The implementer must either invent an approval record schema (forbidden: 'not silent schema invention') or make /approvals and decisions permanently Unavailable. Plan5 then lands a different field set, and 6B silently misparses approvals or shows a stale 'open' state, with no listed reconciliation gate except Plan6A.

## 5. Queue labels Beads.ready output as 'the owner's pickup order', but the scheduler (scheduler.py) resumes parked beads first and skips beads with open ops, unclaimable/session-pinned beads and deadline/account gates. Also Queue.ready returns [] whenever the parent 'paused' flag exists.

**Disposition: addressed in proposed design.** Withdraw false actual pickup-order claim. New pure owner candidate observation includes resumable-first/new-ready, pause/gates and explicit not-a-launch-prediction wording; insufficient proof unavailable.

Revised contract: §§3,4.2; acceptance5.

Actual R1 failure scenario: For a paused workstream, /queue shows an empty, 'complete' ready list, which is the invented-empty result the design forbids. For a running workstream it shows bead X as next while the scheduler actually resumes parked bead Y or skips X as unclaimable. A separate paused() read raced against ready() can give 'not paused + empty'.

## 6. Where the new typed 6B records and the last_events index live is unspecified. wsd's _check refuses any journal whose sqlite_master differs from EXPECTED_SCHEMA ('schema differs'), and events has no (ws,bead) index.

**Disposition: addressed in proposed design.** Use separate6Bschema/DB; no new runtime indexes. Last events scan at most100descending rows and mark missing earlier matches incomplete.

Revised contract: §§4,4.2,7; acceptance4,11.

Actual R1 failure scenario: If replay/reply tables or an events(ws,bead,seq) index are migrated into the wsd journal, the deployed wsd release refuses to open it, and §10's rollback ('stops only the new consumer… retains original journal') leaves wsd unable to start. Without the index, last_events(ws,bead,5) is a full scan of a never-pruned events table, the same unbounded scan the design rejects for events_since.

## 7. Bounds conflict: ReadScope allows 1–100 workstream bindings, but sources is capped at 1–100 entries with unique (origin, workstream), and native-beads plus wsd-journal are per-workstream origins.

**Disposition: addressed in proposed design.** Raise finite observation cap to1024 with <=8perworkstream and256KiBframe limit, preserving100bindings; oversize unavailable.

Revised contract: §4.1; acceptance6.

Actual R1 failure scenario: A control surface with 50+ registered workstreams needs at least 2N+1 > 100 SourceObservations for Workstreams/Status. Every such envelope fails validation, so /workstreams and /status are permanently Rejected or Unavailable for a valid configuration.

## 8. One shown/total/completeness triple per (origin, workstream) can't represent views that include several enumerated collections from the same origin (ready queue, parked, held, deferred, approvals).

**Disposition: addressed in proposed design.** Add closed collection identity; counts/completeness/times independent per origin/ws/collection.

Revised contract: §4.1; acceptance6.

Actual R1 failure scenario: In a workstream /status, the ready list is complete (12/12) but approvals exceed 100 rows. The single native-beads observation must either claim complete, which is false for approvals, or mark the whole thing incomplete with a total that applies to neither collection.

## 9. Persisted snapshots 'after restart … can still be displayed', yet the cache key includes producer run/boot identity and generation, and a changed producer invalidates adoption (Unavailable(producer-restarted)).

**Disposition: addressed in proposed design.** Stable logical key plus explicit historical stale old-run entries; separate active producer binding and fresh authorization; no old-run authority.

Revised contract: §8; acceptance7.

Actual R1 failure scenario: wsd restarts while Beads is unreachable. The new producer run_id/boot_id means no persisted key matches, so /status returns Unavailable instead of the ADR §6.3 'answer from cache with ⚠️ beads unreachable' behaviour. Alternatively, an implementer looks the cache up ignoring producer identity, which defeats the producer binding.

## 10. Putting generation in the cache key makes 'one active refresh per cache key' single-flight per generation rather than per scope/selector, and a reader can't find the current entry without already knowing the generation.

**Disposition: addressed in proposed design.** Remove generation/run from cache/singleflight identity; generation domains stored and validated in entries.

Revised contract: §8; acceptance7.

Actual R1 failure scenario: Two commands for the same scope/selector arrive while different generations are cached or refreshing, and each starts its own refresh, defeating coalescing and the 4-worker bound. Generation belongs on the entry as a monotonic adoption attribute, not in the key.

## 11. Every ordinary text/reaction is 'durably classified', untrusted input leaves audit rows, and replay tombstones are never age-evicted, with no per-group/per-sender storage quota. Meanwhile disk pressure 'refuses new reservations'.

**Disposition: addressed in proposed design.** No per-event stranger/plaintext rows; capped fixed counters.64MiBnoncache quota, boundedbusy/recoveryslots and no eviction of replay/send evidence.

Revised contract: §7; acceptance8.

Actual R1 failure scenario: A non-allowlisted group member, or ordinary operator chat in the control group, keeps posting. The 6B journal grows without bound until disk pressure refuses all new reservations, so authorised /status queries stop being answered, with no recovery path until a future retention design exists.

## 12. 6B records a durable trust hold that it 'never clears', but it defines no interface through which a recovery owner's action (registration epoch bump, new baseline) releases it. 6B also doesn't raise the control-group alert ADR §3.4 requires on a detected membership change.

**Disposition: addressed in proposed design.** Name config/recovery script and owner, exact authenticated recovery/epoch binding, separately trusted control alert and durable pending doctor status; deployment requires implemented procedure.

Revised contract: §5; acceptance3.

Actual R1 failure scenario: The operator adds a second device and a GroupStateChanged arrives. Reads are suppressed permanently with nobody alerted (6B posts only read replies). Later /trust-group or admin recovery writes its own store, and the 6B-private hold never observes it, so the hold state diverges from the approval-path owner.

## 13. §5 says a stale (>30s) group observation 'withholds sending until success'. §8 says that when verification is unavailable the query is recorded suppressed (terminal) and recovers only on a new query, and the §7 state machine has no 'awaiting authority' state.

**Disposition: addressed in proposed design.** waiting-auth is resumable inside bounded admission; stale observation triggers coalesced check. Definite revocation suppresses, timeout auth-unavailable; idle firstquery can complete.

Revised contract: §§5,7; acceptance3.

Actual R1 failure scenario: In a group idle for over 30s (the common case), the first /status finds a stale observation. One reading holds the reply in a state that isn't defined. The other suppresses it terminally, so the operator gets silence and must resend every first command after idle.

## 14. The durable query states (accepted, preparing, ready, sending, delivered, suppressed, unavailable, unsupported, uncertain) don't cover busy replies, Rejected/not-found replies, fixed usage replies, over-quota silently dropped requests, or partially delivered multipart responses.

**Disposition: addressed in proposed design.** Specify state machine for reservation/auth/projection/fixed semantic notices/parts/delivery/partial/uncertainty/quota and recovery.

Revised contract: §7; acceptance9.

Actual R1 failure scenario: Part 1 is delivered and then the sender is revoked, so remaining parts are suppressed. The row must be 'suppressed' (losing the delivered fact) or 'delivered' (false). A busy reservation or not-found reply has no terminal state, so restart recovery can rebuild and send it again.

## 15. A 'ready' response may be resumed from exact stored rendered bytes, yet §8 requires the final warning to reflect send-time age and says persisted data is stale after restart. Exact bytes can't carry a fresh age/stale warning.

**Disposition: addressed in proposed design.** Bind snapshot/rendered timestamps/revision; rebuild delayed unsent ready response,30s multipart bound, absolute observations, no timestamp rewrite.

Revised contract: §7; acceptance10.

Actual R1 failure scenario: wsd crashes with a ready response rendered as fresh (age 3s) and restarts 10 minutes later. Resuming the exact stored bytes sends content labelled fresh with no stale warning, which manufactures freshness.

## 16. send_final returns FinalSent.message_ids_hex as a list, but the design threads later parts under 'the genuine part1 receipt' without saying which ID to use when one send returns several.

**Disposition: addressed in proposed design.** Require exactly one wellformed nativeID per part for continuation; multi-ID results retained but uncertain, never arbitrary root or resend.

Revised contract: §7; acceptance9.

Actual R1 failure scenario: wn-agent returns two message IDs for part 1, for example a native split or relay copies. The implementation picks an arbitrary one, so later parts thread under the wrong or a non-root message and ReadReplyRef's dedup/threading becomes inconsistent across restarts.

## 17. The mapping from the envelope's per-source availability and observed_at to 6A's single view-level availability (reachable/cached-unreachable), observed_at and cache_state (fresh/refresh-required, with no 'refreshing') is unspecified. 6A's fixed warning text names only beads.

**Disposition: addressed in proposed design.** Define partial/overall availability and6Acompatibility mapping, independent times/counts and source-specific warnings; unsupported partial uses fixed unavailable.

Revised contract: §4.3; acceptance6.

Actual R1 failure scenario: Beads is fresh, but the wsd-journal or account-usage source is unreachable. The view must either show 'reachable/fresh', hiding stale components, or show '⚠️ beads unreachable', which is wrong about the cause. The view-level observed_at choice (max) can retimestamp older components.

## 18. Commands never wait for a refresh, and 'No snapshot returns fixed unavailable', so every cold or evicted cache key answers Unavailable even when Beads is healthy.

**Disposition: addressed in proposed design.** Cold query waits for bounded refresh up to5s; warm path retains subsecond local reservation target; distinguish source and network latency.

Revised contract: §8; acceptance7.

Actual R1 failure scenario: After startup, or after LRU eviction under 16 MiB pressure, the first /queue or /status <bead> for each scope/selector always returns 'unavailable', and the operator must ask again. ADR §6.3 says commands are answered from the cache refreshed when more than 30s old, not unavailable when it is cold.

## 19. 6A's RenderResult NeedsDetails (more than 20 parts) and Withheld (unsafe content) outcomes have no defined 6B reply or terminal state, and the selector set offers no details command.

**Disposition: addressed in proposed design.** NeedsDetails maps fixed narrow-query notice; Withheld maps safe unavailable; no raw content fallback.

Revised contract: §4.3; acceptance10.

Actual R1 failure scenario: A control /queue partitioned over many workstreams exceeds 20 parts and 6A returns NeedsDetails. 6B has no state or fixed reply for it, so the query either hangs in 'preparing' through restart recovery or is silently dropped.

## 20. The manifest pins wsd/beads.py but not wsd/scheduler.py (the actual pickup-order owner) or wsd/btq.py and the external btq Queue source, which decide what ready(), show() and the pause flag actually do.

**Disposition: addressed in proposed design.** Add exact scheduler/wsd.btq/config.layers and external queue source bytes/commit; source limitations documented in§3.

Revised contract: Input manifest; acceptance4,5.

Actual R1 failure scenario: Reviewers and implementers can't check the 'owner's pickup order' and 'read-only reuse' claims against pinned text, and a btq change to Queue.ready filtering or side effects would silently change 6B semantics.

## 21. Registration requires 'exact route… one group has one configured meaning', but it doesn't require refusing a wsd registration whose account/group equals admind's own configured group.

**Disposition: addressed in proposed design.** Reject normalized read-route aliases of configured admind own account/group before startup.

Revised contract: §5; acceptance1.

Actual R1 failure scenario: The operator registers admind's group as a wsd control surface. Both admind (sending text to its agent) and 6B handle '/status'. 6B replies inside a group whose latch/membership semantics are admind's, and the two daemons keep separate replay stores for the same events.

## 22. Authenticated mutation commands such as '/approve' or '/pause' are 'durably classified ignored/unsupported', and a fixed reply is only optional ('may'), so an operator using ADR §6.3 commands in a wsd group can get silence.

**Disposition: addressed in proposed design.** Mandatory fixed unsupported/no-action response for authenticated mutation forms, within rate/auth bounds.

Revised contract: §6; acceptance9.

Actual R1 failure scenario: During the early read-only slice, the operator replies '/approve' to an item in a wsd group and gets no response. They may assume it was accepted. A mandatory fixed 'not supported here; nothing was done' reply would remove the ambiguity without acknowledging any action.

## 23. Registration (route, kind, workstreams, epoch, trusted baseline) is said to be 'loaded from supported host configuration', but source076 config has no such keys (config/layers.py WORKSTREAM_KEYS/HOST_ONLY_KEYS), and the design excludes any registration mutation API. That implies hand-edited config, against ~/.claude/CLAUDE.md: 'Liam can't copy/paste from the terminal and won't hand-edit files or settings. If he must run something: write a script'.

**Disposition: addressed in proposed design.** Specify proposed version1config and supported validate/plan/apply script consuming existing approval; no personalCLAUDErule or manual config authority invented.

Revised contract: §5; acceptance3.

Actual R1 failure scenario: Deployment and every trust-recovery baseline change require Liam to hand-edit a new, still-unspecified host config schema. That isn't listed under §3's 'Missing work', and it conflicts with the stated working rule.

## 24. 'Strings are UTF-8, bounded before allocation' can't hold over the reused control.py, which reads and msgspec-decodes whole frames of up to 1 MiB before any field bound. Separately, the 16 KiB UTF-8 text limit is redundant with 4096 characters (4 bytes × 4096 = 16384).

**Disposition: addressed in proposed design.** Distinguish native1MiBpredecode frame bound from postdecode4096codepoint check;16KiBis derived not independent; projection256KiBpredecode bound explicit.

Revised contract: §§4.1,5; acceptance1.

Actual R1 failure scenario: A 1 MiB inbound_message text is fully allocated and decoded before 6B's 4096-character check, so the claimed pre-allocation bound is false. The duplicate byte bound adds a check that can never fail.

## 25. The replay key requires a native event ID, but ReactionAdded.event_id_hex is optional and GroupStateChanged has none, so the 'durably classified ignored' rows for such events have no defined key.

**Disposition: addressed in proposed design.** Only valid-IDquery messages reserve replay rows; reactions ignored, ID-less groupstate latches epoch hold idempotently; no synthetic event identity.

Revised contract: §7; acceptance8.

Actual R1 failure scenario: A reaction without event_id_hex arrives. The implementation either crashes on a null key, writes non-unique rows (unbounded duplicates), or silently drops it, and the design and B01 tests don't say which.

## 26. Untrusted-input audit keeps 'correlation hashes' rather than identity, but a plain hash of a public Nostr key is reversible by hashing known public keys.

**Disposition: addressed in proposed design.** Drop stranger identity correlation by default; optional private rotating keyedHMAC only within bounded diagnostic window; no reversible public-key hash.

Revised contract: §7; acceptance8.

Actual R1 failure scenario: An audit reader hashes candidate npubs from public relays and recovers which stranger posted, which defeats 'not full stranger… identity'. A keyed (local secret) hash or a truncated prefix like admind's sender_prefix is needed.

## 27. The AccountIdHex rule ('PubkeyHex only where the configured native account contract identifies it… otherwise bounded even-hex') is left undecided, although source076 already treats Sender.account_id_hex as the 32-byte pubkey (admind matches npub_to_hex against it).

**Disposition: addressed in proposed design.** Fix account and sender to32bytepublickey hex under pinned native contract; no opaque short account fallback.

Revised contract: §5; acceptance1.

Actual R1 failure scenario: An implementer picks the looser 2–512-hex validator for the local account and native sender identifiers. Malformed or short account IDs pass syntax checks and the strict 64-hex sender/allowlist binding is weakened.

## Remaining gates and interpretation

No implementation or tests were performed. Final approved6A compatibility is a later exact validated input; runtime availability/count/order contracts must be proven in named offline adapter tests. New supported readconfig/recovery and readrole are proposed implementation, not current installations. Early sequencing and read-disclosure suspension/recovery choices are part of this proposal for genuine Marmot design approval. Separate deployment approval remains required. No Plan5writer,6Cgeneraloutbox,6Dmutation integration or operational acceptance is claimed.
