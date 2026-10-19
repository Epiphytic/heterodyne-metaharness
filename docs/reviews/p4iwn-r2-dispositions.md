# P4 actual second HIGH: author dispositions

Actual review reported ten findings and explicitly was not approval. These are author repairs and retained limits, awaiting the next actual source review. All prior reports/dispositions remain unchanged.

## 1. REPAIRED OFFLINE; PHYSICAL RESERVE OPEN

> 1. `src/heterodyne/admind/gateway_custody.py:914`: Every agreeing settle, every 'held' command on a revoked proposal, and every conflict diagnostic is now stored as a full receipt in the 32KiB bundle, with no room set aside for permits. The ADR says that 32KiB must hold the descriptor, the decision and all three permits/certificates, so a consented proposal can end up unable to get its first permit or a renewal.
> 
>    Failure scenario: Each command receipt is about 800 bytes (event dict, 64-hex ids, policy hash twice, event_id key). After the first settle, operators send about 30 more exact `!custody settle T` replies in the thread, for example retrying because they saw no response. Each one is stored with outcome 'consented'. The bundle nears 32KiB, so `_issue_permit`'s `capacity(updated, ...)` fails with 'bundle-capacity' for generation 1, or later for renewal generations 2 and 3. The consent stays valid but can never be used, and there is no reclamation path. Before this repair an agreeing settle revoked; now it fills up storage.

Routine command/conflict diagnostics are bounded to 8 KiB. New receipt admission preserves 8 KiB of each 32 KiB bundle for all three permits/certificates. Aggregate payload remains 128 MiB. Saturation refuses without erasing receipts or existing consent. This is payload headroom, not the absent physical 256 KiB WAL/index reserve.

Regression: test_command_storm_cannot_consume_three_permit_reserve.

## 2. REPAIRED OFFLINE

> 2. `src/heterodyne/admind/gateway_custody.py:792`: message() calls _block_conflict, which revokes and writes conflict diagnostics, before it calls self.authority(...) and before it checks the thread mapping. A conflicting replay therefore changes proposal state without current-principal, policy or thread authentication, against the ADR rule that 'New effects always require current authorization'.
> 
>    Failure scenario: Operator A's settle (mid M, reply_to = notice id) is retained and the state is consented. The transport later redelivers M with different metadata, for example reply_to=None after a resubscribe, or from a sender who is no longer current (on_event only checks a cached operators set, and the Service contract checks nothing). `previous['event'] != event` triggers _block_conflict, and the state becomes 'revoked' with no authority call and no notice-thread check, so a valid consent is lost. The same path also writes diagnostics while admind is latched, because authorised() is never consulted.

Conflicting retained native IDs now pass current authorization and current thread binding before the shared serialized mutation path. Exact immutable receipt readback remains distinct from a new conflicting effect; no caller metadata can revoke without current authority.

Regression: conflicting replay removed principal and wrong-thread regressions.

## 3. REPAIRED OFFLINE; NOTICE RECOVERY OPEN

> 3. `src/heterodyne/admind/gateway_custody.py:713`: Duplicate registration only refuses notice_status 'failed' or 'missing'. A row that is 'sent' with a NULL message_id, or held as quarantined/suppressed/delayed, returns 'registered', even though the repaired undelivered count treats exactly those rows as undelivered and _retain_command can never map a thread to them.
> 
>    Failure scenario: Native send succeeds but returns no message id, so mark_sent(seq, None) sets status='sent' with message_id NULL. A re-register returns {'result':'registered'} and status shows notice_status='sent'. Every `!custody settle` fails with 'thread-unmapped' because deliveries[0][0] is None. The operator gets the same misleading success that finding 3 was meant to remove, while the slot counts against the 256-undelivered limit.

Duplicate registration refuses sent-with-NULL-id and delayed/quarantined/suppressed/failed/missing states. Existing pending registration remains idempotent. No resend, fake delivery or slot reclamation; retained native delivery recovery remains unavailable.

