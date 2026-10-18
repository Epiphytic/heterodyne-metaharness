**Verdict: REVISE.** This was a read-only review of 597a1d4..8d8b228 against approved design aa860fe7. I ran no tests, probes or edits, and did no verify pass, so findings are unverified.

Ranking:
1. **Highest:** findings 1–3. Each can leave an attempt the design says is eligible permanently or practically unretirable, so the ask-slot lockout this feature exists to clear stays in place.
2. **Next:** finding 4, a weakness in the evidence chain. Only the latest adapter identity is checked.
3. **Lower:** findings 5–8, which are smaller design or receipt deviations.
4. **Cleanup:** findings 9–10.

I found nothing that allows retirement without the authenticated, current-authority check, or that writes a native decision.

10 findings, most severe first:

1. `src/heterodyne/admind/retirement.py:546`: publish_notice skips republishing whenever a mapping row with the same proof sha256 exists, without checking that the bound outbox row was actually sent with a message_id, so a notice that fails delivery permanently prevents retirement.

   Failure scenario: A suspended hold (e.g. maintenance-held:changed-card) gets its notice queued, but the outbox marks it 'failed' (mark_failed) or 'sent' with message_id=None. Suspended holds never re-invoke the child, so the proof sha never changes. Every later `!asks` refresh hits `row[0] == proof.sha256` and returns. select() needs o.status='sent' AND o.message_id=target, so no reply can ever select the proof and the attempt keeps its slot forever.

2. `src/heterodyne/admind/retirement.py:548`: For retryable pending-maintenance overlays, each ~30s retry re-invokes the child and rewrites the proof (invocations is in the hashed body), but hold_maintenance publishes with refresh=False, so the delivered token goes stale within one retry interval.

   Failure scenario: The fence is held and the attempt retries every MAINTENANCE_RETRY_SECONDS with an exact HOLD each time. The operator runs `!asks` (new notice queued), waits for delivery and replies on a phone. If the reply lands after the next retry, select() finds no matching sha and returns 'Retirement refused'. The design says retryable overlays are eligible, but in practice they need a sub-30-second reply; the test test_no_per_retry_proof_or_notice_growth confirms the original token stops selecting after a retry.

3. `src/heterodyne/admind/retirement.py:617`: Retirement requires the current approve-bead file SHA256 to equal the identity captured at the last HOLD, but suspended holds never invoke the child again, so any legitimate adapter upgrade permanently freezes every suspended attempt.

   Failure scenario: Deploying the planned exjsy/fence adapter repair (or any approve-bead update) changes the file bytes. Every existing maintenance-held:* attempt then permanently gets 'native adapter changed; original retained', with no path to refresh the proof. All 20 slots can stay starved by attempts that do have a full not-issued proof chain.

4. `src/heterodyne/admind/retirement.py:358`: record_hold overwrites adapter_identity on each invocation without requiring it to match the previous proof's identity, and 'consecutive' is always set to count, so the chain does not record that all invocations ran under one adapter.

   Failure scenario: Invocation 1 runs under adapter A and returns HOLD. The approve-bead file is swapped to B, and invocation 2 under B returns HOLD. The proof now holds only B's identity with consecutive=2, and retire() checks only the current identity against B. Invocation 1's evidence under a different adapter is silently merged and lost, contrary to the design's 'captured coordinator/adapter identity' per chain.

5. `src/heterodyne/admind/store.py:1181`: issue_decision increments the never-reset invocations counter before the child is spawned, and a spawn failure with launched=False is not recorded as not-spawned, so one transient fork/exec failure permanently disables retirement for that attempt.

   Failure scenario: A maintenance retry hits EAGAIN or ENOMEM in reap.spawn, which raises BtqError('unavailable') with launched=False. The stop is held as read-unavailable and invocations=n+1 while the proof stays at n. Every later exact HOLD fails `previous.invocations != count-1` in record_hold, so a provably not-spawned generation strands the attempt. The design allows atomic not-spawned marking for exactly this case.

6. `src/heterodyne/admind/retirement.py:585`: Receipt lookup is keyed on (token, notice_id), so 'already retired' is only reported for the same notice and token, and the same-command branch returns with no reply.

   Failure scenario: Two cases. (a) The attempt is retired via notice N2 (after an `!asks` refresh), and another operator replies to the older N1 with its token. receipt() misses and select() fails, so they get 'Retirement refused: reply to the current eligible held notice', not the design's 'reports the existing retirement'. (b) A direct re-invocation with the same command_id returns silently, without the 'returns its existing receipt' behavior the design requires.

7. `src/heterodyne/admind/retirement.py:552`: The notice, unavailable-notice and `!asks` texts embed the 64-hex Marmot attempt message_id, which redact()'s _HEX_RUN (>=64 hex) replaces with '<redacted hex key>', so the held attempt identity the design requires to be shown never reaches operators.

   Failure scenario: The notice renders as 'Ask k7m2: held approve attempt <redacted hex key>. Reply directly...'. The same happens in the 'retirement unavailable' notice (line 538) and the daemon `!asks` line (daemon.py:1163, via finish->reply->redact). Operators cannot tell which held attempt a token or diagnostic refers to when one ask has several historical attempts.

8. `src/heterodyne/admind/retirement.py:509`: The receipt labels every blocked retirement reason 'native-conflict', even when blocked came from explicit not-open or not-approval readback, and it records a synthesized 'ref' ('marmot:id:'+command) rather than the inbound's actual ref.

   Failure scenario: The native card is closed but undecided, or kind_approval=false. settle returns 'blocked' or kind_approval is false, so blocked=True and the immutable receipt says reason='native-conflict'. The design requires distinct provenance for the conflict, not-open and not-approval branches, and the permanent audit record misstates why the local attempt was blocked.

9. `src/heterodyne/admind/approvals.py:176`: ApproveBead.identity() synchronously reads and SHA256-hashes up to 8 MiB on the asyncio event loop, twice per decide() and twice per retire.

   Failure scenario: With up to 128 retryable overlays each retrying every 30s, each decide call does two blocking file reads and hashes, and retire adds two more around an awaited read. A large adapter or slow filesystem stalls the daemon loop (membership observation, outbox sends) for every call. Hashing via asyncio.to_thread, or caching keyed on (st_dev, st_ino, st_mtime_ns, st_size), would avoid this.

10. `src/heterodyne/admind/retirement.py:570`: The new code duplicates existing helpers and adds redundant checks: raw `store.db.execute('SELECT reply_to FROM inbound...')` outside the store lock instead of Store.reply_target, lowercase-hex validation written out three times plus the parse regex, the token re-checked after commands.parse already validated it, a duplicate `type(value) is not dict` test, and a 'consecutive' field that always equals 'invocations'.

    Failure scenario: Four copies of the same hex validator and a bypassed @_locked accessor drift independently. The redundant 'consecutive' field implies chain information that is never independently checked, which hides the gap in finding 4 from future readers.