Regression: duplicate notice-state matrix.

## 4. REPAIRED OFFLINE

> 4. `src/heterodyne/admind/gateway_custody.py:633`: Retrying an interrupted renewal with the same request (same nonconsumption_sha256) can never repair it. After the crash, records[-1] is the retained successor, so permit_certificate looks for nonconsumption-{N+1}. That file does not exist, which surfaces as OSError 'unavailable', or the call raises 'generation-cap' when N+1 is 3.
> 
>    Failure scenario: Renewal 1 to 2 commits generation 2 to SQLite, then tombstone publish hits ENOSPC. The operator repeats `admind gateway-custody permit K --nonconsumption-sha256 H`. previous is now generation 2, private_read of `K.nonconsumption-2.json` raises FileNotFoundError, and the daemon replies 'custody source or store unavailable'. Only the undocumented form without the flag repairs. For a retained generation 3, the retry gets 'generation-cap', which looks like a final refusal.

An exact repeated renewal request identifies the retained generation by its retained certificate hash. Interrupted publication validates the original queue pair against that generation and repairs identical bytes/UUID/window. It does not demand a next-generation certificate or grant another generation.

Regression: same-request interrupted renewal repair.

## 5. REPAIRED OFFLINE

> 5. `src/heterodyne/admind/gateway_custody.py:520`: Every readback of a retained permit at generation 2 or higher, not only crash repair, re-runs the full predecessor certificate check through renewal(). That check refuses when boottime + 30s exceeds the proposal expiry, and it needs the queue's predecessor certificate files and no consumed/issuing/receipt files. Generation-1 readback has none of these conditions.
> 
>    Failure scenario: Generation 2 is fully published. In the last 30s before proposal expiry, or after the queue has rotated its nonconsumption-1/superseded-1 files, a status-style readback via `permit K` raises 'proposal-expiring' or OSError. The same readback works at generation 1. 'Readback never grants a new window' becomes 'readback is refused' only for renewed permits.

Fully published exact renewed bytes plus predecessor tombstone support immutable readback without rerunning the new-window/certificate check. Current original source/boot/expiry/authority remain required. Partial repair still requires original authentic queue certificate bytes. No new effect or initiation window is inferred from readback.

Regression: published renewal readback at original expiry boundary.

## 6. REPAIRED OFFLINE; CALLBACK LATENCY OPEN

> 6. `src/heterodyne/admind/gateway_custody.py:466`: issue_permit now holds the shared publication_lock for the whole call. That includes self.publication(), self.source() and two self.authority() callbacks, and each daemon authority call blocks on the event loop for up to 5s (run_coroutine_threadsafe, then refresh_authority, then the check_group network call). The lock is held through the source, publication and authority callbacks, while the in-code comment says 'Policy/source callbacks remain outside Store locks'.
> 
>    Failure scenario: In the Service contract (independent views share the lock), a native decline handled on another thread waits in `_retain_command`'s `with self.publication_lock` while issue_permit waits up to about 10s on two slow group_info calls plus a source read. The authenticated revocation is delayed past permit commit and publication instead of racing it under CAS. The nested `with self.publication_lock` at line 540 is now redundant.

External source/publication/policy callbacks run outside the publication mutex and Store lock. Final exact row CAS and publication remain serialized against authenticated revocation; a revoke during slow authority lookup wins and prevents issuance. Independent external callback latency remains bounded by its owning adapter, not hidden as zero cost.

Regression: blocking authority callback versus concurrent authenticated revoke.

## 7. REPAIRED OFFLINE; DURABLE INTAKE OPEN

> 7. `src/heterodyne/admind/daemon.py:2623`: The new on_message fallback calls self.custody_work.put_nowait(ev) without handling asyncio.QueueFull, unlike on_event. A full queue raises inside worker_loop and is logged only as a generic handler failure.
> 
>    Failure scenario: custody_work holds 256 items while custody_loop is blocked, and a custody command reaches on_message by the direct or fallback path. QueueFull propagates, and worker_loop audits {'handler','failed','QueueFull'}. The intended 'gateway-custody refused command-capacity' audit never appears, so the dropped native command is invisible to custody audit.

Direct on_message QueueFull emits fixed gateway-custody/refused/command-capacity audit, consistent with on_event. No ACK, consent or durable native redelivery is claimed.

Regression: full direct-fallback queue audit.

## 8. REPAIRED OFFLINE; LEGACY AVAILABILITY LIMIT

> 8. `src/heterodyne/admind/gateway_custody.py:731`: The 32 notices/hour limit compares wall-clock datetime.now(UTC).isoformat() strings stored in each bundle, so a wall-clock step can bypass it or block it.
> 
>    Failure scenario: The host clock jumps forward 2h, then NTP corrects it. Registrations made during the jump are stored with future timestamps, so after the correction they count as 'recent' for about 2h and wrongly block registration. Equally, after the forward step, older rows fall out of the one-hour window, so another 32 registrations are allowed early. The daemon elsewhere uses BOOTTIME and monotonic clocks for this kind of bound.

Notice rate accounting uses source-owned boot ID and decimal BOOTTIME stamps. Wall-clock creation time is presentation only. Missing legacy stamp, different boot or regressed source clock counts conservatively recent rather than granting capacity. This may hold old rows indefinitely pending separately approved export/recovery; no history deletion.

Regression: source clock hour boundary versus mutated wall timestamp.

## 9. PARTIAL REPAIR; OPEN UNWAIVED

> 9. `src/heterodyne/admind/daemon.py:785`: Each custody effect makes 2-3 authority callbacks, and each one calls refresh_authority (policy file reads), check_group (a network group_info call plus a store write of expected_members and flush_membership_notice) and recovery_authority. On top of that, on_gateway_custody already ran refresh_authority and check_group, and a readback at generation 2 or higher repeats three fsync publish cycles.
> 
>    Failure scenario: Cost: one permit request means 3 group_info round trips, 3 policy reloads and 3 expected_members writes, plus side effects (membership notice flush) triggered by what should be a read-only policy query. One permit readback means three publish(temp, fsync, link, dir fsync) cycles even when all files already match. Cheaper: take one policy snapshot per request, compare it under the writer mutex, and skip publish when private_read already returns identical bytes.

Exact matching published files and unchanged retained bundle skip redundant publication/fsync and UPDATE. Independent current-authority callbacks remain repeated to preserve final authorization/CAS and revocation safety. Network/policy snapshot consolidation and membership-side-effect costs remain OPEN; no fabricated read-only production adapter or performance clearance.

Regression: exact completed readback refuses attempted republish.

## 10. REPAIRED DUPLICATE PATH; QUALITY OPEN

> 10. `src/heterodyne/admind/gateway_custody.py:826`: The repair adds parallel special cases instead of one mechanism. The guarded revoke UPDATE is written out twice (_block_conflict and _retain_command), the conflict-append logic is duplicated (_block_conflict and _record_command), and the 'terminal' state, which no product code path ever sets, is special-cased in two methods.
> 
>     Failure scenario: Cost: a later fix to revocation guards or conflict bounding has to be applied in several copies and can drift. The terminal immutability is only exercised by tests that write state='terminal' directly. A single `_revoke_unissued(key)` helper plus one `_retain_conflict(bundle, event)` used by both paths, with the fast-path conflict routed through `_retain_command` after authentication, would remove the copies and close finding 2 as well.

Removed _block_conflict and its duplicate revoke/conflict path. Conflicts share authenticated _retain_command/_record_command. Terminal immutability is preserved as a defensive stored-state contract; no product terminal lifecycle implementation is claimed. Complexity and churn debt remain OPEN UNWAIVED.

Regression: existing terminal immutability and new conflicting replay tests.

